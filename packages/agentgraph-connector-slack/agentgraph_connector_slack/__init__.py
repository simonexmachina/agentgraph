"""Slack connector with OAuth and browser-session authentication."""

from __future__ import annotations

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
    ResourceUnavailableError,
    SourceReference,
    get_known_channel_syncs,
)
from agentgraph.graph.upsert import upsert_batch
from agentgraph_connector_slack.auth import (
    SlackBrowserCredentials,
    account_id_for_team,
    list_slack_accounts,
    load_slack_creds,
    slack_headers,
)

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api"
_STALE_AFTER = 5 * 60  # 5 minutes
_PADDING_MESSAGES = 20  # messages to fetch on first visit as immediate context
_SLACK_CHANNEL_URL_RE = re.compile(
    r"https://app\.slack\.com/client/(?P<workspace_id>[A-Z0-9]+)/(?P<channel_id>[A-Z0-9]+)"
)
_INACCESSIBLE_CHANNEL_ERROR_CODES = frozenset({
    "channel_not_found",
    "missing_scope",
    "not_in_channel",
    "restricted_action",
})


class SlackApiError(RuntimeError):
    """A Slack Web API response where the request completed but was rejected."""

    def __init__(self, method: str, error_code: str) -> None:
        self.method = method
        self.error_code = error_code
        super().__init__(f"Slack API error on {method}: {error_code}")


def _team_id_from_token(account_id: str | None = None) -> str | None:
    """Extract the Slack team ID from the stored credentials."""
    try:
        creds = load_slack_creds(account_id)
    except RuntimeError:
        return None
    if creds.team_id:
        return creds.team_id
    if not isinstance(creds, SlackBrowserCredentials):
        return None
    parts = creds.xoxc_token.split("-")
    return parts[1] if len(parts) >= 2 else None


def _channel_ref(team_id: str, channel_id: str) -> str:
    return f"{team_id}/{channel_id}"


def _message_ref(team_id: str, channel_id: str, ts: str) -> str:
    return f"{team_id}/{channel_id}:{ts}"


def _user_ref(team_id: str, user_id: str) -> str:
    return f"{team_id}/{user_id}"


def _split_channel_ref(resource_id: str) -> tuple[str, str]:
    team_id, _, channel_id = resource_id.partition("/")
    if not team_id or not channel_id:
        raise ValueError(f"Slack resource must be workspace-qualified: {resource_id}")
    return team_id, channel_id


def _normalise_channel_ref(resource_id: str, account_id: str | None = None) -> str:
    if "/" in resource_id:
        return resource_id
    team_id = _team_id_from_token(account_id)
    if not team_id:
        raise ValueError(f"Slack resource must be workspace-qualified: {resource_id}")
    return _channel_ref(team_id, resource_id)


