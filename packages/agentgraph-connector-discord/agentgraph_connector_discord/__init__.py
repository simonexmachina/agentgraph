"""Discord connector (bot token auth)."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx

from agentgraph.connectors.base import (
    BaseConnector,
    ConnectorAccount,
    EdgeRecord,
    EntityBatch,
    EntityRecord,
    EntityTypeDefinition,
    FetchPolicy,
    PersonRecord,
    ResourceType,
    SourceReference,
    get_known_channel_syncs,
)
from agentgraph.graph.upsert import upsert_batch
from agentgraph_connector_discord.auth import list_discord_accounts, load_discord_creds

logger = logging.getLogger(__name__)

DISCORD_API = "https://discord.com/api/v10"
_STALE_AFTER = 5 * 60  # 5 minutes
_MAX_RETRIES = 3
_DISCORD_CHANNEL_URL_RE = re.compile(
    r"https://discord\.com/channels/(?P<guild_id>\d+)/(?P<channel_id>\d+)"
)
_DISCORD_DM_URL_RE = re.compile(
    r"https://discord\.com/channels/@me/(?P<channel_id>\d+)"
)

# Module-level user cache so repeated syncs don't re-fetch the same users
_user_cache: dict[str, PersonRecord] = {}


def _get_headers(account_id: str | None = None) -> dict[str, str]:
    creds = load_discord_creds(account_id)
    return {"Authorization": f"Bot {creds.bot_token}"}


async def _api_get(client: httpx.AsyncClient, path: str, account_id: str | None = None, **params: Any) -> Any:
    for _attempt in range(_MAX_RETRIES):
        resp = await client.get(
            f"{DISCORD_API}{path}",
            headers=_get_headers(account_id),
            params=params,
            timeout=30,
        )
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "1"))
            is_global = resp.headers.get("X-RateLimit-Global") == "true"
            logger.warning(
                "Discord rate limited%s on %s — sleeping %.1fs",
                " (global)" if is_global else "",
                path,
                retry_after,
            )
            await asyncio.sleep(retry_after)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Discord API {path} still rate-limited after {_MAX_RETRIES} retries")


def _snowflake_to_dt(snowflake: str) -> datetime:
    """Convert a Discord snowflake ID to a UTC datetime."""
    ts_ms = (int(snowflake) >> 22) + 1420070400000
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC)


def _parse_discord_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _snowflake_after(dt: datetime) -> str:
    """Return the smallest snowflake ID that is strictly after dt."""
    ts_ms = int(dt.timestamp() * 1000) - 1420070400000
    return str(max(ts_ms, 0) << 22)


async def refresh_attachment_urls(
    channel_id: str,
    message_id: str,
    stored_json: str,
) -> str:
    """Return attachments JSON with fresh CDN URLs from Discord.

    Uses POST /attachments/refresh-urls — the purpose-built Discord
    endpoint for renewing expiring signed CDN tokens. Discord only issues
    new tokens once the current ones have expired; while still valid the
    same URL is returned unchanged, which is correct behaviour.

    Falls back to stored_json on missing credentials or any API error.
    channel_id and message_id are accepted for interface compatibility but
    are not used by this endpoint (it operates on the URLs directly).
    """
    import json as _json

    try:
        token = load_discord_creds().bot_token
    except Exception:
        return stored_json

    try:
        attachments: list[dict[str, Any]] = _json.loads(stored_json)
        urls = [a["url"] for a in attachments if a.get("url")]
        if not urls:
            return stored_json

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{DISCORD_API}/attachments/refresh-urls",
                headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
                json={"attachment_urls": urls},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

        url_map: dict[str, str] = {
            r["original"]: r["refreshed"]
            for r in data.get("refreshed_urls", [])
        }
        for a in attachments:
            if a.get("url") in url_map:
                a["url"] = url_map[a["url"]]
        return _json.dumps(attachments)
    except Exception as exc:
        logger.debug(
            "discord: could not refresh attachments for %s/%s: %s",
            channel_id, message_id, exc,
        )
        return stored_json


def _parse_mentions(content: str) -> list[str]:
    """Extract <@USER_ID> and <@!USER_ID> user IDs from message content."""
    import re
    return re.findall(r"<@!?(\d+)>", content)


def _extract_attachments(msg: dict[str, Any]) -> str | None:
    """Return attachments as a JSON string for storage in scalar metadata, or None if none."""
    import json
    result: list[dict[str, Any]] = []
    for a in msg.get("attachments", []):
        url: str = a.get("url", "")
        if not url:
            continue
        entry: dict[str, Any] = {"url": url, "filename": a.get("filename", "")}
        content_type: str = a.get("content_type", "")
        if content_type:
            entry["content_type"] = content_type
        width = a.get("width")
        height = a.get("height")
        if width and height:
            entry["width"] = width
            entry["height"] = height
        result.append(entry)
    return json.dumps(result) if result else None


class DiscordConnector(BaseConnector):
    source = "discord"
    entity_types = (
        EntityTypeDefinition(
            name="Channel",
            resource_type="channel",
            description="Discord channels and direct-message conversations.",
        ),
        EntityTypeDefinition(
            name="Message",
            resource_type="message",
            description="Discord messages, replies, and uploads stored in message metadata.",
        ),
    )
    fetch_policy = FetchPolicy(stale_after_seconds=_STALE_AFTER)
    poll_interval: timedelta | None = timedelta(minutes=5)  # type: ignore[assignment]
    url_patterns = ["https://discord.com/*"]
    auth_label = "discord"
    auth_description = "Discord guild channels, DMs, and threads (channels the bot is in): Channel and Message entities with attachments, authors, and mentions."
    onboard_prompt = "Set up Discord?"

    @classmethod
    def run_auth_flow(
        cls,
        account_id: str | None = None,
        add: bool = False,
        args: list[str] | None = None,
    ) -> None:
        from agentgraph_connector_discord.auth import run_token_flow

        if args:
            raise ValueError(f"Discord authentication does not accept options: {' '.join(args)}")
        run_token_flow(account_id=account_id, add=add)

    @classmethod
    def get_authenticated_user(cls) -> str | None:
        try:
            return load_discord_creds().bot_user_id
        except Exception:
            return None

    @classmethod
    def list_accounts(cls) -> list[ConnectorAccount]:
        return [
            ConnectorAccount(
                account_id=str(account["account_id"]),
                label=str(account["label"]),
                auth_group=cls.auth_label or cls.source,
                source=cls.source,
                user_id=account.get("bot_user_id"),
                auth_method="bot-token",
            )
            for account in list_discord_accounts()
        ]

    @classmethod
    async def verify_auth(cls, account_id: str | None = None) -> tuple[str, str | None]:
        try:
            creds = load_discord_creds(account_id)
        except Exception:
            return ("missing", None)
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    "https://discord.com/api/v10/users/@me",
                    headers={"Authorization": f"Bot {creds.bot_token}"},
                )
            if resp.status_code == 200:
                data = resp.json()
                username: str | None = data.get("username")
                return ("ok", username or data.get("id"))
            if resp.status_code == 401:
                return ("invalid", "token rejected (401) — run: agentgraph auth discord")
            return ("invalid", f"HTTP {resp.status_code}")
        except Exception as exc:
            return ("invalid", f"network error: {type(exc).__name__}")

    def can_handle(self, url: str) -> bool:
        return self.resolve_url(url) is not None

    def resolve_url(self, url: str) -> SourceReference | None:
        dm_match = _DISCORD_DM_URL_RE.match(url)
        if dm_match is not None:
            return SourceReference(
                source=self.source,
                resource_type="dm",
                resource_id=dm_match.group("channel_id"),
            )

        channel_match = _DISCORD_CHANNEL_URL_RE.match(url)
        if channel_match is not None:
            return SourceReference(
                source=self.source,
                resource_type="channel",
                resource_id=channel_match.group("channel_id"),
            )
        return None

    def normalise_fetch_id(self, resource_id: str, entity_type: str) -> tuple[str, ResourceType]:
        # Message IDs are stored as "channel_id:message_id"; fetch operates on the channel.
        if entity_type == "Message" and ":" in resource_id:
            return resource_id.split(":")[0], "channel"
        return super().normalise_fetch_id(resource_id, entity_type)

    async def enrich_results(self, entities: list[dict[str, Any]]) -> None:
        """Refresh expiring Discord CDN attachment URLs in query results."""
        messages = [
            entity for entity in entities
            if entity.get("entity_type") == "Message"
        ]
        if not messages:
            return

        async def _refresh_one(entity: dict[str, Any]) -> None:
            meta = entity.get("metadata")
            if not isinstance(meta, dict):
                return
            metadata = cast(dict[str, Any], meta)
            stored_json = metadata.get("attachments")
            if not isinstance(stored_json, str) or not stored_json:
                return
            channel_id = metadata.get("channel_id")
            message_id = metadata.get("message_id")
            if not isinstance(channel_id, str) or not isinstance(message_id, str):
                return
            if not channel_id or not message_id:
                return
            metadata["attachments"] = await refresh_attachment_urls(
                channel_id,
                message_id,
                stored_json,
            )

        await asyncio.gather(*(_refresh_one(message) for message in messages))

    async def fetch(
        self,
        resource_type: ResourceType,
        resource_id: str,
        meta: dict[str, str] | None = None,
        account_id: str | None = None,
    ) -> EntityBatch:
        last_sync = await self.last_synced_at(resource_id)
        decision = self.fetch_policy.decide(last_sync)

        if decision == FetchPolicy.FRESH:
            logger.debug("discord/%s is fresh", resource_id)
            return EntityBatch()

        after_snowflake: str | None = None
        if decision == FetchPolicy.INCREMENTAL and last_sync:
            after_snowflake = _snowflake_after(last_sync)

        selected_account_id = account_id or ((meta or {}).get("account_id") if meta else None)
        is_dm = resource_type == "dm"
        logger.info("Fetching Discord %s %s (policy=%s)", resource_type, resource_id, decision)
        batch = await _fetch_channel(
            resource_id,
            after_snowflake=after_snowflake,
            is_dm=is_dm,
            account_id=selected_account_id,
        )
        await upsert_batch(batch)
        return batch

    async def poll(
        self,
        cursor: dict[str, Any],
        account_id: str | None = None,
    ) -> tuple[EntityBatch, dict[str, Any]]:
        channel_rows = await get_known_channel_syncs("discord", account_id=account_id)
        combined = EntityBatch()
        for channel_id, synced_at in channel_rows:
            after_snowflake = _snowflake_after(synced_at) if synced_at else None
            try:
                batch = await _fetch_channel(
                    channel_id,
                    after_snowflake=after_snowflake,
                    account_id=account_id,
                )
                combined.entities.extend(batch.entities)
                combined.persons.extend(batch.persons)
                combined.edges.extend(batch.edges)
            except Exception:
                logger.exception("discord poll: failed to fetch channel %s", channel_id)
        return combined, cursor


async def _fetch_user(
    client: httpx.AsyncClient,
    user_id: str,
    seen_users: dict[str, PersonRecord],
    account_id: str | None = None,
) -> PersonRecord | None:
    if user_id in seen_users:
        return seen_users[user_id]
    if user_id in _user_cache:
        seen_users[user_id] = _user_cache[user_id]
        return _user_cache[user_id]
    try:
        data = await _api_get(client, f"/users/{user_id}", account_id=account_id)
        username = data.get("username", "")
        global_name = data.get("global_name") or username
        person = PersonRecord(
            platform="discord",
            platform_user_id=user_id,
            platform_username=username,
            display_name=global_name or None,
            # Discord bots cannot access user emails
        )
        seen_users[user_id] = person
        _user_cache[user_id] = person
        return person
    except Exception as exc:
        logger.debug("Could not fetch Discord user %s: %s", user_id, exc)
        return None


async def _fetch_thread_messages(
    client: httpx.AsyncClient,
    thread_id: str,
    parent_channel_id: str,
    parent_message_id: str,
    entities: list[EntityRecord],
    edges: list[EdgeRecord],
    seen_users: dict[str, PersonRecord],
    persons: list[PersonRecord],
    after_snowflake: str | None,
    guild_id: str = "",
    account_id: str | None = None,
) -> None:
    """Fetch messages in a Discord thread (threads are channels in Discord API)."""
    try:
        params: dict[str, Any] = {"limit": 100}
        if after_snowflake:
            params["after"] = after_snowflake

        messages = await _api_get(client, f"/channels/{thread_id}/messages", account_id=account_id, **params)
    except Exception as exc:
        logger.warning("Could not fetch thread %s: %s", thread_id, exc)
        return

    for msg in messages:
        msg_id: str = msg.get("id", "")
        content: str = msg.get("content", "")
        author: dict[str, Any] = msg.get("author", {})
        user_id: str = author.get("id", "")

        if not msg_id:
            continue

        attachments_json = _extract_attachments(msg)
        meta: dict[str, Any] = {"channel_id": parent_channel_id, "thread_id": thread_id, "message_id": msg_id}
        if guild_id:
            meta["web_url"] = f"https://discord.com/channels/{guild_id}/{parent_channel_id}/{msg_id}"
        if attachments_json:
            meta["attachments"] = attachments_json
        if account_id:
            meta["account_id"] = account_id

        entities.append(EntityRecord(
            entity_type="Message",
            platform="discord",
            platform_entity_id=f"{thread_id}:{msg_id}",
            content=content,
            source_created_at=_snowflake_to_dt(msg_id),
            source_updated_at=_parse_discord_time(msg.get("edited_timestamp"))
            or _snowflake_to_dt(msg_id),
            metadata=meta,
            retention_policy="observed",
            retention_parent_platform_entity_id=parent_channel_id,
        ))

        edges.append(EdgeRecord(
            edge_type="replied_to",
            source_platform_entity_id=f"{thread_id}:{msg_id}",
            target_platform_entity_id=f"{parent_channel_id}:{parent_message_id}",
            platform="discord",
        ))
        edges.append(EdgeRecord(
            edge_type="posted_in",
            source_platform_entity_id=f"{thread_id}:{msg_id}",
            target_platform_entity_id=parent_channel_id,
            platform="discord",
        ))

        if user_id:
            person = await _fetch_user(client, user_id, seen_users, account_id=account_id)
            if person and person not in persons:
                persons.append(person)
            edges.append(EdgeRecord(
                edge_type="authored",
                source_platform_user_id=user_id,
                target_platform_entity_id=f"{thread_id}:{msg_id}",
                platform="discord",
            ))

        for mentioned_id in _parse_mentions(content):
            person = await _fetch_user(client, mentioned_id, seen_users, account_id=account_id)
            if person and person not in persons:
                persons.append(person)
            edges.append(EdgeRecord(
                edge_type="mentions",
                source_platform_entity_id=f"{thread_id}:{msg_id}",
                target_platform_user_id=mentioned_id,
                platform="discord",
            ))


async def _fetch_channel(
    channel_id: str,
    after_snowflake: str | None = None,
    is_dm: bool = False,
    account_id: str | None = None,
) -> EntityBatch:
    entities: list[EntityRecord] = []
    persons: list[PersonRecord] = []
    edges: list[EdgeRecord] = []
    seen_users: dict[str, PersonRecord] = {}

    async with httpx.AsyncClient(timeout=30) as client:
        # Channel entity
        try:
            channel_info = await _api_get(client, f"/channels/{channel_id}", account_id=account_id)
        except Exception as exc:
            logger.error("Could not fetch Discord channel %s: %s", channel_id, exc)
            raise

        guild_id = channel_info.get("guild_id", "")
        channel_type: int = channel_info.get("type", 0)

        if channel_type in (1, 3):  # 1 = DM, 3 = Group DM
            recipients: list[dict[str, Any]] = channel_info.get("recipients", [])
            if channel_type == 3 and channel_info.get("name"):
                channel_name = channel_info["name"]
            elif recipients:
                channel_name = ", ".join(r.get("username", r.get("id", "")) for r in recipients)
            else:
                channel_name = channel_id
            # Emit Person records for DM participants from channel metadata
            for recipient in recipients:
                user_id: str = recipient.get("id", "")
                if not user_id or user_id in seen_users:
                    continue
                username = recipient.get("username", "")
                global_name = recipient.get("global_name") or username
                person = PersonRecord(
                    platform="discord",
                    platform_user_id=user_id,
                    platform_username=username,
                    display_name=global_name or None,
                )
                seen_users[user_id] = person
                _user_cache[user_id] = person
                persons.append(person)
                edges.append(EdgeRecord(
                    edge_type="participated_in",
                    source_platform_user_id=user_id,
                    target_platform_entity_id=channel_id,
                    platform="discord",
                ))
        else:
            channel_name = channel_info.get("name", channel_id)

        channel_meta: dict[str, Any] = {}
        if guild_id:
            channel_meta["guild_id"] = guild_id
            channel_meta["web_url"] = f"https://discord.com/channels/{guild_id}/{channel_id}"
        if account_id:
            channel_meta["account_id"] = account_id
        entities.append(EntityRecord(
            entity_type="Channel",
            platform="discord",
            platform_entity_id=channel_id,
            title=f"#{channel_name}",
            metadata=channel_meta,
        ))

        # Fetch messages (Discord returns newest-first; reverse for chronological)
        params: dict[str, Any] = {"limit": 100}
        if after_snowflake:
            params["after"] = after_snowflake

        try:
            messages = await _api_get(client, f"/channels/{channel_id}/messages", account_id=account_id, **params)
        except Exception as exc:
            logger.error("Could not fetch messages for Discord channel %s: %s", channel_id, exc)
            raise

        for msg in messages:
            msg_id: str = msg.get("id", "")
            content: str = msg.get("content", "")
            author: dict[str, Any] = msg.get("author", {})
            user_id: str = author.get("id", "")
            thread: dict[str, Any] | None = msg.get("thread")

            if not msg_id:
                continue

            attachments_json = _extract_attachments(msg)
            meta: dict[str, Any] = {"channel_id": channel_id, "message_id": msg_id, "guild_id": guild_id}
            if guild_id:
                meta["web_url"] = f"https://discord.com/channels/{guild_id}/{channel_id}/{msg_id}"
            if attachments_json:
                meta["attachments"] = attachments_json
            if account_id:
                meta["account_id"] = account_id

            entities.append(EntityRecord(
                entity_type="Message",
                platform="discord",
                platform_entity_id=f"{channel_id}:{msg_id}",
                content=content,
                source_created_at=_snowflake_to_dt(msg_id),
                source_updated_at=_parse_discord_time(msg.get("edited_timestamp"))
                or _snowflake_to_dt(msg_id),
                metadata=meta,
                retention_policy="observed",
                retention_parent_platform_entity_id=channel_id,
            ))

            edges.append(EdgeRecord(
                edge_type="posted_in",
                source_platform_entity_id=f"{channel_id}:{msg_id}",
                target_platform_entity_id=channel_id,
                platform="discord",
            ))

            if user_id:
                person = await _fetch_user(client, user_id, seen_users, account_id=account_id)
                if person and person not in persons:
                    persons.append(person)
                edges.append(EdgeRecord(
                    edge_type="authored",
                    source_platform_user_id=user_id,
                    target_platform_entity_id=f"{channel_id}:{msg_id}",
                    platform="discord",
                ))

            for mentioned_id in _parse_mentions(content):
                person = await _fetch_user(client, mentioned_id, seen_users, account_id=account_id)
                if person and person not in persons:
                    persons.append(person)
                edges.append(EdgeRecord(
                    edge_type="mentions",
                    source_platform_entity_id=f"{channel_id}:{msg_id}",
                    target_platform_user_id=mentioned_id,
                    platform="discord",
                ))

            # Fetch thread replies if this message spawned a thread
            if thread:
                thread_id: str = thread.get("id", "")
                if thread_id:
                    await _fetch_thread_messages(
                        client, thread_id, channel_id, msg_id,
                        entities, edges, seen_users, persons, after_snowflake,
                        guild_id=guild_id, account_id=account_id,
                    )

    batch = EntityBatch(entities=entities, persons=persons, edges=edges)
    for entity in batch.entities[:]:
        batch.add_stubs_from(entity)
    return batch
