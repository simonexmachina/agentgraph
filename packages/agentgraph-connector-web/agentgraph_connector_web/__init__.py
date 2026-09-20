"""Generic web connector."""

from __future__ import annotations

import hashlib
import html
import json
import re
import shlex
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import ClassVar, Literal, cast
from urllib.parse import urldefrag, urlparse
from xml.etree import ElementTree

import httpx

from agentgraph.connectors.base import (
    BaseConnector,
    ConnectorCommandEffects,
    EntityBatch,
    EntityRecord,
    EntityTypeDefinition,
    FetchPolicy,
    ResourceType,
    SourceReference,
)
from agentgraph.connectors.match_patterns import matches_pattern
from agentgraph_connector_web.config import (
    load_web_settings,
    observe_urls,
    remove_observed_urls,
)
from agentgraph_connector_web.http import fetch_http_resource

_STALE_AFTER = 24 * 60 * 60
_MAX_BYTES = 2_000_000
_MAX_REDIRECTS = 5
_RESPONSE_TOO_LARGE_PREFIX = "Response too large for web document"
_ACCEPT = (
    "text/markdown, text/html, application/json, text/plain, "
    "application/atom+xml, application/rss+xml, application/xml, text/xml, "
    "application/pdf;q=0.7, */*;q=0.1"
)


class UnsupportedFormatError(ValueError):
    """Raised when a URL returns content AgentGraph cannot store for LLM use."""


class WebConnector(BaseConnector):
    source = "web"
    entity_types = (
        EntityTypeDefinition(
            name="Document",
            resource_type="document",
            description="Web pages and files fetched over HTTP.",
        ),
    )
    fetch_policy = FetchPolicy(stale_after_seconds=_STALE_AFTER)
    is_generic_url_fallback = True
    url_patterns: ClassVar[list[str]] = []
    auth_description = "Generic web pages: HTML, Markdown, JSON, plain text, and XML/RSS/Atom documents fetched directly over HTTP."
    appears_in_auth_status = False

    def can_handle(self, url: str) -> bool:
        return self.resolve_url(url) is not None

    @classmethod
    def run_cli_command(cls, args: list[str]) -> dict[str, object]:
        if not args:
            raise ValueError(_web_usage())
        command, *rest = args
        if command == "observe":
            urls, remove = _parse_web_observe_args(rest)
            if remove:
                config, removed = remove_observed_urls(urls)
                return {
                    "status": "ok",
                    "source": cls.source,
                    "observation_urls": config.observation_urls,
                    "removed": removed,
                }
            config, observed = observe_urls(urls)
            return {
                "status": "ok",
                "source": cls.source,
                "observation_urls": config.observation_urls,
                "observed": observed,
            }
        if command == "list":
            if rest:
                raise ValueError(_web_usage())
            config = load_web_settings()
            return {
                "status": "ok",
                "source": cls.source,
                "observation_urls": config.observation_urls,
            }
        if command == "fetch":
            url, compact = _parse_web_fetch_args(rest)
            return {"status": "ok", "source": cls.source, "url": url, "compact": compact}
        raise ValueError(
            f"Unknown web connector command '{command}'. Available: observe, list, fetch"
        )

    @classmethod
    def cli_help(cls) -> str:
        return _web_usage()

    @classmethod
    def format_cli_result(cls, result: dict[str, object]) -> str:
        fetched = result.get("fetched")
        if isinstance(fetched, list) and fetched and isinstance(fetched[0], dict):
            item = cast(dict[str, object], fetched[0])
            return (
                f"Fetched {item.get('resource_id')}: {item.get('entities', 0)} entities, "
                f"{item.get('metadata_patches', 0)} metadata patches, "
                f"{item.get('persons', 0)} persons, {item.get('edges', 0)} edges"
            )
        raw_urls = result.get("observation_urls", [])
        urls: list[str] = []
        if isinstance(raw_urls, list):
            urls = [url for url in cast(list[object], raw_urls) if isinstance(url, str)]
        return "\n".join([f"Observed web URLs ({len(urls)}):", *[f"  {url}" for url in urls]])

    @classmethod
    def command_effects(
        cls,
        args: list[str],
        result: dict[str, object],
    ) -> ConnectorCommandEffects:
        if not args or args[0] != "fetch":
            return ConnectorCommandEffects()
        url = result.get("url")
        if not isinstance(url, str):
            raise ValueError(_web_fetch_usage())
        fetch_meta = {"compact_html": "true"} if result.get("compact") is True else None
        return ConnectorCommandEffects(
            fetch_references=(
                SourceReference(
                    source=cls.source,
                    resource_type="document",
                    resource_id=url,
                    fetch_meta=fetch_meta,
                ),
            )
        )

    def resolve_url(self, url: str) -> SourceReference | None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        return SourceReference(
            source=self.source,
            resource_type="document",
            resource_id=_canonical_url(url),
        )

    def url_entity_reference(self, url: str) -> SourceReference | None:
        return self.resolve_url(url)

    async def resolve_observation_url(
        self,
        url: str,
        meta: dict[str, str] | None = None,
    ) -> SourceReference | None:
        _ = meta
        normalized = _canonical_url(url)
        for rule in load_web_settings().observation_urls:
            if matches_pattern(normalized, rule):
                return SourceReference(source=self.source, resource_type="document", resource_id=normalized)
        return None

    async def observation_url_patterns(self) -> list[str]:
        return load_web_settings().observation_urls

    async def fetch(
        self,
        resource_type: ResourceType,
        resource_id: str,
        meta: dict[str, str] | None = None,
        account_id: str | None = None,
    ) -> EntityBatch:
        _ = (resource_type, account_id)
        ref = self.resolve_url(resource_id)
        if ref is None:
            raise ValueError("Web connector only supports http:// and https:// URLs")

        result = await _fetch_web_entity(
            ref.resource_id,
            compact_html=(meta or {}).get("compact_html") == "true",
        )
        return EntityBatch(entities=[result])

    def entity_url(self, platform_entity_id: str) -> str | None:
        return platform_entity_id

    def fetch_error_hint(
        self,
        resource_id: str,
        error: Exception,
        audience: Literal["cli", "mcp"],
    ) -> str | None:
        if not isinstance(error, ValueError) or not str(error).startswith(_RESPONSE_TOO_LARGE_PREFIX):
            return None
        if audience == "cli":
            return f"Try: agentgraph connector web fetch {shlex.quote(resource_id)} --compact"
        return (
            'Try: run_connector_command_tool("web", '
            f'["fetch", {json.dumps(resource_id)}, "--compact"])'
        )