async def _api_get(
    client: httpx.AsyncClient,
    method: str,
    account_id: str | None = None,
    **params: Any,
) -> dict[str, Any]:
    for attempt in range(2):
        resp = await client.get(
            f"{SLACK_API}/{method}",
            headers=await slack_headers(
                account_id,
                force_refresh=attempt == 1,
                client=client,
            ),
            params=params,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        if data.get("ok"):
            return data
        error_code = str(data.get("error", "unknown"))
        if error_code != "token_expired" or attempt == 1:
            raise SlackApiError(method, error_code)
    raise SlackApiError(method, "token_expired")


async def _fetch_channel_info(client: httpx.AsyncClient, channel_id: str, account_id: str | None = None) -> dict[str, Any]:
    data = await _api_get(client, "conversations.info", account_id=account_id, channel=channel_id)
    return data.get("channel", {})  # type: ignore[return-value]


async def _fetch_user(client: httpx.AsyncClient, user_id: str, account_id: str | None = None) -> dict[str, Any]:
    data = await _api_get(client, "users.info", account_id=account_id, user=user_id)
    return data.get("user", {})  # type: ignore[return-value]


def _ts_to_dt(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=UTC)


def _edited_at(message: dict[str, Any]) -> datetime | None:
    edited = message.get("edited")
    if not isinstance(edited, dict):
        return None
    edited_ts = cast(dict[str, object], edited).get("ts")
    if not isinstance(edited_ts, str) or not edited_ts:
        return None
    return _ts_to_dt(edited_ts)


def _parse_mentions(text: str) -> list[str]:
    """Extract <@UXXXXXXX> user IDs from message text."""
    import re
    return re.findall(r"<@([A-Z0-9]+)>", text)


def _parse_channel_mentions(text: str) -> list[str]:
    """Extract <#CXXXXXXX> channel IDs from message text."""
    import re
    return re.findall(r"<#([A-Z0-9]+)(?:\|[^>]*)?>", text)


def _user_display_name(user_info: dict[str, Any]) -> str | None:
    """Return the most useful human-readable Slack user name."""
    profile_value = user_info.get("profile")
    if isinstance(profile_value, dict):
        profile = cast(dict[str, Any], profile_value)
        for field in ("display_name", "real_name"):
            value = profile.get(field)
            if isinstance(value, str) and value:
                return value
    for field in ("real_name", "name"):
        value = user_info.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _authenticated_user_id(account_id: str | None) -> str | None:
    try:
        return load_slack_creds(account_id).user_id
    except RuntimeError:
        return None


async def _channel_title(
    client: httpx.AsyncClient,
    channel_info: dict[str, Any],
    account_id: str | None = None,
) -> str:
    """Build a readable title for Slack channels, including DMs without names."""
    channel_name = channel_info.get("name")
    if isinstance(channel_name, str) and channel_name:
        return f"#{channel_name}"

    if channel_info.get("is_im"):
        user_id = channel_info.get("user")
        if isinstance(user_id, str) and user_id:
            try:
                user_info = await _fetch_user(client, user_id, account_id=account_id)
                display_name = _user_display_name(user_info)
                if display_name:
                    return display_name
            except Exception:
                logger.debug("Could not fetch DM participant %s", user_id)
        return "Direct message"

    if channel_info.get("is_mpim"):
        current_user_id = _authenticated_user_id(account_id)
        member_ids = channel_info.get("members")
        if isinstance(member_ids, list):
            names: list[str] = []
            for member_id in cast(list[Any], member_ids):
                if not isinstance(member_id, str) or member_id == current_user_id:
                    continue
                try:
                    user_info = await _fetch_user(client, member_id, account_id=account_id)
                    display_name = _user_display_name(user_info)
                    if display_name:
                        names.append(display_name)
                except Exception:
                    logger.debug("Could not fetch group DM participant %s", member_id)
            if names:
                return ", ".join(names)
        return "Group direct message"

    channel_id = channel_info.get("id")
    return f"#{channel_id}" if isinstance(channel_id, str) and channel_id else "#unknown"


class SlackConnector(BaseConnector):
    source = "slack"
    entity_types = (
        EntityTypeDefinition(
            name="Channel",
            resource_type="channel",
            description="Slack channels and direct-message conversations.",
        ),
        EntityTypeDefinition(
            name="Message",
            resource_type="message",
            description="Slack messages, replies, and uploads stored in message metadata.",
        ),
    )
    fetch_policy = FetchPolicy(stale_after_seconds=_STALE_AFTER)
    poll_interval: timedelta | None = timedelta(minutes=5)  # type: ignore[assignment]
    url_patterns = ["https://app.slack.com/*"]
    auth_label = "slack"
    auth_description = "Slack workspace channels and DMs: Channel and Message entities with thread replies, authors, and user/channel mentions."
    onboard_prompt = "Set up Slack?"

    @classmethod
    def run_auth_flow(
        cls,
        account_id: str | None = None,
        add: bool = False,
        args: list[str] | None = None,
    ) -> None:
        from agentgraph_connector_slack.auth import run_interactive_auth_flow

        if args:
            cls.run_auth_flow_with_args(args, account_id=account_id, add=add)
            return
        run_interactive_auth_flow(account_id=account_id, add=add)

    @classmethod
    def run_auth_flow_with_args(
        cls,
        args: list[str],
        account_id: str | None = None,
        add: bool = False,
    ) -> None:
        from agentgraph_connector_slack.auth import (
            run_cookie_flow,
            run_guided_oauth_flow,
            run_interactive_auth_flow,
        )

        method: str | None = None
        client_id: str | None = None
        xoxc_token: str | None = None
        d_cookie: str | None = None
        index = 0
        while index < len(args):
            arg = args[index]
            if arg in {"--method", "--client-id", "--xoxc-token", "--d-cookie"}:
                if index + 1 >= len(args):
                    raise ValueError(f"{arg} requires a value")
                value = args[index + 1]
                index += 1
            elif any(arg.startswith(f"{option}=") for option in (
                "--method", "--client-id", "--xoxc-token", "--d-cookie"
            )):
                option, value = arg.split("=", 1)
                arg = option
            else:
                raise ValueError(f"Unknown Slack authentication option: {arg}")
            if arg == "--method":
                method = value
            elif arg == "--client-id":
                client_id = value.strip()
                if not client_id:
                    raise ValueError("--client-id requires a non-empty value")
            elif arg == "--xoxc-token":
                xoxc_token = value
            else:
                d_cookie = value
            index += 1

        if method not in {None, "oauth", "browser"}:
            raise ValueError("Slack auth method must be 'oauth' or 'browser'")
        browser_options = xoxc_token is not None or d_cookie is not None
        if method == "oauth" and browser_options:
            raise ValueError("--xoxc-token and --d-cookie cannot be used with --method oauth")
        if method == "browser" and client_id is not None:
            raise ValueError("--client-id cannot be used with --method browser")
        selected_method = method or (
            "browser" if browser_options else "oauth" if client_id is not None else None
        )
        if selected_method is None:
            run_interactive_auth_flow(account_id=account_id, add=add)
            return
        if selected_method == "browser":
            run_cookie_flow(
                account_id=account_id,
                add=add,
                xoxc_token=xoxc_token,
                d_cookie=d_cookie,
            )
            return
        run_guided_oauth_flow(account_id=account_id, add=add, client_id=client_id)

    @classmethod
    def get_authenticated_user(cls) -> str | None:
        try:
            creds = load_slack_creds()
            if creds.team_name and creds.user_id:
                return f"{creds.team_name} / {creds.user_id}"
            return creds.user_id
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
                user_id=account.get("user_id"),
                workspace_id=account.get("team_id"),
                email=account.get("email"),
                auth_method=account.get("auth_method"),
            )
            for account in list_slack_accounts()
        ]

    @classmethod
    async def verify_auth(cls, account_id: str | None = None) -> tuple[str, str | None]:
        try:
            credentials = load_slack_creds(account_id)
        except RuntimeError:
            return ("missing", None)
        except Exception as exc:
            return ("invalid", str(exc))
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                data = await _api_get(client, "auth.test", account_id=account_id)
        except Exception as exc:
            return ("invalid", str(exc))
        team_name = data.get("team") or credentials.team_name or credentials.team_id
        user_id = data.get("user_id") or credentials.user_id
        detail = f"{team_name} / {user_id}" if team_name and user_id else user_id or team_name
        return ("ok", str(detail) if detail else "authenticated")

    @classmethod
    def current_user_ids(cls) -> list[str]:
        ids: list[str] = []
        for account in list_slack_accounts():
            team_id = account.get("team_id")
            user_id = account.get("user_id")
            if team_id and user_id:
                ids.append(f"slack:{team_id}/{user_id}")
        return ids

    def can_handle(self, url: str) -> bool:
        return self.resolve_url(url) is not None

    def resolve_url(self, url: str) -> SourceReference | None:
        match = _SLACK_CHANNEL_URL_RE.match(url)
        if match is None:
            return None
        return SourceReference(
            source=self.source,
            resource_type="channel",
            resource_id=f"{match.group('workspace_id')}/{match.group('channel_id')}",
        )

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
            logger.debug("slack/%s is fresh", resource_id)
            return EntityBatch()

        oldest: str | None = None
        if decision == FetchPolicy.INCREMENTAL and last_sync:
            oldest = str(last_sync.timestamp())

        resource_id = _normalise_channel_ref(resource_id, account_id)
        team_id, _ = _split_channel_ref(resource_id)
        selected_account_id = account_id or account_id_for_team(team_id)
        logger.info("Fetching Slack channel %s (policy=%s)", resource_id, decision)
        try:
            batch = await _fetch_channel(resource_id, oldest=oldest, account_id=selected_account_id)
        except SlackApiError as exc:
            if exc.error_code in _INACCESSIBLE_CHANNEL_ERROR_CODES:
                raise ResourceUnavailableError("Slack channel is unavailable to this account") from exc
            raise
        await upsert_batch(batch)
        return batch

    async def poll(
        self,
        cursor: dict[str, Any],
        account_id: str | None = None,
    ) -> tuple[EntityBatch, dict[str, Any]]:
        channel_rows = await get_known_channel_syncs("slack", account_id=account_id)
        combined = EntityBatch()
        for channel_id, synced_at in channel_rows:
            oldest = str(synced_at.timestamp()) if synced_at else None
            try:
                batch = await _fetch_channel(channel_id, oldest=oldest, account_id=account_id)
                combined.entities.extend(batch.entities)
                combined.persons.extend(batch.persons)
                combined.edges.extend(batch.edges)
            except Exception:
                logger.exception("slack poll: failed to fetch channel %s", channel_id)
        return combined, cursor


