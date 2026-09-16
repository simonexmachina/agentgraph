"""RSS and Atom connector."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from importlib import import_module
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from agentgraph_connector_web import fetch_http_resource

from agentgraph.connectors.base import (
    BaseConnector,
    ConnectorAccount,
    ConnectorCommandEffects,
    EdgeRecord,
    EntityBatch,
    EntityMetadataPatch,
    EntityRecord,
    EntityReference,
    EntityTypeDefinition,
    FetchPolicy,
    PersonRecord,
    ResourceType,
    SourceReference,
)
from agentgraph.core.context import get_backend
from agentgraph_connector_rss.auth import (
    add_feed_urls,
    import_opml_feeds,
    load_rss_settings,
    preview_feed,
    remove_feed_urls,
    resolve_feed_sources,
    run_rss_flow,
    verify_rss_auth,
)

_STALE_AFTER = 30 * 60
_FEED_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_MAX_FEED_BYTES = 5_000_000
_MAX_OBSERVATION_PATTERNS_PER_FEED = 5
_RSS_PAYLOAD_CURSOR_VERSION = 1
_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "dclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "_hsenc",
    "_hsmi",
}
logger = logging.getLogger(__name__)


class RssConnector(BaseConnector):
    source = "rss"
    entity_types = (
        EntityTypeDefinition(
            name="Document",
            resource_type="document",
            description="RSS and Atom feed entries.",
        ),
        EntityTypeDefinition(
            name="Folder",
            resource_type="folder",
            description="RSS and Atom feeds containing entries.",
        ),
    )
    fetch_policy = FetchPolicy(stale_after_seconds=_STALE_AFTER)
    poll_interval: timedelta | None = timedelta(minutes=15)  # type: ignore[assignment]
    url_patterns = []
    auth_label = "rss"
    auth_description = "RSS/Atom feeds: feed URLs are fetched directly and entries are indexed as Document entities."
    onboard_prompt = "Set up RSS feeds?"
    onboard_last = True
    appears_in_auth_status = False

    @classmethod
    def run_auth_flow(
        cls,
        account_id: str | None = None,
        add: bool = False,
        args: list[str] | None = None,
    ) -> None:
        if args:
            raise ValueError(f"RSS setup does not accept auth options: {' '.join(args)}")
        run_rss_flow(account_id=account_id, add=add)

    @classmethod
    def get_authenticated_user(cls) -> str | None:
        try:
            settings = load_rss_settings()
        except RuntimeError:
            return None
        return "RSS" if settings.feed_urls else None

    @classmethod
    def list_accounts(cls) -> list[ConnectorAccount]:
        return []

    @classmethod
    async def verify_auth(cls, account_id: str | None = None) -> tuple[str, str | None]:
        return await verify_rss_auth(account_id)

    @classmethod
    def run_cli_command(cls, args: list[str]) -> dict[str, Any]:
        if not args:
            raise ValueError(_rss_usage())
        command, *rest = args
        if command == "add":
            sources = _parse_feed_url_args(rest, command="add")
            feed_urls = resolve_feed_sources(sources)
            config = add_feed_urls(feed_urls, validate=False)
            return {
                "status": "ok",
                "source": cls.source,
                "feed_urls": config.feed_urls,
                "added": feed_urls,
            }
        if command == "remove":
            feed_urls = _parse_feed_url_args(rest, command="remove")
            config, removed_feed_urls = remove_feed_urls(feed_urls)
            return {
                "status": "ok",
                "source": cls.source,
                "feed_urls": config.feed_urls,
                "removed": removed_feed_urls,
            }
        if command == "import-opml":
            options = _parse_import_opml_args(rest)
            config, feeds, selected_feeds = import_opml_feeds(**options)
            selected_feed_urls = [feed.feed_url for feed in selected_feeds]
            return {
                "status": "ok",
                "source": cls.source,
                "feed_urls": config.feed_urls,
                "imported_feed_count": len(feeds),
                "selected_feed_count": len(selected_feeds),
                "added": selected_feed_urls,
            }
        raise ValueError(
            f"Unknown rss connector command '{command}'. Available: add, remove, import-opml"
        )

    @classmethod
    def cli_help(cls) -> str:
        return _rss_help()

    @classmethod
    def format_cli_result(cls, result: dict[str, Any]) -> str:
        return _format_rss_cli_result(result)

    @classmethod
    def command_effects(
        cls,
        args: list[str],
        result: dict[str, Any],
    ) -> ConnectorCommandEffects:
        _ = result
        command = args[0] if args else None
        removed = result.get("removed") if command == "remove" else None
        deleted = (
            tuple(
                EntityReference(platform=cls.source, platform_entity_id=f"feed/{_feed_id(feed_url)}")
                for feed_url in removed
            )
            if isinstance(removed, list)
            else ()
        )
        return ConnectorCommandEffects(
            poll=command in {"add", "import-opml"},
            delete_entities=deleted,
        )

    def can_handle(self, url: str) -> bool:
        return self.resolve_url(url) is not None

    def resolve_url(self, url: str) -> SourceReference | None:
        try:
            settings = load_rss_settings()
        except RuntimeError:
            return None
        if url not in settings.feed_urls:
            return None
        return SourceReference(
            source=self.source,
            resource_type="folder",
            resource_id=f"feed/{_feed_id(url)}",
            fetch_meta={"feed_url": url},
        )

    async def resolve_observation_url(
        self,
        url: str,
        meta: dict[str, str] | None = None,
    ) -> SourceReference | None:
        _ = meta
        normalised_url = normalise_article_url(url)
        if normalised_url is None:
            return None
        try:
            backend = get_backend()
        except RuntimeError:
            return None
        entries = await backend.query_by_filter(
            "Document",
            {"platform": self.source, "web_url": normalised_url},
            1,
            "updated_at",
            None,
            None,
        )
        if not entries:
            return None
        entry = entries[0]
        platform_entity_id = entry.get("platform_entity_id")
        metadata = entry.get("metadata")
        if not isinstance(platform_entity_id, str) or not isinstance(metadata, Mapping):
            return None
        return SourceReference(
            source=self.source,
            resource_type="document",
            resource_id=platform_entity_id,
            fetch_meta=_string_metadata(metadata),
        )

    async def observation_url_patterns(self) -> list[str]:
        try:
            settings = load_rss_settings()
        except RuntimeError:
            return []

        backend = get_backend()
        links_by_feed: dict[str, list[str]] = {}
        for feed_url in settings.feed_urls:
            feed = await backend.get_entity_by_platform(
                self.source, f"feed/{_feed_id(feed_url)}"
            )
            content = feed.get("content") if feed is not None else None
            if not isinstance(content, str) or not content:
                continue
            links_by_feed[feed_url] = await asyncio.to_thread(_article_links_from_feed_xml, content)
        return derive_observation_url_patterns(links_by_feed)

    async def ingest(
        self,
        account_id: str | None = None,
        *,
        defer_article_hydration_for_legacy_feeds: bool = False,
    ) -> EntityBatch:
        settings = load_rss_settings(account_id)
        combined = EntityBatch()
        for feed_url in settings.feed_urls:
            logger.info("Fetching RSS feed %s", feed_url)
            try:
                batch = await _fetch_feed(
                    feed_url,
                    hydrate_documents=not defer_article_hydration_for_legacy_feeds,
                    persist_feed_payload_only=defer_article_hydration_for_legacy_feeds,
                )
            except Exception as exc:
                logger.warning(
                    "Skipping RSS feed %s (%s: %s)",
                    feed_url,
                    type(exc).__name__,
                    exc,
                )
                logger.debug("RSS feed fetch failure", exc_info=True)
                continue
            combined.entities.extend(batch.entities)
            combined.metadata_patches.extend(batch.metadata_patches)
            combined.edges.extend(batch.edges)
            combined.persons.extend(batch.persons)
        return combined

    async def poll(
        self,
        cursor: dict[str, Any],
        account_id: str | None = None,
    ) -> tuple[EntityBatch, dict[str, Any]]:
        payload_needs_upgrade = cursor.get("feed_payload_version") != _RSS_PAYLOAD_CURSOR_VERSION
        batch = await self.ingest(
            account_id=account_id,
            defer_article_hydration_for_legacy_feeds=payload_needs_upgrade,
        )
        return batch, {
            "last_polled_at": datetime.now(UTC).isoformat(),
            "feed_payload_version": _RSS_PAYLOAD_CURSOR_VERSION,
        }

    async def preview_feed(self, feed_url: str, *, count: int = 3) -> dict[str, Any]:
        return await preview_feed(feed_url, count=count)

    async def fetch(
        self,
        resource_type: ResourceType,
        resource_id: str,
        meta: dict[str, str] | None = None,
        account_id: str | None = None,
    ) -> EntityBatch:
        _ = account_id
        feed_url = resource_id
        if resource_type == "folder" and meta and meta.get("feed_url"):
            feed_url = meta["feed_url"]
        if feed_url.startswith(("http://", "https://")):
            return await _fetch_feed(
                feed_url,
                hydrate_documents=True,
                new_documents_only=True,
            )
        if resource_type == "document" and meta and meta.get("web_url"):
            result = await _fetch_entry_document(resource_id, meta)
            if isinstance(result, EntityMetadataPatch):
                return EntityBatch(metadata_patches=[result])
            return EntityBatch(entities=[result])
        return EntityBatch()


async def _fetch_feed(
    feed_url: str,
    *,
    hydrate_documents: bool = False,
    new_documents_only: bool = False,
    persist_feed_payload_only: bool = False,
) -> EntityBatch:
    parsed_result = await _parse_feed(feed_url)
    raw_content = (
        parsed_result.raw_content if isinstance(parsed_result, _ParsedFeedPayload) else None
    )
    parsed = parsed_result.parsed if isinstance(parsed_result, _ParsedFeedPayload) else parsed_result
    feed_title = str(cast(dict[str, Any], parsed.feed).get("title") or feed_url)
    feed_id = _feed_id(feed_url)
    feed_entity_id = f"feed/{feed_id}"
    entities = [
        EntityRecord(
            entity_type="Folder",
            platform="rss",
            platform_entity_id=feed_entity_id,
            title=feed_title,
            content=raw_content or f"RSS feed: {feed_title}\n{feed_url}",
            content_searchable=raw_content is None,
            metadata={"feed_url": feed_url, "web_url": feed_url},
            retention_policy="persistent",
        )
    ]
    if persist_feed_payload_only:
        return EntityBatch(entities=entities)

    edges: list[EdgeRecord] = []
    persons: dict[str, PersonRecord] = {}
    batch = EntityBatch()
    feed_authors = _parse_authors(cast(dict[str, Any], parsed.feed))
    for author in feed_authors:
        persons.setdefault(author.user_id, author.to_person())

    for raw_entry in cast(list[Any], parsed.entries):
        entry = cast(dict[str, Any], raw_entry)
        # RFC 4287 §4.2.1: feed-level authors apply to entries that declare none.
        authors = _parse_authors(entry) or feed_authors
        entity = _entry_to_entity(feed_url, feed_entity_id, entry, authors)
        include_entity = True
        if new_documents_only:
            existing = await get_backend().get_entity_by_platform(
                "rss",
                entity.platform_entity_id,
            )
            include_entity = existing is None
        if include_entity:
            if hydrate_documents:
                hydrated = await _hydrate_entry_document(entity)
                if isinstance(hydrated, EntityMetadataPatch):
                    batch.metadata_patches.append(hydrated)
                    include_entity = False
                else:
                    entity = hydrated
            if include_entity:
                entities.append(entity)
        edges.append(
            EdgeRecord(
                edge_type="posted_in",
                source_platform_entity_id=entity.platform_entity_id,
                target_platform_entity_id=feed_entity_id,
                platform="rss",
            )
        )
        for author in authors:
            persons.setdefault(author.user_id, author.to_person())
            edges.append(
                EdgeRecord(
                    edge_type="authored",
                    source_platform_user_id=author.user_id,
                    target_platform_entity_id=entity.platform_entity_id,
                    platform="rss",
                )
            )
        if include_entity:
            batch.add_stubs_from(entity)

    edges.extend(
        EdgeRecord(
            edge_type="authored",
            source_platform_user_id=platform_user_id,
            target_platform_entity_id=feed_entity_id,
            platform="rss",
        )
        for platform_user_id in persons
    )

    batch.entities = [*entities, *batch.entities]
    batch.edges = [*edges, *batch.edges]
    batch.persons = [*persons.values(), *batch.persons]
    return batch


async def _parse_feed(feed_url: str) -> Any:
    import feedparser  # type: ignore[import-untyped]

    response = await fetch_http_resource(
        feed_url,
        headers={
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"
        },
        max_bytes=_MAX_FEED_BYTES,
        too_large_message=f"RSS feed response too large: limit is {_MAX_FEED_BYTES} bytes",
        timeout=_FEED_TIMEOUT,
        max_redirects=5,
    )
    parsed = await asyncio.to_thread(feedparser.parse, response.content)
    return _ParsedFeedPayload(
        parsed=parsed,
        raw_content=response.content.decode("utf-8", errors="replace"),
    )


@dataclass(frozen=True)
class _ParsedFeedPayload:
    parsed: Any
    raw_content: str

    def __getattr__(self, name: str) -> Any:
        return getattr(self.parsed, name)


def _article_links_from_feed_xml(content: str) -> list[str]:
    import feedparser  # type: ignore[import-untyped]

    parsed = feedparser.parse(content)
    links: list[str] = []
    for entry in cast(list[Any], parsed.entries):
        link = entry.get("link") if isinstance(entry, Mapping) else None
        if isinstance(link, str) and link:
            links.append(link)
    return links


def _rss_usage() -> str:
    return (
        "Usage: agentgraph connector rss add <feed-or-page-url> [feed-or-page-url...]\n"
        "   or: agentgraph connector rss remove <feed-or-page-url> [feed-or-page-url...]\n"
        "   or: agentgraph connector rss import-opml <file.opml> [--all | --select <indexes>]"
    )


def _rss_help() -> str:
    return "\n".join(
        [
            "RSS connector commands:",
            "",
            _rss_usage(),
            "",
            "Commands:",
            "  add <feed-or-page-url> [feed-or-page-url...]",
            "      Add RSS/Atom feeds or pages advertising one, then queue an RSS poll.",
            "  remove <feed-or-page-url> [feed-or-page-url...]",
            "      Remove feeds or pages advertising them and their local feed Folders.",
            "  import-opml <file.opml> [--all | --select <indexes>]",
            "      Import RSS/Atom feed URLs from an OPML file. Omit flags for checkbox selection.",
            "",
            "Options:",
            "  --all                   Import every feed from the OPML file.",
            "  --select <indexes>      Import selected feed numbers, e.g. 1,3-5.",
            "  --json                  Output command results as JSON.",
        ]
    )


def _format_rss_cli_result(result: dict[str, Any]) -> str:
    added = [str(item) for item in cast(list[object], result.get("added") or [])]
    removed = [str(item) for item in cast(list[object], result.get("removed") or [])]
    feed_urls = cast(list[object], result.get("feed_urls") or [])

    if "imported_feed_count" in result:
        imported_count = int(result.get("imported_feed_count") or 0)
        selected_count = int(result.get("selected_feed_count") or len(added))
        lines = [f"Imported {selected_count} of {imported_count} feed(s)."]
    elif "removed" in result:
        lines = [f"Removed {len(removed)} feed(s)."]
    else:
        lines = [f"Added {len(added)} feed(s)."]

    if added:
        lines.append("Added feeds:")
        lines.extend(f"  - {feed_url}" for feed_url in added[:20])
        if len(added) > 20:
            lines.append(f"  ... {len(added) - 20} more")
    if removed:
        lines.append("Removed feeds:")
        lines.extend(f"  - {feed_url}" for feed_url in removed[:20])
        if len(removed) > 20:
            lines.append(f"  ... {len(removed) - 20} more")
    lines.append(f"Total configured feeds: {len(feed_urls)}")
    poll = result.get("poll")
    if isinstance(poll, Mapping):
        status = str(poll.get("status") or "unknown").replace("_", " ")
        lines.append(f"Poll: {status}.")
    return "\n".join(lines)


def _parse_feed_url_args(args: list[str], *, command: str) -> list[str]:
    for arg in args:
        if arg.startswith("--"):
            raise ValueError(f"Unknown {command} option: {arg}")
    return args


def _parse_import_opml_args(args: list[str]) -> dict[str, Any]:
    path: str | None = None
    include_all = False
    selection: str | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--all":
            include_all = True
            index += 1
            continue
        if arg == "--select":
            if index + 1 >= len(args):
                raise ValueError("--select requires a value")
            selection = args[index + 1]
            index += 2
            continue
        if arg.startswith("--"):
            raise ValueError(f"Unknown import-opml option: {arg}")
        if path is not None:
            raise ValueError(_rss_usage())
        path = arg
        index += 1
    if path is None:
        raise ValueError(_rss_usage())
    if include_all and selection is not None:
        raise ValueError("Use either --all or --select, not both")
    return {
        "path": path,
        "include_all": include_all,
        "selection": selection,
    }


@dataclass(frozen=True)
class FeedAuthor:
    """An author declared by a feed or one of its entries."""

    user_id: str
    display_name: str | None
    email: str | None

    def to_person(self) -> PersonRecord:
        return PersonRecord(
            platform="rss",
            platform_user_id=self.user_id,
            display_name=self.display_name,
            canonical_email=self.email,
        )

    @property
    def label(self) -> str:
        return self.display_name or self.user_id


def _author_details(container: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return feedparser author dicts for a feed or entry, newest API first."""
    raw_authors = container.get("authors")
    if isinstance(raw_authors, list):
        details = [item for item in cast(list[Any], raw_authors) if isinstance(item, Mapping)]
        if details:
            return cast(list[Mapping[str, Any]], details)
    detail = container.get("author_detail")
    if isinstance(detail, Mapping):
        return [cast(Mapping[str, Any], detail)]
    name = container.get("author")
    if isinstance(name, str) and name.strip():
        return [{"name": name}]
    return []