async def _fetch_web_entity(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    compact_html: bool = False,
) -> EntityRecord:
    response = await fetch_http_resource(
        url,
        headers={
            "Accept": _ACCEPT,
            "User-Agent": "AgentGraph/0.1",
        },
        max_bytes=_MAX_BYTES,
        too_large_message=f"{_RESPONSE_TOO_LARGE_PREFIX}: limit is {_MAX_BYTES} bytes",
        timeout=httpx.Timeout(10.0, connect=5.0),
        max_redirects=_MAX_REDIRECTS,
        client=client,
        compact_html=compact_html,
    )
    if response.status_code == 304:
        raise ValueError(f"Received unexpected 304 response for {url}")

    content_type = _normalise_content_type(response.headers.get("content-type", ""))
    final_url = _canonical_url(response.url)
    parsed = _parse_content(response.content, content_type, final_url)
    content_sha256 = hashlib.sha256(response.content).hexdigest()
    entity = EntityRecord(
        entity_type="Document",
        platform="web",
        platform_entity_id=final_url,
        title=parsed.title,
        content=parsed.content,
        source_updated_at=_parse_http_date(response.headers.get("last-modified")),
        metadata={
            "url": url,
            "final_url": final_url,
            "web_url": final_url,
            "content_type": content_type,
            "status_code": response.status_code,
            "fetched_at": datetime.now(UTC).isoformat(),
            "content_sha256": content_sha256,
            **_response_cache_metadata(response.headers),
        },
    )
    return entity


async def fetch_http_document(
    url: str,
) -> EntityRecord:
    """Fetch an HTTP-backed Document without reading stored entity state."""
    return await _fetch_web_entity(url)