async def _fetch_thread_replies(
    client: httpx.AsyncClient,
    channel_ref: str,
    thread_ts: str,
    entities: list[EntityRecord],
    persons: list[PersonRecord],
    edges: list[EdgeRecord],
    seen_users: set[str],
    team_id: str | None = None,
    account_id: str | None = None,
) -> None:
    """Fetch replies to a threaded message and add them to the batch in-place."""
    if team_id is None:
        return
    _, channel_id = _split_channel_ref(channel_ref)
    try:
        data = await _api_get(
            client, "conversations.replies", account_id=account_id, channel=channel_id, ts=thread_ts
        )
    except Exception as exc:
        logger.warning("Could not fetch replies for %s:%s: %s", channel_id, thread_ts, exc)
        return

    reply_messages: list[dict[str, Any]] = data.get("messages", [])

    for reply in reply_messages:
        ts: str = reply.get("ts", "")
        if not ts or ts == thread_ts:
            continue  # skip the parent message Slack echoes back

        user_id: str = reply.get("user", "")
        text: str = reply.get("text", "")

        reply_meta: dict[str, Any] = {"team_id": team_id, "channel_id": channel_id, "ts": ts, "thread_ts": thread_ts}
        if team_id:
            ts_compact = ts.replace(".", "")
            reply_meta["web_url"] = f"https://app.slack.com/client/{team_id}/{channel_id}/p{ts_compact}"
        if account_id:
            reply_meta["account_id"] = account_id
        entities.append(EntityRecord(
            entity_type="Message",
            platform="slack",
            platform_entity_id=_message_ref(team_id, channel_id, ts),
            content=text,
            source_created_at=_ts_to_dt(ts),
            source_updated_at=_edited_at(reply),
            metadata=reply_meta,
            retention_policy="observed",
            retention_parent_platform_entity_id=channel_ref,
        ))

        edges.append(EdgeRecord(
            edge_type="replied_to",
            source_platform_entity_id=_message_ref(team_id, channel_id, ts),
            target_platform_entity_id=_message_ref(team_id, channel_id, thread_ts),
            platform="slack",
        ))
        edges.append(EdgeRecord(
            edge_type="posted_in",
            source_platform_entity_id=_message_ref(team_id, channel_id, ts),
            target_platform_entity_id=channel_ref,
            platform="slack",
        ))

        if user_id and user_id not in seen_users:
            seen_users.add(user_id)
            try:
                user_info = await _fetch_user(client, user_id, account_id=account_id)
                profile = user_info.get("profile", {})
                persons.append(PersonRecord(
                    platform="slack",
                    platform_user_id=_user_ref(team_id, user_id),
                    platform_username=user_info.get("name"),
                    canonical_email=profile.get("email") or None,
                    display_name=profile.get("real_name") or None,
                ))
            except Exception:
                logger.debug("Could not fetch user %s", user_id)

        if user_id:
            edges.append(EdgeRecord(
                edge_type="authored",
                source_platform_user_id=_user_ref(team_id, user_id),
                target_platform_entity_id=_message_ref(team_id, channel_id, ts),
                platform="slack",
            ))

        for mentioned_id in _parse_mentions(text):
            if mentioned_id not in seen_users:
                seen_users.add(mentioned_id)
                try:
                    user_info = await _fetch_user(client, mentioned_id, account_id=account_id)
                    profile = user_info.get("profile", {})
                    persons.append(PersonRecord(
                        platform="slack",
                        platform_user_id=_user_ref(team_id, mentioned_id),
                        platform_username=user_info.get("name"),
                        canonical_email=profile.get("email") or None,
                        display_name=profile.get("real_name") or None,
                    ))
                except Exception:
                    logger.debug("Could not fetch user %s", mentioned_id)
            edges.append(EdgeRecord(
                edge_type="mentions",
                source_platform_entity_id=_message_ref(team_id, channel_id, ts),
                target_platform_user_id=_user_ref(team_id, mentioned_id),
                platform="slack",
            ))

        for mentioned_channel_id in _parse_channel_mentions(text):
            edges.append(EdgeRecord(
                edge_type="mentions",
                source_platform_entity_id=_message_ref(team_id, channel_id, ts),
                target_platform_entity_id=_channel_ref(team_id, mentioned_channel_id),
                platform="slack",
            ))


