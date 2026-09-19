"""Tests for the generic web connector."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
from pathlib import Path
from unittest.mock import AsyncMock, patch

import agentgraph_connector_web
import httpx
import pytest
from agentgraph_connector_web import UnsupportedFormatError, WebConnector
from agentgraph_connector_web.http import (
    HttpFetchResult,
    _impersonation_headers,
    fetch_http_resource,
)

from agentgraph.connectors.base import EntityRecord, SourceReference


def test_web_connector_resolves_http_urls() -> None:
    connector = WebConnector()

    assert connector.resolve_url("https://example.com/page#section") == SourceReference(
        source="web",
        resource_type="document",
        resource_id="https://example.com/page",
    )
    assert connector.resolve_url("ftp://example.com/file") is None


def test_web_config_round_trips_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setattr("agentgraph.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("agentgraph.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("agentgraph.config.CONFIG_YAML_FILE", tmp_path / "config.yaml")

    from agentgraph_connector_web.config import WebConfig, load_web_settings, save_web_config

    save_web_config(WebConfig(observation_urls=["http://localhost:3000/*", "https://example.com/page#part"]))

    rendered = config_file.read_text()
    assert "[connectors.web]" in rendered
    assert not rendered.startswith("\n")
    assert 'observation_urls = [\n  "http://localhost:3000/*",\n  "https://example.com/page#part",\n]' in rendered
    assert load_web_settings().observation_urls == [
        "http://localhost:3000/*",
        "https://example.com/page",
    ]


def test_web_config_uses_env_directory_after_config_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "custom-config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        '[connectors.web]\nobservation_urls = ["https://custom.example/*"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTGRAPH_CONFIG_DIR", str(config_dir))

    from agentgraph_connector_web.config import load_web_settings

    assert load_web_settings().observation_urls == ["https://custom.example/*"]


def test_web_config_round_trips_yaml_and_preserves_other_connectors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("connectors:\n  rss:\n    feed_urls: [https://example.com/feed.xml]\n")
    monkeypatch.setattr("agentgraph.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("agentgraph.config.CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr("agentgraph.config.CONFIG_YAML_FILE", config_yaml)

    from agentgraph_connector_web.config import WebConfig, load_web_settings, save_web_config

    save_web_config(WebConfig(observation_urls=["http://localhost:3000/content/*"]))

    content = config_yaml.read_text()
    assert "rss:" in content
    assert load_web_settings().observation_urls == ["http://localhost:3000/content/*"]


def test_web_cli_observe_and_remove(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agentgraph.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("agentgraph.config.CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr("agentgraph.config.CONFIG_YAML_FILE", tmp_path / "config.yaml")

    observed = WebConnector.run_cli_command(
        ["observe", "http://localhost:3000/page#section", "http://localhost:3000/*"]
    )
    assert observed["observed"] == ["http://localhost:3000/page", "http://localhost:3000/*"]
    assert WebConnector.run_cli_command(["list"])["observation_urls"] == [
        "http://localhost:3000/page",
        "http://localhost:3000/*",
    ]
    removed = WebConnector.run_cli_command(
        ["observe", "http://localhost:3000/page", "--remove"]
    )
    assert removed["removed"] == ["http://localhost:3000/page"]


@pytest.mark.parametrize(
    "args",
    [
        ["observe"],
        ["observe", "--remove", "--remove", "https://example.com/page"],
        ["observe", "--unknown", "https://example.com/page"],
        ["watch", "https://example.com/page"],
        ["unwatch", "https://example.com/page"],
    ],
)
def test_web_cli_observe_rejects_invalid_arguments(args: list[str]) -> None:
    with pytest.raises(ValueError):
        WebConnector.run_cli_command(args)



def test_web_cli_fetch_parses_compact_option() -> None:
    result = WebConnector.run_cli_command(
        ["fetch", "https://example.com/page#section", "--compact"]
    )

    assert result == {
        "status": "ok",
        "source": "web",
        "url": "https://example.com/page",
        "compact": True,
    }
    assert WebConnector.command_effects(
        ["fetch", "https://example.com/page#section", "--compact"], result
    ).fetch_references == (
        SourceReference(
            source="web",
            resource_type="document",
            resource_id="https://example.com/page",
            fetch_meta={"compact_html": "true"},
        ),
    )


def test_web_cli_fetch_without_compaction_has_no_fetch_metadata() -> None:
    result = WebConnector.run_cli_command(["fetch", "https://example.com/page"])

    assert WebConnector.command_effects(
        ["fetch", "https://example.com/page"], result
    ).fetch_references[0].fetch_meta is None


def test_web_cli_fetch_formats_metadata_patch_count() -> None:
    assert WebConnector.format_cli_result(
        {
            "fetched": [
                {
                    "resource_id": "https://example.com/page",
                    "entities": 0,
                    "metadata_patches": 1,
                    "persons": 0,
                    "edges": 0,
                }
            ]
        }
    ) == (
        "Fetched https://example.com/page: 0 entities, 1 metadata patches, "
        "0 persons, 0 edges"
    )


@pytest.mark.parametrize(
    "args",
    [
        ["fetch"],
        ["fetch", "not-a-url"],
        ["fetch", "https://example.com", "https://example.org"],
        ["fetch", "https://example.com", "--unknown"],
        ["fetch", "https://example.com", "--compact", "--compact"],
    ],
)
def test_web_cli_fetch_rejects_invalid_arguments(args: list[str]) -> None:
    with pytest.raises(ValueError):
        WebConnector.run_cli_command(args)


@pytest.mark.asyncio
async def test_web_fetch_uses_command_compaction_metadata() -> None:
    entity = agentgraph_connector_web.EntityRecord(
        entity_type="Document",
        platform="web",
        platform_entity_id="https://example.com/page",
    )
    with patch(
        "agentgraph_connector_web._fetch_web_entity",
        new=AsyncMock(return_value=entity),
    ) as fetch_web_entity:
        batch = await WebConnector().fetch(
            "document",
            "https://example.com/page",
            meta={"compact_html": "true"},
        )

    fetch_web_entity.assert_awaited_once_with(
        "https://example.com/page",
        compact_html=True,
    )
    assert batch.entities == [entity]


@pytest.mark.asyncio
async def test_web_fetch_does_not_query_for_an_existing_document() -> None:
    result = EntityRecord(
        entity_type="Document",
        platform="web",
        platform_entity_id="https://example.com/final",
    )
    with patch(
        "agentgraph_connector_web._fetch_web_entity",
        new=AsyncMock(return_value=result),
    ) as fetch_web_entity:
        batch = await WebConnector().fetch(
            "document",
            "https://example.com/requested",
        )

    fetch_web_entity.assert_awaited_once_with(
        "https://example.com/requested",
        compact_html=False,
    )
    assert batch.entities == [result]


@pytest.mark.asyncio
async def test_web_observation_resolution_enforces_exact_and_prefix_rules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agentgraph.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("agentgraph.config.CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr("agentgraph.config.CONFIG_YAML_FILE", tmp_path / "config.yaml")

    from agentgraph_connector_web.config import WebConfig, save_web_config

    save_web_config(WebConfig(observation_urls=["http://localhost:3000/page", "http://localhost:3000/content/*"]))
    connector = WebConnector()

    assert await connector.resolve_observation_url("http://localhost:3000/page#section") == SourceReference(
        source="web", resource_type="document", resource_id="http://localhost:3000/page"
    )
    assert await connector.resolve_observation_url("http://localhost:3000/page/child") is None
    assert await connector.resolve_observation_url("http://localhost:3000/content/research.md") is not None


def test_parse_html_extracts_title_and_preserves_source() -> None:
    source = (
        b"<html><head><title>Hello</title><script>x()</script></head>"
        b"<body><h1>Hi</h1><p>World</p></body></html>"
    )
    parsed = agentgraph_connector_web._parse_content(  # noqa: SLF001
        source,
        "text/html",
        "https://example.com/hello",
    )

    assert parsed.title == "Hello"
    assert parsed.content == source.decode()


def test_parse_markdown_uses_first_heading() -> None:
    parsed = agentgraph_connector_web._parse_content(  # noqa: SLF001
        b"# Notes\n\nBody",
        "text/markdown",
        "https://example.com/notes.md",
    )

    assert parsed.title == "Notes"
    assert parsed.content == "# Notes\n\nBody"


def test_parse_json_uses_title_key() -> None:
    source = b'{"title":"API","ok":true}'
    parsed = agentgraph_connector_web._parse_content(  # noqa: SLF001
        source,
        "application/json",
        "https://example.com/api",
    )

    assert parsed.title == "API"
    assert parsed.content == source.decode()


def test_parse_plain_text() -> None:
    parsed = agentgraph_connector_web._parse_content(  # noqa: SLF001
        b"plain notes",
        "text/plain",
        "https://example.com/plain.txt",
    )

    assert parsed.title == "plain.txt"
    assert parsed.content == "plain notes"


def test_parse_xml_uses_first_title() -> None:
    source = b"<rss><channel><title>Feed</title><item><title>Entry</title></item></channel></rss>"
    parsed = agentgraph_connector_web._parse_content(  # noqa: SLF001
        source,
        "application/rss+xml",
        "https://example.com/feed.xml",
    )

    assert parsed.title == "Feed"
    assert parsed.content == source.decode()


def test_parse_unsupported_content_type_raises_clear_error() -> None:
    with pytest.raises(UnsupportedFormatError, match="Unsupported content type"):
        agentgraph_connector_web._parse_content(  # noqa: SLF001
            b"%PDF",
            "application/pdf",
            "https://example.com/file.pdf",
        )


@pytest.mark.asyncio
async def test_fetch_http_resource_retries_cloudflare_challenge() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"cf-mitigated": "challenge"},
            request=request,
        )

    fallback = HttpFetchResult(
        url="https://example.com/page",
        status_code=200,
        headers={"content-type": "text/html"},
        content=b"<title>Fetched</title>",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with patch(
            "agentgraph_connector_web.http._fetch_with_impersonation",
            new=AsyncMock(return_value=fallback),
        ) as fetch_fallback:
            result = await fetch_http_resource(
                "https://example.com/page",
                headers={"Accept": "text/html", "User-Agent": "AgentGraph/0.1"},
                max_bytes=1_000,
                too_large_message="too large",
                client=client,
            )

    assert result is fallback
    fetch_fallback.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_http_resource_does_not_retry_an_ordinary_forbidden_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with patch(
            "agentgraph_connector_web.http._fetch_with_impersonation",
            new=AsyncMock(),
        ) as fetch_fallback:
            with pytest.raises(httpx.HTTPStatusError):
                await fetch_http_resource(
                    "https://example.com/page",
                    headers={"Accept": "text/html"},
                    max_bytes=1_000,
                    too_large_message="too large",
                    client=client,
                )

    fetch_fallback.assert_not_awaited()


def test_impersonation_headers_preserve_request_headers_without_agent_user_agent() -> None:
    assert _impersonation_headers(  # noqa: SLF001
        {"Accept": "text/html", "User-Agent": "AgentGraph/0.1", "If-None-Match": '"abc"'}
    ) == {"Accept": "text/html", "If-None-Match": '"abc"'}


@pytest.mark.asyncio
async def test_fetch_web_entity_streams_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "text/html",
                "etag": '"fresh"',
                "last-modified": "Mon, 08 Jun 2026 01:23:45 GMT",
            },
            content=b"<title>Fetched</title><p>Body</p>",
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as client:
        entity = await agentgraph_connector_web._fetch_web_entity(  # noqa: SLF001
            "https://example.com/page",
            client=client,
        )

    assert isinstance(entity, EntityRecord)
    assert entity.platform == "web"
    assert entity.platform_entity_id == "https://example.com/page"
    assert entity.title == "Fetched"
    assert entity.content == "<title>Fetched</title><p>Body</p>"
    assert entity.metadata["content_type"] == "text/html"
    assert entity.metadata["web_url"] == "https://example.com/page"
    assert entity.metadata["http_etag"] == '"fresh"'
    assert entity.metadata["http_last_modified"] == "Mon, 08 Jun 2026 01:23:45 GMT"


@pytest.mark.asyncio
async def test_fetch_web_entity_does_not_send_conditional_headers() -> None:
    body = b"<title>Cached title</title><p>Cached body</p>"

    def handler(request: httpx.Request) -> httpx.Response:
        assert "if-none-match" not in request.headers
        assert "if-modified-since" not in request.headers
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "etag": '"refreshed"'},
            content=body,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await agentgraph_connector_web._fetch_web_entity(  # noqa: SLF001
            "https://example.com/page",
            client=client,
        )

    assert isinstance(result, EntityRecord)
    assert result.platform_entity_id == "https://example.com/page"
    assert result.metadata["status_code"] == 200
    assert result.metadata["http_etag"] == '"refreshed"'


@pytest.mark.asyncio
async def test_fetch_web_entity_rejects_unexpected_304_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            304,
            headers={
                "etag": '"cached"',
                "last-modified": "Sun, 07 Jun 2026 12:00:00 GMT",
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="unexpected 304"):
            await agentgraph_connector_web._fetch_web_entity(  # noqa: SLF001
                "https://example.com/page",
                client=client,
            )


@pytest.mark.asyncio
async def test_fetch_web_entity_caps_response_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agentgraph_connector_web, "_MAX_BYTES", 5)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"too much text",
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="Response too large for web document"):
            await agentgraph_connector_web._fetch_web_entity(  # noqa: SLF001
                "https://example.com/large",
                client=client,
            )


def test_web_fetch_error_hint_only_handles_size_limit() -> None:
    connector = WebConnector()
    size_error = ValueError("Response too large for web document: limit is 2000000 bytes")

    assert connector.fetch_error_hint("https://example.com/page", size_error, "cli") == (
        "Try: agentgraph connector web fetch https://example.com/page --compact"
    )
    assert connector.fetch_error_hint("https://example.com/page", size_error, "mcp") == (
        'Try: run_connector_command_tool("web", '
        '["fetch", "https://example.com/page", "--compact"])'
    )
    assert connector.fetch_error_hint("https://example.com/page", ValueError("not found"), "cli") is None


@pytest.mark.asyncio
async def test_fetch_web_entity_compacts_html_before_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agentgraph_connector_web, "_MAX_BYTES", 64)
    source = b"<html><head><style>" + (b"x" * 200) + b"</style></head><body><p>Keep me</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=source,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        entity = await agentgraph_connector_web._fetch_web_entity(  # noqa: SLF001
            "https://example.com/compact",
            client=client,
            compact_html=True,
        )

    assert isinstance(entity, EntityRecord)
    assert "<style>" not in (entity.content or "")
    assert "Keep me" in (entity.content or "")