def _response_cache_metadata(headers: Mapping[str, str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    etag = headers.get("etag")
    if etag:
        metadata["http_etag"] = etag
    last_modified = headers.get("last-modified")
    if last_modified:
        metadata["http_last_modified"] = last_modified
    return metadata


def _parse_http_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class _ParsedContent:
    def __init__(self, *, title: str, content: str) -> None:
        self.title = title
        self.content = content


def _parse_content(body: bytes, content_type: str, url: str) -> _ParsedContent:
    text = _decode_text(body)
    if _is_html(content_type):
        return _parse_html(text, url)
    if _is_markdown(content_type, url):
        return _parse_markdown(text, url)
    if _is_json(content_type):
        return _parse_json(text, url)
    if _is_plain_text(content_type):
        return _ParsedContent(title=_title_from_url(url), content=text.strip())
    if _is_xml(content_type):
        return _parse_xml(text, url)
    raise UnsupportedFormatError(
        f"Unsupported content type for web bookmark: {content_type or 'unknown'}"
    )


def _decode_text(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")


def _normalise_content_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _is_html(content_type: str) -> bool:
    return content_type in {"text/html", "application/xhtml+xml"}


def _is_markdown(content_type: str, url: str) -> bool:
    return content_type in {"text/markdown", "text/x-markdown"} or urlparse(url).path.endswith(
        (".md", ".markdown")
    )


def _is_json(content_type: str) -> bool:
    return content_type == "application/json" or content_type.endswith("+json")


def _is_plain_text(content_type: str) -> bool:
    return content_type == "text/plain"


def _is_xml(content_type: str) -> bool:
    return content_type in {
        "application/atom+xml",
        "application/rss+xml",
        "application/xml",
        "text/xml",
    } or content_type.endswith("+xml")


class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        self._tag_stack.append(tag)
        if tag in {"p", "br", "div", "section", "article", "li", "h1", "h2", "h3"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()
        elif tag in self._tag_stack:
            self._tag_stack.remove(tag)
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if any(tag in {"script", "style", "noscript"} for tag in self._tag_stack):
            return
        if self._tag_stack and self._tag_stack[-1] == "title":
            self.title_parts.append(data)
            return
        self.text_parts.append(data)


def _parse_html(text: str, url: str) -> _ParsedContent:
    parser = _TextHTMLParser()
    parser.feed(text)
    title = _clean_text(" ".join(parser.title_parts)) or _title_from_url(url)
    return _ParsedContent(title=title, content=text.strip())


def _parse_markdown(text: str, url: str) -> _ParsedContent:
    stripped = text.strip()
    title = _title_from_url(url)
    for line in stripped.splitlines():
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            title = match.group(1).strip()
            break
    return _ParsedContent(title=title, content=stripped)


def _parse_json(text: str, url: str) -> _ParsedContent:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON response for web bookmark: {exc.msg}") from exc
    title = _json_title(value) or _title_from_url(url)
    return _ParsedContent(title=title, content=text.strip())


def _json_title(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    items = cast(dict[str, object], value)
    for key in ("title", "name", "headline"):
        item = items.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return None


def _parse_xml(text: str, url: str) -> _ParsedContent:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ValueError(f"Invalid XML response for web bookmark: {exc}") from exc
    title = _xml_title(root) or _title_from_url(url)
    return _ParsedContent(title=title, content=text.strip())


def _xml_title(root: ElementTree.Element) -> str | None:
    for element in root.iter():
        if _strip_namespace(element.tag).lower() == "title":
            title = _clean_text(element.text or "")
            if title:
                return title
    return None


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _title_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path:
        return parsed.netloc
    return html.unescape(path.rsplit("/", 1)[-1]) or parsed.netloc


def _canonical_url(url: str) -> str:
    return urldefrag(url)[0]


def _clean_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _web_usage() -> str:
    return (
        "Usage: agentgraph connector web observe <url> [url...] [--remove] | list\n"
        "   or: agentgraph connector web fetch <url> [--compact]"
    )


def _web_fetch_usage() -> str:
    return "Usage: agentgraph connector web fetch <url> [--compact]"


def _parse_web_observe_args(args: list[str]) -> tuple[list[str], bool]:
    remove = False
    urls: list[str] = []
    for arg in args:
        if arg == "--remove":
            if remove:
                raise ValueError(_web_usage())
            remove = True
        elif arg.startswith("-"):
            raise ValueError(_web_usage())
        else:
            urls.append(arg)
    if not urls:
        raise ValueError(_web_usage())
    return urls, remove


def _parse_web_fetch_args(args: list[str]) -> tuple[str, bool]:
    compact = False
    urls: list[str] = []
    for arg in args:
        if arg == "--compact":
            if compact:
                raise ValueError(_web_fetch_usage())
            compact = True
        elif arg.startswith("-"):
            raise ValueError(_web_fetch_usage())
        else:
            urls.append(arg)
    if len(urls) != 1:
        raise ValueError(_web_fetch_usage())
    parsed = urlparse(urls[0])
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Web fetch URL must use http:// or https://")
    return _canonical_url(urls[0]), compact
