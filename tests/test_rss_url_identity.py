"""RSS URL identities, provenance, and shared resolution against real storage."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from agentgraph_connector_rss import RssConnector, _fetch_feed, normalise_article_url
from agentgraph_connector_web import WebConnector

from agentgraph.backends.sqlite.backend import SQLiteBackend
from agentgraph.connectors.base import EntityBatch, EntityRecord
from agentgraph.core.context import clear_backend, set_backend
from agentgraph.graph.query import get_entity, get_entity_by_url
from agentgraph.mcp.models import entity_from_record
from agentgraph.query_client import HttpQueryClient, InProcessQueryClient
from agentgraph.server.app import app
from agentgraph.server.router import classify_observation_url

pytestmark = pytest.mark.integration


@pytest.fixture
async def backend(tmp_path: Path) -> AsyncIterator[SQLiteBackend]:
    storage = SQLiteBackend(str(tmp_path / "rss.db"), vector_mode="bm25-only")
    await storage.initialize()
    set_backend(storage)
    try:
        yield storage
    finally:
        clear_backend()
        await storage.close()


async def test_redirected_entries_deduplicate_and_keep_all_feed_relationships(backend: SQLiteBackend) -> None:
    feed_one, feed_two = "https://example.com/feed.xml", "https://example.com/other.xml"
    final = "https://example.com/article"
    parsed = SimpleNamespace(feed={"title": "Feed"}, entries=[
        {"id": "first", "link": "https://example.com/redirect"},
        {"guid": "second", "link": "https://example.com/another-redirect"},
    ])
    fetched = EntityRecord(entity_type="Document", platform="web", platform_entity_id=final + "/?utm_source=feed#top",
                           title="Article", content="Full content", metadata={"final_url": final})
    with patch("agentgraph_connector_rss._parse_feed", AsyncMock(return_value=parsed)), patch(
        "agentgraph_connector_rss._fetch_http_document", AsyncMock(return_value=fetched)
    ) as fetch:
        first = await _fetch_feed(feed_one, skip_existing_articles=True)
        await backend.upsert_batch(first, {}, {})
        article = await backend.get_entity_by_platform("rss", final)
        assert article is not None
        await backend.set_entity_bookmarked(str(article["id"]), True)
        again = await _fetch_feed(feed_one, skip_existing_articles=True)
        assert fetch.await_count == 2
        assert len(again.entities) == 1
        await backend.upsert_batch(again, {}, {})
        other = await _fetch_feed(feed_two, skip_existing_articles=True)
        await backend.upsert_batch(other, {}, {})
        edges = await backend.get_edges(str(article["id"]), "posted_in", "out")
        assert len(edges) == 2
        assert all(edge["properties"]["entry_ids"] == ["id:first", "id:second"] for edge in edges)
        stored = await backend.get_entity_by_platform("rss", final)
        assert stored is not None and stored["bookmarked"]
        assert len(await backend.list_entities(["Document"], "rss", None, 100)) == 1


async def test_missing_guid_and_failed_fetch_retry(backend: SQLiteBackend) -> None:
    link = "https://example.com/original"
    parsed = SimpleNamespace(feed={"title": "Feed"}, entries=[
        {"title": "Same title", "link": link}, {"title": "Same title"},
    ])
    fetched = EntityRecord(entity_type="Document", platform="web", platform_entity_id="https://example.com/final")
    with patch("agentgraph_connector_rss._parse_feed", AsyncMock(return_value=parsed)), patch(
        "agentgraph_connector_rss._fetch_http_document", AsyncMock(side_effect=[RuntimeError("offline"), fetched])
    ) as fetch:
        failed = await _fetch_feed("https://example.com/feed", skip_existing_articles=True)
        await backend.upsert_batch(failed, {}, {})
        assert failed.edges == []
        assert len(failed.entities) == 1
        retry = await _fetch_feed("https://example.com/feed", skip_existing_articles=True)
        await backend.upsert_batch(retry, {}, {})
        assert retry.edges[0].properties == {"entry_ids": ["url:" + link]}
        await _fetch_feed("https://example.com/feed", skip_existing_articles=True)
        assert fetch.await_count == 2


async def test_url_lookup_parity_and_bookmarks_without_metadata_scans(backend: SQLiteBackend) -> None:
    url = "https://example.com/article"
    await backend.upsert_batch(EntityBatch(entities=[
        EntityRecord(entity_type="Document", platform=platform, platform_entity_id=url, title=platform,
                     metadata={"web_url": url, "url": "https://example.com/original"})
        for platform in ("rss", "web")
    ]), {}, {})
    connectors = [WebConnector(), RssConnector()]
    with patch("agentgraph.connectors.registry.get_all_connectors", return_value=connectors), patch.object(
        backend, "query_by_filter", AsyncMock(side_effect=AssertionError("metadata scan"))
    ), patch.object(backend, "search_entities", AsyncMock(side_effect=AssertionError("search scan"))):
        entity = await get_entity_by_url(url)
        assert entity is not None and entity["platform"] == "rss"
        assert await get_entity_by_url("https://example.com/original") is None
        for platform in ("rss", "web"):
            for ref in (f"{platform}/{url}", f"{platform}/document/{url}"):
                resolved = await get_entity(ref)
                assert resolved is not None and resolved["platform"] == platform
        local = await InProcessQueryClient().get_entity(url, False)
        remote = await HttpQueryClient("http://test", transport=httpx.ASGITransport(app=app)).get_entity(url, False)
        assert local == remote
        assert entity_from_record(entity).id == entity["id"]
        ref = await classify_observation_url(url)
        assert ref is not None and ref.source == "rss" and ref.resource_id == url
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            marked = await client.post("/api/extension/bookmark", json={"url": url, "bookmarked": True})
            assert marked.status_code == 200
            result = await client.post("/api/extension/page", json={"url": url})
            assert result.json()["entity"]["bookmarked"] is True
            assert result.json()["entity"]["id"] == entity["id"]


def test_rss_url_normalization_is_idempotent() -> None:
    raw = "https://EXAMPLE.com/article/?b=2&utm_source=feed&a=1#section"
    expected = "https://example.com/article?a=1&b=2"
    assert normalise_article_url(raw) == expected
    assert normalise_article_url(expected) == expected
    ref = RssConnector().url_entity_reference(raw)
    assert ref is not None and ref.resource_id == expected