def _parse_authors(container: Mapping[str, Any]) -> list[FeedAuthor]:
    """Map feedparser author metadata onto identity-bearing FeedAuthor records.

    RSS ``<author>`` carries an email address, Atom ``<author>`` a name plus
    optional email, and ``<dc:creator>`` a bare name; feedparser normalises all
    three into ``{"name": ..., "email": ...}`` dicts with either key optional.
    """
    authors: list[FeedAuthor] = []
    seen: set[str] = set()
    for detail in _author_details(container):
        name = _clean_author_field(detail.get("name"))
        email = _clean_author_field(detail.get("email"))
        if email is None and name is not None and "@" in name and " " not in name:
            name, email = None, name
        if email is not None:
            email = email.lower()
        user_id = email or name
        if user_id is None or user_id in seen:
            continue
        seen.add(user_id)
        authors.append(FeedAuthor(user_id=user_id, display_name=name, email=email))
    return authors


def _clean_author_field(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _entry_to_entity(
    feed_url: str,
    feed_entity_id: str,
    entry: dict[str, Any],
    authors: Sequence[FeedAuthor] = (),
) -> EntityRecord:
    link = normalise_article_url(str(entry.get("link") or "")) or ""
    external_id = str(entry.get("id") or entry.get("guid") or link or entry.get("title") or "")
    entity_id = f"entry/{_hash_ref(feed_url + ':' + external_id)}"
    title = str(entry.get("title") or link or "(untitled)")
    summary = _entry_text(entry)
    published = _parse_entry_datetime(entry.get("published") or entry.get("updated"))
    metadata: dict[str, str | int | float | bool | None] = {
        "feed_url": feed_url,
        "feed_entity_id": feed_entity_id,
        "link": link or None,
        "web_url": link or None,
        "author": authors[0].label if authors else None,
        "authors": ", ".join(author.label for author in authors) or None,
    }
    content_parts = [title]
    if summary:
        content_parts.append(summary)
    if link:
        content_parts.append(link)
    return EntityRecord(
        entity_type="Document",
        platform="rss",
        platform_entity_id=entity_id,
        title=title,
        content="\n\n".join(content_parts),
        source_created_at=published,
        source_updated_at=_parse_entry_datetime(entry.get("updated")),
        metadata=metadata,
    )


async def _fetch_entry_document(
    platform_entity_id: str,
    metadata: Mapping[str, object],
    *,
    fallback: EntityRecord | None = None,
) -> EntityRecord | EntityMetadataPatch:
    web_url = _metadata_str(metadata, "web_url")
    if web_url is None:
        raise ValueError(f"RSS document {platform_entity_id} has no web_url")
    existing = await get_backend().get_entity_by_platform("rss", platform_entity_id)
    http_existing = _http_existing_entity(existing, web_url)
    fetched = await _fetch_http_document(web_url, existing_entity=http_existing)
    if isinstance(fetched, EntityMetadataPatch):
        return EntityMetadataPatch(
            platform="rss",
            platform_entity_id=platform_entity_id,
            metadata=dict(fetched.metadata),
        )
    fetched_metadata = dict(fetched.metadata)
    rss_metadata: dict[str, str | int | float | bool | None] = {
        **_entity_record_metadata(metadata),
        **fetched_metadata,
        "web_url": str(fetched_metadata.get("web_url") or web_url),
        "link": _metadata_str(metadata, "link") or web_url,
    }
    return EntityRecord(
        entity_type="Document",
        platform="rss",
        platform_entity_id=platform_entity_id,
        title=fetched.title or (fallback.title if fallback else None),
        content=fetched.content or (fallback.content if fallback else None),
        source_created_at=fallback.source_created_at if fallback else None,
        source_updated_at=fetched.source_updated_at
        or (fallback.source_updated_at if fallback else None),
        metadata=rss_metadata,
    )


async def _hydrate_entry_document(
    entity: EntityRecord,
) -> EntityRecord | EntityMetadataPatch:
    if _metadata_str(entity.metadata, "web_url") is None:
        return entity
    try:
        return await _fetch_entry_document(
            entity.platform_entity_id,
            entity.metadata,
            fallback=entity,
        )
    except Exception as exc:
        logger.warning(
            "Skipping RSS article hydration for %s (%s: %s)",
            entity.platform_entity_id,
            type(exc).__name__,
            exc,
        )
        logger.debug("RSS article hydration failure", exc_info=True)
        return entity


def _http_existing_entity(
    existing: dict[str, Any] | None,
    web_url: str,
) -> dict[str, object] | None:
    if existing is None:
        return None
    http_existing = dict(existing)
    http_existing["platform_entity_id"] = web_url
    return http_existing


async def _fetch_http_document(
    url: str,
    *,
    existing_entity: dict[str, object] | None,
) -> EntityRecord | EntityMetadataPatch:
    module: Any = import_module("agentgraph_connector_web")
    result = await module.fetch_http_document(url, existing_entity=existing_entity)
    return cast(EntityRecord | EntityMetadataPatch, result)


def _entity_record_metadata(
    metadata: Mapping[str, object],
) -> dict[str, str | int | float | bool | None]:
    result: dict[str, str | int | float | bool | None] = {}
    for key, value in metadata.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
    return result


def _metadata_str(metadata: Mapping[str, object], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) and value else None


def _string_metadata(metadata: Mapping[str, object]) -> dict[str, str]:
    return {key: value for key, value in metadata.items() if isinstance(value, str)}


def normalise_article_url(url: str) -> str | None:
    """Canonicalise an RSS entry URL for exact observation matching."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    query_items = sorted(
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_query_key(key)
    )
    query = urlencode(query_items, doseq=True)
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, query, ""))


def derive_observation_url_patterns(links_by_feed: Mapping[str, list[str]]) -> list[str]:
    """Build bounded Chrome patterns from previously indexed article links."""
    patterns: list[str] = []
    seen: set[str] = set()
    for links in links_by_feed.values():
        candidates: dict[str, set[str]] = {}
        for raw_link in links:
            link = normalise_article_url(raw_link)
            if link is None:
                continue
            parsed = urlsplit(link)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            segments = [segment for segment in parsed.path.split("/") if segment]
            for depth in range(1, len(segments)):
                prefix = f"{origin}/{'/'.join(segments[:depth])}"
                candidates.setdefault(prefix, set()).add(link)
            candidates.setdefault(origin, set()).add(link)

        shared = [
            (prefix, urls)
            for prefix, urls in candidates.items()
            if len(urls) >= 2 and prefix.count("/") > 2
        ]
        useful = shared or [(prefix, urls) for prefix, urls in candidates.items() if len(urls) >= 1]
        useful.sort(key=lambda item: (item[0].count("/"), -len(item[1]), item[0]))
        selected_prefixes: list[str] = []
        for prefix, _urls in useful:
            if any(
                prefix.startswith(f"{selected}/") or prefix == selected
                for selected in selected_prefixes
            ):
                continue
            selected_prefixes.append(prefix)
            if len(selected_prefixes) == _MAX_OBSERVATION_PATTERNS_PER_FEED:
                break

        for prefix in selected_prefixes:
            pattern = f"{prefix}/*"
            if pattern not in seen:
                patterns.append(pattern)
                seen.add(pattern)
    return patterns


def _is_tracking_query_key(key: str) -> bool:
    return key.lower().startswith("utm_") or key.lower() in _TRACKING_QUERY_KEYS




def _entry_text(entry: dict[str, Any]) -> str:
    content = entry.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("value"):
            return str(first["value"])
    return str(entry.get("summary") or entry.get("description") or "")


def _parse_entry_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except Exception:
        return None


def _feed_id(feed_url: str) -> str:
    return _hash_ref(feed_url)


def _hash_ref(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