async def _fetch_channel(channel_ref: str, oldest: str | None = None, account_id: str | None = None) -> EntityBatch:
    entities: list[EntityRecord] = []
    persons: list[PersonRecord] = []
    edges: list[EdgeRecord] = []
    seen_users: set[str] = set()

    team_id, channel_id = _split_channel_ref(channel_ref)

    async with httpx.AsyncClient(timeout=30) as client:
        # Channel entity
        channel_info = await _fetch_channel_info(client, channel_id, account_id=account_id)
        channel_title = await _channel_title(client, channel_info, account_id=account_id)

        channel_meta: dict[str, Any] = {"team_id": team_id, "channel_id": channel_id}
        if team_id:
            channel_meta["web_url"] = f"https://app.slack.com/client/{team_id}/{channel_id}"
        if account_id:
            channel_meta["account_id"] = account_id
        channel_entity = EntityRecord(
            entity_type="Channel",
            platform="slack",
            platform_entity_id=channel_ref,
            title=channel_title,
            metadata=channel_meta,
        )
        entities.append(channel_entity)

        # Fetch messages
        params: dict[str, Any] = {"channel": channel_id, "limit": 100}
        if oldest:
            params["oldest"] = oldest

        data = await _api_get(client, "conversations.history", account_id=account_id, **params)
        messages: list[dict[str, Any]] = data.get("messages", [])

        for msg in messages:
            user_id: str = msg.get("user", "")
            ts: str = msg.get("ts", "")
            text: str = msg.get("text", "")
            thread_ts: str | None = msg.get("thread_ts")
            reply_count: int = msg.get("reply_count", 0)

            if not ts:
                continue

            msg_meta: dict[str, Any] = {"team_id": team_id, "channel_id": channel_id, "ts": ts}
            if team_id:
                ts_compact = ts.replace(".", "")
                msg_meta["web_url"] = f"https://app.slack.com/client/{team_id}/{channel_id}/p{ts_compact}"
            if account_id:
                msg_meta["account_id"] = account_id
            msg_entity = EntityRecord(
                entity_type="Message",
                platform="slack",
                platform_entity_id=_message_ref(team_id, channel_id, ts),
                content=text,
                source_created_at=_ts_to_dt(ts),
                source_updated_at=_edited_at(msg),
                metadata=msg_meta,
                retention_policy="observed",
                retention_parent_platform_entity_id=channel_ref,
            )
            entities.append(msg_entity)

            # posted_in edge: message → channel
            edges.append(EdgeRecord(
                edge_type="posted_in",
                source_platform_entity_id=_message_ref(team_id, channel_id, ts),
                target_platform_entity_id=channel_ref,
                platform="slack",
            ))

            # Thread reply edge (this message is itself a reply)
            if thread_ts and thread_ts != ts:
                edges.append(EdgeRecord(
                    edge_type="replied_to",
                    source_platform_entity_id=_message_ref(team_id, channel_id, ts),
                    target_platform_entity_id=_message_ref(team_id, channel_id, thread_ts),
                    platform="slack",
                ))

            # Author
            if user_id and user_id not in seen_users:
                seen_users.add(user_id)
                user_info = await _fetch_user(client, user_id, account_id=account_id)
                profile = user_info.get("profile", {})
                persons.append(PersonRecord(
                    platform="slack",
                    platform_user_id=_user_ref(team_id, user_id),
                    platform_username=user_info.get("name"),
                    canonical_email=profile.get("email") or None,
                    display_name=profile.get("real_name") or None,
                ))

            if user_id:
                edges.append(EdgeRecord(
                edge_type="authored",
                source_platform_user_id=_user_ref(team_id, user_id),
                target_platform_entity_id=_message_ref(team_id, channel_id, ts),
                    platform="slack",
                ))

            # User mention edges
            for mentioned_id in _parse_mentions(text):
                if mentioned_id not in seen_users:
                    seen_users.add(mentioned_id)
                    try:
                        user_info = await _fetch_user(client, mentioned_id, account_id=account_id)
                        profile = user_info.get("profile", {})
                        persons.append(PersonRecord(
                            platform="slack",
                            platform_user_id=_user_ref(team_id, mentioned_id),
                            platform_username=user_info.get("name"),
                            canonical_email=profile.get("email") or None,
                            display_name=profile.get("real_name") or None,
                        ))
                    except Exception:
                        logger.debug("Could not fetch user %s", mentioned_id)
                edges.append(EdgeRecord(
                    edge_type="mentions",
                    source_platform_entity_id=_message_ref(team_id, channel_id, ts),
                    target_platform_user_id=_user_ref(team_id, mentioned_id),
                    platform="slack",
                ))

            # Channel mention edges
            for mentioned_channel_id in _parse_channel_mentions(text):
                edges.append(EdgeRecord(
                    edge_type="mentions",
                    source_platform_entity_id=_message_ref(team_id, channel_id, ts),
                    target_platform_entity_id=_channel_ref(team_id, mentioned_channel_id),
                    platform="slack",
                ))

            # Fetch thread replies for messages that have them
            if reply_count > 0 and not (thread_ts and thread_ts != ts):
                await _fetch_thread_replies(
                    client, channel_ref, ts, entities, persons, edges, seen_users, team_id=team_id, account_id=account_id
                )

    batch = EntityBatch(entities=entities, persons=persons, edges=edges)
    for entity in batch.entities[:]:
        batch.add_stubs_from(entity)
    return batch
