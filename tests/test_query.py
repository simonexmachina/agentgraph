"""Unit tests for the graph query layer and MCP tools (mocked backend)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from mcp.types import CallToolResult

from agentgraph.connectors.base import ConnectorCommandEffects, EntityReference, SourceReference
from agentgraph.core.context import set_backend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mcp_content(result: CallToolResult) -> dict[str, Any]:
    assert isinstance(result, CallToolResult)
    assert isinstance(result.structuredContent, dict)
    return result.structuredContent


def _mcp_data(result: CallToolResult) -> dict[str, Any]:
    content = _mcp_content(result)
    assert result.isError is False
    assert content["status"] == "ok"
    data = content["data"]
    assert isinstance(data, dict)
    return cast(dict[str, Any], data)


def _mcp_error(result: CallToolResult) -> dict[str, Any]:
    content = _mcp_content(result)
    assert result.isError is True
    assert content["status"] == "error"
    return content


def _entity(
    *,
    entity_type: str = "Document",
    platform: str = "gdocs",
    title: str = "Test Doc",
    content: str = "some content",
    score: float | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": str(uuid4()),
        "entity_type": entity_type,
        "platform": platform,
        "platform_entity_id": "pe-" + str(uuid4())[:8],
        "title": title,
        "content": content,
        "metadata": {},
        "created_at": None,
        "updated_at": None,
        "bookmarked": False,
    }
    if score is not None:
        base["score"] = score
    return base


def _edge(
    *,
    edge_type: str = "authored",
    source_entity_id: str | None = None,
    target_entity_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "edge_type": edge_type,
        "platform": "gdocs",
        "properties": {},
        "source_entity_id": source_entity_id,
        "target_entity_id": target_entity_id,
        "source_ref": None,
        "target_ref": None,
    }


def _mock_backend(**method_overrides: Any) -> Any:
    """Build a mock StorageBackend with sensible async defaults."""
    backend = MagicMock()
    defaults = {
        "search_entities": AsyncMock(return_value=[]),
        "get_entity_by_id": AsyncMock(return_value=None),
        "get_entities_by_id_prefix": AsyncMock(return_value=[]),
        "get_entity_by_platform": AsyncMock(return_value=None),
        "get_edges": AsyncMock(return_value=[]),
        "get_edges_for_entities": AsyncMock(return_value=[]),
        "traverse_graph": AsyncMock(return_value={"nodes": [], "edges": []}),
        "query_by_filter": AsyncMock(return_value=[]),
        "list_entities": AsyncMock(return_value=[]),
        "get_platform_last_synced_at": AsyncMock(return_value=None),
        "get_platforms_last_synced_at": AsyncMock(return_value={}),
        "set_entity_bookmarked": AsyncMock(return_value=_entity(title="Bookmarked Doc")),
        "delete_entity": AsyncMock(return_value=_entity(title="Deleted Doc")),
        "delete_entities": AsyncMock(return_value=[]),
    }
    for name, value in {**defaults, **method_overrides}.items():
        setattr(backend, name, value)
    return backend


# ---------------------------------------------------------------------------
# search_entities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_entities_returns_results() -> None:
    from agentgraph.graph.query import search_entities

    expected = [_entity(score=0.9), _entity(score=0.7)]
    backend = _mock_backend(search_entities=AsyncMock(return_value=expected))
    set_backend(backend)

    with patch("agentgraph.graph.embeddings.encode_query", return_value=[0.1] * 384):
        results = await search_entities("test query", limit=5)

    assert len(results) == 2
    assert results[0]["entity_type"] == "Document"


@pytest.mark.asyncio
async def test_search_entities_empty_results() -> None:
    from agentgraph.graph.query import search_entities

    backend = _mock_backend(search_entities=AsyncMock(return_value=[]))
    set_backend(backend)

    with patch("agentgraph.graph.embeddings.encode_query", return_value=[0.0] * 384):
        results = await search_entities("empty query")

    assert results == []


@pytest.mark.asyncio
async def test_search_entities_passes_platform_to_backend() -> None:
    from agentgraph.graph.query import search_entities

    mock_search = AsyncMock(return_value=[])
    backend = _mock_backend(search_entities=mock_search)
    set_backend(backend)

    with patch("agentgraph.graph.embeddings.encode_query", return_value=[0.1] * 384):
        await search_entities("discord stuff", platform="discord")

    mock_search.assert_called_once()
    _, kwargs = mock_search.call_args
    assert kwargs.get("platform") == "discord"


@pytest.mark.asyncio
async def test_search_entities_platform_none_by_default() -> None:
    from agentgraph.graph.query import search_entities

    mock_search = AsyncMock(return_value=[])
    backend = _mock_backend(search_entities=mock_search)
    set_backend(backend)

    with patch("agentgraph.graph.embeddings.encode_query", return_value=[0.1] * 384):
        await search_entities("anything")

    mock_search.assert_called_once()
    _, kwargs = mock_search.call_args
    assert kwargs.get("platform") is None


@pytest.mark.asyncio
async def test_search_entities_offloads_query_embedding() -> None:
    from agentgraph.graph.query import clear_query_embedding_cache, search_entities

    backend = _mock_backend(search_entities=AsyncMock(return_value=[]))
    set_backend(backend)
    calls: list[tuple[Any, tuple[Any, ...]]] = []
    clear_query_embedding_cache()

    async def fake_to_thread(func: Any, *args: Any) -> Any:
        calls.append((func, args))
        return func(*args)

    with (
        patch("agentgraph.graph.embeddings.encode_query", return_value=[0.1] * 384),
        patch("agentgraph.graph.query.asyncio.to_thread", new=fake_to_thread),
    ):
        await search_entities("anything")

    assert len(calls) == 1
    assert calls[0][1] == ("anything",)


@pytest.mark.asyncio
async def test_search_entities_caches_query_embedding() -> None:
    from agentgraph.graph.query import clear_query_embedding_cache, search_entities

    backend = _mock_backend(search_entities=AsyncMock(return_value=[]))
    set_backend(backend)
    clear_query_embedding_cache()

    with patch(
        "agentgraph.graph.embeddings.encode_query", return_value=[0.1] * 384
    ) as encode_query:
        await search_entities("repeat")
        await search_entities("repeat")

    encode_query.assert_called_once_with("repeat")


@pytest.mark.asyncio
async def test_sqlite_search_skips_vector_when_fts_fills_candidate_window() -> None:
    from agentgraph.backends.sqlite.backend import SQLiteBackend
    from agentgraph.connectors.base import EntityBatch, EntityRecord

    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    try:
        await backend.upsert_batch(
            EntityBatch(
                entities=[
                    EntityRecord(
                        entity_type="Document",
                        platform="web",
                        platform_entity_id=f"doc-{index}",
                        title=f"Alpha {index}",
                        content="alpha content",
                    )
                    for index in range(5)
                ]
            ),
            person_embeddings={},
            entity_embeddings={},
        )

        with patch(
            "agentgraph.backends.sqlite.backend.vector_ranked", new=AsyncMock(return_value=[])
        ) as vector_ranked:
            results = await backend.search_entities([0.0] * 384, "alpha", None, 1, 0.0)

        assert len(results) == 1
        vector_ranked.assert_not_awaited()
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_sqlite_search_uses_large_vector_window_for_sparse_fts() -> None:
    from agentgraph.backends.sqlite.backend import SQLiteBackend
    from agentgraph.connectors.base import EntityBatch, EntityRecord

    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    try:
        await backend.upsert_batch(
            EntityBatch(
                entities=[
                    EntityRecord(
                        entity_type="Document",
                        platform="web",
                        platform_entity_id="doc-0",
                        title="Alpha",
                        content="alpha content",
                    )
                ]
            ),
            person_embeddings={},
            entity_embeddings={},
        )

        with patch(
            "agentgraph.backends.sqlite.backend.vector_ranked", new=AsyncMock(return_value=[])
        ) as vector_ranked:
            await backend.search_entities([0.0] * 384, "alpha", None, 1, 0.0)

        vector_ranked.assert_awaited_once()
        assert vector_ranked.call_args.kwargs["candidate_limit"] == 5
    finally:
        await backend.close()


@asynccontextmanager
async def _filter_backend() -> AsyncIterator[Any]:
    """A backend holding two platforms' Alpha documents, one of them authored."""
    from agentgraph.backends.sqlite.backend import SQLiteBackend
    from agentgraph.connectors.base import EdgeRecord, EntityBatch, EntityRecord, PersonRecord

    backend = SQLiteBackend(":memory:", vector_mode="bm25-only")
    await backend.initialize()
    try:
        await backend.upsert_batch(
            EntityBatch(
                entities=[
                    EntityRecord(
                        entity_type="Document",
                        platform=platform,
                        platform_entity_id=f"{platform}-doc",
                        title="Alpha",
                        content="alpha content",
                        metadata={"web_url": f"https://{platform}.example/alpha"},
                    )
                    for platform in ("web", "rss")
                ],
                persons=[
                    PersonRecord(
                        platform="web", platform_user_id="author-1", display_name="Author One"
                    )
                ],
                edges=[
                    EdgeRecord(
                        edge_type="authored",
                        platform="web",
                        source_platform_user_id="author-1",
                        target_platform_entity_id="web-doc",
                    )
                ],
            ),
            person_embeddings={},
            entity_embeddings={},
        )
        yield backend
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_sqlite_search_applies_filters_to_the_ranked_path() -> None:
    """Filters narrow ranked search, which was impossible before the merge."""
    async with _filter_backend() as backend:
        unfiltered = await backend.search_entities([0.0] * 384, "alpha", None, 10, 0.0)
        assert {result["platform"] for result in unfiltered} == {"web", "rss"}

        by_platform = await backend.search_entities(
            [0.0] * 384, "alpha", None, 10, 0.0, platform="rss"
        )
        assert [result["platform"] for result in by_platform] == ["rss"]

        by_metadata = await backend.search_entities(
            [0.0] * 384,
            "alpha",
            None,
            10,
            0.0,
            filters={"web_url": "https://web.example/alpha"},
        )
        assert [result["platform"] for result in by_metadata] == ["web"]

        by_since = await backend.search_entities(
            [0.0] * 384,
            "alpha",
            None,
            10,
            0.0,
            since=datetime(2099, 1, 1, tzinfo=UTC),
        )
        assert by_since == []


@pytest.mark.asyncio
async def test_sqlite_search_authored_join_applies_to_the_fts_leg() -> None:
    """The lexical leg never had the authored JOIN; bm25-only proves it does now."""
    async with _filter_backend() as backend:
        results = await backend.search_entities(
            [0.0] * 384, "alpha", None, 10, 0.0, authored_by=["author-1"]
        )

    assert [result["platform_entity_id"] for result in results] == ["web-doc"]


@pytest.mark.asyncio
async def test_sqlite_search_orders_a_ranked_result_set_by_date() -> None:
    """`order_by` with a query means relevance-filtered but date-sorted."""
    async with _filter_backend() as backend:
        await backend.search_entities([0.0] * 384, "alpha", None, 10, 0.0)
        ordered = await backend.search_entities(
            [0.0] * 384, "alpha", None, 10, 0.0, order_by="observed_at"
        )

    stamps = [str(result["observed_at"]) for result in ordered]
    assert stamps == sorted(stamps, reverse=True)


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        # The exact shapes the RSS and web connectors call the backend with. Only
        # web_url is stored on the fixture entities, so the link/url probes miss.
        ({"platform": "rss", "web_url": "https://rss.example/alpha"}, ["rss"]),
        ({"platform": "web", "web_url": "https://web.example/alpha"}, ["web"]),
        ({"platform": "rss", "link": "https://rss.example/alpha"}, []),
        ({"platform": "web", "url": "https://web.example/alpha"}, []),
        ({"platform": "web", "web_url": "https://rss.example/alpha"}, []),
    ],
    ids=["rss-web_url", "web-web_url", "rss-link-miss", "web-url-miss", "mismatched-platform"],
)
@pytest.mark.asyncio
async def test_sqlite_query_by_filter_delegate_keeps_connector_call_shapes(
    filters: dict[str, str], expected: list[str]
) -> None:
    """Connectors call query_by_filter positionally; the delegate must behave as before."""
    async with _filter_backend() as backend:
        results = await backend.query_by_filter("Document", filters, 1, "updated_at", None, None)

    assert [result["platform"] for result in results] == expected


@pytest.mark.asyncio
async def test_sqlite_query_by_filter_delegate_honours_limit_and_type() -> None:
    async with _filter_backend() as backend:
        one = await backend.query_by_filter("Document", {}, 1, "updated_at", None, None)
        both = await backend.query_by_filter("Document", {}, 10, "updated_at", None, None)
        wrong_type = await backend.query_by_filter("Message", {}, 10, "updated_at", None, None)

    assert len(one) == 1
    assert len(both) == 2
    # The Person seeded alongside the documents must not leak into a Document query.
    assert {result["entity_type"] for result in both} == {"Document"}
    assert wrong_type == []


@pytest.mark.asyncio
async def test_query_by_filter_reuses_connector_for_web_url_enrichment() -> None:
    from agentgraph.graph.query import query_by_filter

    entities = [
        _entity(platform="example"),
        _entity(platform="example"),
    ]
    # query_by_filter is a narrow spelling of search_entities with no query string.
    backend = _mock_backend(search_entities=AsyncMock(return_value=entities))
    set_backend(backend)

    class FakeConnector:
        def entity_url(self, platform_entity_id: str) -> str:
            return f"https://example.com/{platform_entity_id}"

    with patch(
        "agentgraph.connectors.registry.get_connector", return_value=FakeConnector()
    ) as get_connector:
        result = await query_by_filter("Document", {})

    assert result[0]["metadata"]["web_url"].startswith("https://example.com/")
    get_connector.assert_called_once_with("example")


# ---------------------------------------------------------------------------
# get_entity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_entity_found() -> None:
    from agentgraph.graph.query import get_entity

    eid = str(uuid4())
    entity = _entity(title="Found Doc")
    entity["id"] = eid
    backend = _mock_backend(get_entity_by_id=AsyncMock(return_value=entity))
    set_backend(backend)

    result = await get_entity(eid)
    assert result is not None
    assert result["title"] == "Found Doc"


@pytest.mark.asyncio
async def test_get_entity_not_found() -> None:
    from agentgraph.graph.query import get_entity

    backend = _mock_backend(get_entity_by_id=AsyncMock(return_value=None))
    set_backend(backend)

    result = await get_entity(str(uuid4()))
    assert result is None


@pytest.mark.asyncio
async def test_get_entity_by_url_uses_owning_connector() -> None:
    from agentgraph.connectors.base import SourceReference
    from agentgraph.graph.query import get_entity_by_url

    entity = _entity(platform="gdocs", title="URL Doc")
    backend = _mock_backend(get_entity_by_platform=AsyncMock(return_value=entity))
    set_backend(backend)

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.server.router.classify_url",
            return_value=SourceReference("gdocs", "document", "doc-1"),
        ),
    ):
        result = await get_entity_by_url("https://docs.google.com/document/d/doc-1/edit")

    assert result is entity
    backend.get_entity_by_platform.assert_awaited_once_with("gdocs", "doc-1")


@pytest.mark.asyncio
async def test_get_entity_by_url_uses_web_canonical_url() -> None:
    from agentgraph.connectors.base import SourceReference
    from agentgraph.graph.query import get_entity_by_url

    entity = _entity(platform="web", title="Web Page")
    backend = _mock_backend(get_entity_by_platform=AsyncMock(return_value=entity))
    set_backend(backend)

    class FakeWebConnector:
        def resolve_url(self, url: str) -> SourceReference | None:
            return SourceReference("web", "document", url.removesuffix("#section"))

        def entity_url(self, platform_entity_id: str) -> str:
            return platform_entity_id

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.server.router.classify_url", return_value=None),
        patch("agentgraph.connectors.registry.get_connector", return_value=FakeWebConnector()),
    ):
        result = await get_entity_by_url("https://example.com/page#section")

    assert result is entity
    backend.get_entity_by_platform.assert_awaited_once_with("web", "https://example.com/page")


@pytest.mark.asyncio
async def test_get_entity_by_url_falls_back_to_connector_metadata_urls() -> None:
    from agentgraph.connectors.base import SourceReference
    from agentgraph.graph.query import get_entity_by_url

    entity = _entity(platform="web", title="Redirected Page")
    backend = _mock_backend(
        get_entity_by_platform=AsyncMock(return_value=None),
        query_by_filter=AsyncMock(side_effect=[[], [entity]]),
    )
    set_backend(backend)

    class FakeWebConnector:
        def resolve_url(self, url: str) -> SourceReference | None:
            return SourceReference("web", "document", "https://example.com/final")

        def entity_url(self, platform_entity_id: str) -> str:
            return platform_entity_id

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.server.router.classify_url", return_value=None),
        patch("agentgraph.connectors.registry.get_connector", return_value=FakeWebConnector()),
    ):
        result = await get_entity_by_url("https://example.com/original")

    assert result is entity
    backend.query_by_filter.assert_any_await(
        "Document",
        {"web_url": "https://example.com/original"},
        1,
        "updated_at",
        None,
        None,
    )
    backend.query_by_filter.assert_any_await(
        "Document",
        {"url": "https://example.com/original"},
        1,
        "updated_at",
        None,
        None,
    )


@pytest.mark.asyncio
async def test_get_entity_by_url_finds_rss_document_by_web_url() -> None:
    from agentgraph.connectors.base import SourceReference
    from agentgraph.graph.query import get_entity_by_url

    url = "https://marginalrevolution.com/article.html?utm_source=rss"
    entity = _entity(platform="rss", title="RSS Article")
    backend = _mock_backend(
        get_entity_by_platform=AsyncMock(return_value=None),
        query_by_filter=AsyncMock(side_effect=[[entity]]),
    )
    set_backend(backend)

    class FakeWebConnector:
        def resolve_url(self, raw_url: str) -> SourceReference | None:
            return SourceReference("web", "document", raw_url)

        def entity_url(self, platform_entity_id: str) -> str:
            return platform_entity_id

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.server.router.classify_url", return_value=None),
        patch("agentgraph.connectors.registry.get_connector", return_value=FakeWebConnector()),
    ):
        result = await get_entity_by_url(url)

    assert result is entity
    backend.query_by_filter.assert_awaited_once_with(
        "Document", {"web_url": url}, 1, "updated_at", None, None
    )


@pytest.mark.asyncio
async def test_get_entity_by_url_missing_does_not_fetch_or_upsert() -> None:
    from agentgraph.connectors.base import SourceReference
    from agentgraph.graph.query import get_entity_by_url

    backend = _mock_backend(get_entity_by_platform=AsyncMock(return_value=None))
    set_backend(backend)
    fetch = AsyncMock()

    class FakeWebConnector:
        async def fetch(self, *_args: object) -> None:
            await fetch()

        def resolve_url(self, url: str) -> SourceReference | None:
            return SourceReference("web", "document", url)

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.server.router.classify_url", return_value=None),
        patch("agentgraph.connectors.registry.get_connector", return_value=FakeWebConnector()),
        patch("agentgraph.graph.upsert.upsert_batch", new=AsyncMock()) as upsert_batch,
    ):
        result = await get_entity_by_url("https://example.com/missing")

    assert result is None
    fetch.assert_not_awaited()
    upsert_batch.assert_not_awaited()


# ---------------------------------------------------------------------------
# get_edges
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_edges_returns_edges() -> None:
    from agentgraph.graph.query import get_edges

    eid = str(uuid4())
    edge = _edge(source_entity_id=eid)
    backend = _mock_backend(get_edges=AsyncMock(return_value=[edge]))
    set_backend(backend)

    edges = await get_edges(eid, direction="out")
    assert len(edges) == 1
    assert edges[0]["edge_type"] == "authored"


# ---------------------------------------------------------------------------
# bookmark_entity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bookmark_entity_resolves_id_and_sets_bookmark() -> None:
    from agentgraph.graph.bookmark import bookmark_entity

    entity = _entity(title="Target")
    updated = dict(entity)
    updated["bookmarked"] = True
    set_bookmarked = AsyncMock(return_value=updated)
    backend = _mock_backend(
        get_entities_by_id_prefix=AsyncMock(return_value=[entity]),
        set_entity_bookmarked=set_bookmarked,
    )
    set_backend(backend)

    result = await bookmark_entity(entity["id"][:8])

    assert result["bookmarked"] is True
    set_bookmarked.assert_awaited_once_with(entity["id"], True)


@pytest.mark.asyncio
async def test_set_entity_bookmark_can_clear_bookmark() -> None:
    from agentgraph.graph.bookmark import set_entity_bookmark

    entity = _entity(title="Target")
    entity["bookmarked"] = True
    updated = dict(entity)
    updated["bookmarked"] = False
    set_bookmarked = AsyncMock(return_value=updated)
    backend = _mock_backend(
        get_entities_by_id_prefix=AsyncMock(return_value=[entity]),
        set_entity_bookmarked=set_bookmarked,
    )
    set_backend(backend)

    result = await set_entity_bookmark(entity["id"][:8], False)

    assert result["bookmarked"] is False
    set_bookmarked.assert_awaited_once_with(entity["id"], False)


@pytest.mark.asyncio
async def test_bookmark_route_can_clear_bookmark() -> None:
    from agentgraph.server.graph_api import bookmark_entity

    fake_result = _entity(title="Target")
    fake_result["bookmarked"] = False

    with patch(
        "agentgraph.graph.bookmark.set_entity_bookmark",
        new=AsyncMock(return_value=fake_result),
    ) as set_bookmark:
        result = await bookmark_entity(ref="abc123", bookmarked=False)

    assert result["bookmarked"] is False
    set_bookmark.assert_awaited_once_with("abc123", False)


@pytest.mark.asyncio
async def test_bookmark_entity_missing_raises_value_error() -> None:
    from agentgraph.graph.bookmark import bookmark_entity

    backend = _mock_backend(get_entities_by_id_prefix=AsyncMock(return_value=[]))
    set_backend(backend)

    with pytest.raises(ValueError, match="not found"):
        await bookmark_entity("missing")


@pytest.mark.asyncio
async def test_bookmark_url_uses_owning_connector() -> None:
    from agentgraph.connectors.base import EntityBatch, SourceReference
    from agentgraph.graph.bookmark import bookmark_target

    entity = _entity(platform="gdocs", title="Fetched Doc")
    updated = dict(entity)
    updated["bookmarked"] = True
    backend = _mock_backend(
        get_entity_by_platform=AsyncMock(return_value=entity),
        set_entity_bookmarked=AsyncMock(return_value=updated),
    )
    set_backend(backend)

    class FakeConnector:
        async def fetch(
            self,
            resource_type: str,
            resource_id: str,
        ) -> EntityBatch:
            assert resource_type == "document"
            assert resource_id == "doc-1"
            return EntityBatch()

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=FakeConnector()),
        patch(
            "agentgraph.server.router.classify_url",
            return_value=SourceReference("gdocs", "document", "doc-1"),
        ),
    ):
        result = await bookmark_target("https://docs.google.com/document/d/doc-1/edit")

    assert result["bookmarked"] is True
    backend.set_entity_bookmarked.assert_awaited_once_with(entity["id"], True)


@pytest.mark.asyncio
async def test_bookmark_url_falls_back_to_web_connector() -> None:
    from agentgraph.connectors.base import EntityBatch, EntityRecord
    from agentgraph.graph.bookmark import bookmark_target

    entity = _entity(platform="web", title="Fetched Page")
    updated = dict(entity)
    updated["bookmarked"] = True
    backend = _mock_backend(
        get_entity_by_platform=AsyncMock(return_value=entity),
        set_entity_bookmarked=AsyncMock(return_value=updated),
    )
    set_backend(backend)

    class FakeWebConnector:
        async def fetch(
            self,
            resource_type: str,
            resource_id: str,
        ) -> EntityBatch:
            assert resource_type == "document"
            assert resource_id == "https://example.com/page"
            return EntityBatch(
                entities=[
                    EntityRecord(
                        entity_type="Document",
                        platform="web",
                        platform_entity_id="https://example.com/page",
                        title="Fetched Page",
                        content="Body",
                    )
                ]
            )

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=FakeWebConnector()),
        patch("agentgraph.server.router.classify_url", return_value=None),
        patch("agentgraph.graph.upsert.upsert_batch", new=AsyncMock()),
    ):
        result = await bookmark_target("https://example.com/page")

    assert result["bookmarked"] is True
    backend.set_entity_bookmarked.assert_awaited_once_with(entity["id"], True)


@pytest.mark.asyncio
async def test_bookmark_url_persists_metadata_patch_before_resolving_entity() -> None:
    from agentgraph.connectors.base import EntityBatch, EntityMetadataPatch
    from agentgraph.graph.bookmark import bookmark_target

    entity = _entity(platform="web", title="Fetched Page")
    updated = {**entity, "bookmarked": True}
    backend = _mock_backend(
        get_entity_by_platform=AsyncMock(return_value=entity),
        set_entity_bookmarked=AsyncMock(return_value=updated),
    )
    set_backend(backend)
    batch = EntityBatch(
        metadata_patches=[
            EntityMetadataPatch(
                platform="web",
                platform_entity_id="https://example.com/page",
                metadata={"http_etag": '"fresh"'},
            )
        ]
    )

    class FakeWebConnector:
        async def fetch(self, resource_type: str, resource_id: str) -> EntityBatch:
            _ = (resource_type, resource_id)
            return batch

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=FakeWebConnector()),
        patch("agentgraph.server.router.classify_url", return_value=None),
        patch("agentgraph.graph.upsert.upsert_batch", new=AsyncMock()) as upsert_batch,
    ):
        result = await bookmark_target("https://example.com/page")

    upsert_batch.assert_awaited_once_with(batch)
    assert result["bookmarked"] is True


# ---------------------------------------------------------------------------
# delete_entity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_entity_resolves_id_and_deletes() -> None:
    from agentgraph.graph.delete import delete_entity

    entity = _entity(title="Target")
    backend = _mock_backend(
        get_entities_by_id_prefix=AsyncMock(return_value=[entity]),
        delete_entity=AsyncMock(return_value=entity),
    )
    set_backend(backend)

    result = await delete_entity(entity["id"][:8])

    assert result["deleted"] is True
    assert result["entity"]["id"] == entity["id"]
    backend.delete_entity.assert_awaited_once_with(entity["id"])


@pytest.mark.asyncio
async def test_delete_entity_missing_raises_value_error() -> None:
    from agentgraph.graph.delete import delete_entity

    backend = _mock_backend(get_entities_by_id_prefix=AsyncMock(return_value=[]))
    set_backend(backend)

    with pytest.raises(ValueError, match="not found"):
        await delete_entity("missing")


@pytest.mark.asyncio
async def test_delete_entities_resolves_deduplicates_and_notifies_once() -> None:
    from agentgraph.graph.delete import delete_entities

    first = _entity(title="First")
    second = _entity(title="Second")
    backend = _mock_backend(
        get_entity_by_id=AsyncMock(side_effect=[first, first, second]),
        delete_entities=AsyncMock(return_value=[first, second]),
    )
    set_backend(backend)

    with patch(
        "agentgraph.connectors.feed.notify_feed_connectors", new=AsyncMock()
    ) as notify:
        result = await delete_entities([first["id"], first["id"], second["id"]])

    assert result["deleted_count"] == 2
    assert [entity["id"] for entity in result["entities"]] == [first["id"], second["id"]]
    backend.delete_entities.assert_awaited_once_with([first["id"], second["id"]])
    notify.assert_awaited_once()
    await_args = notify.await_args
    assert await_args is not None
    event = await_args.args[0]
    assert event.kind == "tombstone_batch"
    assert [target.platform_entity_id for target in event.targets] == [
        first["platform_entity_id"],
        second["platform_entity_id"],
    ]


@pytest.mark.asyncio
async def test_delete_entities_validates_all_targets_before_deleting() -> None:
    from agentgraph.graph.delete import delete_entities

    entity = _entity(title="Present")
    backend = _mock_backend(
        get_entity_by_id=AsyncMock(side_effect=[entity, None]),
        delete_entities=AsyncMock(),
    )
    set_backend(backend)

    with pytest.raises(ValueError, match="not found"):
        await delete_entities([entity["id"], str(uuid4())])

    backend.delete_entities.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_route_deletes_entity() -> None:
    from agentgraph.server.graph_api import delete_entity as delete_route

    fake_result = {"deleted": True, "entity": _entity(title="Target")}

    with patch(
        "agentgraph.graph.delete.delete_entity",
        new=AsyncMock(return_value=fake_result),
    ) as delete_entity:
        result = await delete_route(ref="abc123")

    assert result["deleted"] is True
    delete_entity.assert_awaited_once_with("abc123")


@pytest.mark.asyncio
async def test_delete_entities_route_deletes_multiple_entities() -> None:
    from agentgraph.server.graph_api import delete_entities as delete_route

    result: dict[str, Any] = {"deleted_count": 2, "entities": []}
    with patch(
        "agentgraph.graph.delete.delete_entities", new=AsyncMock(return_value=result)
    ) as delete_entities:
        assert await delete_route(["first", "second"]) == result

    delete_entities.assert_awaited_once_with(["first", "second"])


# ---------------------------------------------------------------------------
# MCP tool wrappers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_fetch_web_size_limit_suggests_compact_command() -> None:
    from agentgraph_connector_web import WebConnector

    from agentgraph.mcp.server import fetch_entity_tool

    with (
        patch(
            "agentgraph.graph.fetch.fetch_entity",
            new=AsyncMock(
                side_effect=ValueError(
                    "Response too large for web document: limit is 2000000 bytes"
                )
            ),
        ),
        patch("agentgraph.connectors.registry.get_connector", return_value=WebConnector()),
    ):
        result = await fetch_entity_tool("web", "https://example.com/page")

    error = _mcp_error(result)
    assert "Response too large for web document" in error["message"]
    assert (
        'run_connector_command_tool("web", ["fetch", "https://example.com/page", "--compact"])'
        in error["message"]
    )


@pytest.mark.asyncio
async def test_mcp_search_entities_tool_returns_json() -> None:
    from agentgraph.mcp.server import search_entities_tool

    with patch("agentgraph.graph.query.search_entities", new=AsyncMock(return_value=[])):
        result = await search_entities_tool("test")

    parsed = _mcp_data(result)
    assert parsed == {"entities": [], "limit": 10, "returned": 0, "has_more": False}


@pytest.mark.asyncio
async def test_mcp_search_entities_tool_skips_connector_enrichment_by_default() -> None:
    from agentgraph.mcp.server import search_entities_tool

    entity = _entity(platform="example")

    class FakeConnector:
        async def enrich_results(self, entities: list[dict[str, Any]]) -> None:
            entities[0]["metadata"]["enriched"] = True

    with (
        patch("agentgraph.graph.query.search_entities", new=AsyncMock(return_value=[entity])),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=FakeConnector()),
    ):
        result = await search_entities_tool("test")

    parsed = _mcp_data(result)
    assert "enriched" not in parsed["entities"][0]["metadata"]


@pytest.mark.asyncio
async def test_mcp_search_entities_tool_enriches_results_via_connector_when_refreshed() -> None:
    from agentgraph.mcp.server import search_entities_tool

    entity = _entity(platform="example")

    class FakeConnector:
        async def enrich_results(self, entities: list[dict[str, Any]]) -> None:
            entities[0]["metadata"]["enriched"] = True

    with (
        patch("agentgraph.graph.query.search_entities", new=AsyncMock(return_value=[entity])),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=FakeConnector()),
    ):
        result = await search_entities_tool("test", refresh=True)

    parsed = _mcp_data(result)
    assert parsed["entities"][0]["metadata"]["enriched"] is True


@pytest.mark.asyncio
async def test_mcp_get_entity_tool_not_found() -> None:
    from agentgraph.mcp.server import get_entity_tool

    eid = str(uuid4())
    with patch("agentgraph.graph.operations.get_entity_details", new=AsyncMock(return_value=None)):
        result = await get_entity_tool(eid)

    assert _mcp_error(result)["code"] == "entity_not_found"


@pytest.mark.asyncio
async def test_mcp_get_entity_tool_found() -> None:
    from agentgraph.mcp.server import get_entity_tool

    eid = str(uuid4())
    fake_entity = _entity(title="My Doc")
    fake_entity["id"] = eid
    with patch(
        "agentgraph.graph.operations.get_entity_details",
        new=AsyncMock(return_value=fake_entity),
    ):
        result = await get_entity_tool(eid)

    parsed = _mcp_data(result)
    assert parsed["entity"]["title"] == "My Doc"


@pytest.mark.asyncio
async def test_mcp_get_entity_tool_resolves_stub_when_requested() -> None:
    from agentgraph.mcp.server import get_entity_tool

    eid = str(uuid4())
    resolved = _entity(title="Resolved", content="Full source content")
    resolved["id"] = eid

    with patch(
        "agentgraph.graph.operations.get_entity_details",
        new=AsyncMock(return_value=resolved),
    ) as get_entity_details:
        result = await get_entity_tool(eid, resolve=True)

    parsed = _mcp_data(result)
    assert parsed["entity"]["content"] == "Full source content"
    get_entity_details.assert_awaited_once_with(eid, resolve=True)


@pytest.mark.asyncio
async def test_mcp_get_entity_tool_does_not_resolve_stub_by_default() -> None:
    from agentgraph.mcp.server import get_entity_tool

    stub = _entity(title="", content="")
    with patch(
        "agentgraph.graph.operations.get_entity_details",
        new=AsyncMock(return_value=stub),
    ) as get_entity_details:
        result = await get_entity_tool(str(stub["id"]))

    assert _mcp_data(result)["entity"]["content"] == ""
    get_entity_details.assert_awaited_once_with(str(stub["id"]), resolve=False)


@pytest.mark.asyncio
async def test_mcp_get_entity_tool_url_found() -> None:
    from agentgraph.mcp.server import get_entity_tool

    fake_entity = _entity(platform="web", title="Web Page")
    with patch(
        "agentgraph.graph.operations.get_entity_details",
        new=AsyncMock(return_value=fake_entity),
    ):
        result = await get_entity_tool("https://example.com/page")

    parsed = _mcp_data(result)
    assert parsed["entity"]["title"] == "Web Page"


@pytest.mark.asyncio
async def test_mcp_get_entity_tool_url_not_found() -> None:
    from agentgraph.mcp.server import get_entity_tool

    with patch("agentgraph.graph.operations.get_entity_details", new=AsyncMock(return_value=None)):
        result = await get_entity_tool("https://example.com/missing")

    assert _mcp_error(result)["code"] == "entity_not_found"


@pytest.mark.asyncio
async def test_mcp_get_edges_resolves_platform_reference() -> None:
    from agentgraph.mcp.server import get_edges_tool

    entity = _entity(platform="slack")
    edge = _edge(source_entity_id=str(entity["id"]), target_entity_id=str(uuid4()))
    with patch(
        "agentgraph.graph.operations.get_entity_edges",
        new=AsyncMock(return_value=(entity, [edge])),
    ) as get_entity_edges:
        result = await get_edges_tool("slack/T123/C123")

    parsed = _mcp_data(result)
    assert parsed["edges"] == [edge]
    assert parsed["entity"]["id"] == entity["id"]
    get_entity_edges.assert_awaited_once_with(
        "slack/T123/C123",
        edge_type=None,
        direction="both",
    )


@pytest.mark.asyncio
async def test_mcp_traverse_caps_depth() -> None:
    from agentgraph.mcp.server import traverse_graph_tool

    entity = _entity()
    with patch(
        "agentgraph.graph.operations.traverse_entity",
        new=AsyncMock(return_value=(entity, {"nodes": [], "edges": []})),
    ) as traverse:
        await traverse_graph_tool(str(uuid4()), max_depth=99)

    traverse.assert_awaited_once()
    assert traverse.await_args is not None
    assert traverse.await_args.kwargs["max_depth"] == 4


@pytest.mark.asyncio
async def test_mcp_traverse_allows_depth_zero() -> None:
    from agentgraph.mcp.server import traverse_graph_tool

    entity = _entity()
    with patch(
        "agentgraph.graph.operations.traverse_entity",
        new=AsyncMock(return_value=(entity, {"nodes": [], "edges": []})),
    ) as traverse:
        await traverse_graph_tool(str(uuid4()), max_depth=0)

    traverse.assert_awaited_once()
    assert traverse.await_args is not None
    assert traverse.await_args.kwargs["max_depth"] == 0


@pytest.mark.asyncio
async def test_mcp_traverse_resolves_stub_nodes_and_repeats_traversal() -> None:
    from agentgraph.mcp.server import traverse_graph_tool

    start = _entity(title="Start")
    resolved = _entity(title="Hydrated", content="source")
    traversal = {"nodes": [start, resolved], "edges": []}

    with patch(
        "agentgraph.graph.operations.traverse_entity",
        new=AsyncMock(return_value=(start, traversal)),
    ) as traverse:
        result = await traverse_graph_tool("gdocs/doc-id", resolve=True)

    parsed = _mcp_data(result)
    assert parsed["nodes"][1]["title"] == "Hydrated"
    assert parsed["nodes"][1]["content_truncated"] is False
    traverse.assert_awaited_once_with("gdocs/doc-id", max_depth=2, resolve=True)


@pytest.mark.asyncio
async def test_mcp_tool_metadata_guides_agent_workflow() -> None:
    from agentgraph.mcp.server import mcp

    tools = {tool.name: tool for tool in await mcp.list_tools()}

    instructions = mcp.instructions
    assert instructions is not None
    assert "search broadly" in instructions
    assert tools["search_entities_tool"].inputSchema["properties"]["min_score"]["default"] == 0.03
    assert tools["get_entity_tool"].inputSchema["properties"]["resolve"]["default"] is False
    assert tools["traverse_graph_tool"].inputSchema["properties"]["resolve"]["default"] is False
    assert tools["search_entities_tool"].annotations is not None
    assert tools["search_entities_tool"].annotations.readOnlyHint is True
    assert tools["delete_entity_tool"].annotations is not None
    assert tools["delete_entity_tool"].annotations.destructiveHint is True
    assert tools["delete_entities_tool"].annotations is not None
    assert tools["delete_entities_tool"].annotations.destructiveHint is True
    search_output = tools["search_entities_tool"].outputSchema
    assert search_output is not None
    assert search_output["title"] == "ToolData[SearchData]"
    assert len(search_output["anyOf"]) == 2
    assert "install_skill_tool" not in tools
    assert "query_by_filter_tool" not in tools
    search_description = tools["search_entities_tool"].description
    assert search_description is not None
    assert "default 0.03" in search_description
    # The filtering guidance folded in from the removed query_by_filter_tool.
    assert "Gmail attachments" in search_description
    assert "has_attachments" in search_description
    assert "Entity types available in this MCP process:" in search_description
    for entity_type in ("Channel", "Document", "Email", "Folder", "Message", "Person"):
        assert f"- {entity_type}:" in search_description


def test_mcp_entity_type_catalog_includes_connector_descriptions() -> None:
    from agentgraph.mcp.server import entity_type_catalog_description

    catalog = [
        (
            "Project",
            [
                ("first", "A project in the first source."),
                ("second", "A project in the second source."),
            ],
        )
    ]
    with patch("agentgraph.connectors.registry.get_entity_type_catalog", return_value=catalog):
        description = entity_type_catalog_description()

    assert "Project:" in description
    assert "first: A project in the first source." in description
    assert "second: A project in the second source." in description


@pytest.mark.asyncio
async def test_mcp_search_entities_tool_filters_without_a_query() -> None:
    from agentgraph.mcp.server import search_entities_tool

    with patch("agentgraph.graph.query.search_entities", new=AsyncMock(return_value=[])) as search:
        result = await search_entities_tool(filters={"channel_id": "C123"})

    assert _mcp_data(result)["entities"] == []
    # No query string, so the unranked default limit applies and filter values are
    # coerced to the strings every predicate compares against.
    assert search.await_args is not None
    assert search.await_args.args[0] is None
    assert search.await_args.kwargs["filters"] == {"channel_id": "C123"}
    assert search.await_args.kwargs["limit"] == 51


@pytest.mark.asyncio
async def test_mcp_search_entities_tool_reports_remaining_results() -> None:
    from agentgraph.mcp.server import search_entities_tool

    with patch(
        "agentgraph.graph.query.search_entities",
        new=AsyncMock(return_value=[_entity(title="First"), _entity(title="Second")]),
    ) as search:
        result = await search_entities_tool("test", limit=1)

    data = _mcp_data(result)
    assert [entity["title"] for entity in data["entities"]] == ["First"]
    assert data["returned"] == 1
    assert data["has_more"] is True
    assert search.await_args is not None
    assert search.await_args.kwargs["limit"] == 2


@pytest.mark.asyncio
async def test_mcp_search_entities_tool_coerces_non_string_filter_values() -> None:
    """MCP clients send numbers and bools; the predicates compare against text."""
    from agentgraph.mcp.server import search_entities_tool

    with patch("agentgraph.graph.query.search_entities", new=AsyncMock(return_value=[])) as search:
        await search_entities_tool(filters={"issue_number": 42, "resolved": True})

    assert search.await_args is not None
    assert search.await_args.kwargs["filters"] == {
        "issue_number": "42",
        "resolved": "True",
    }


@pytest.mark.asyncio
async def test_mcp_search_entities_tool_truncates_long_content() -> None:
    from agentgraph.mcp.server import search_entities_tool

    entity = _entity(content="x" * 700)

    with (
        patch("agentgraph.graph.query.search_entities", new=AsyncMock(return_value=[entity])),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=None),
    ):
        result = await search_entities_tool(entity_types=["Message"])

    parsed = _mcp_data(result)
    # Bounded by the query layer's summarize_entities, which the transport applies for
    # every caller, rather than by a second truncation inside the MCP tool.
    assert len(parsed["entities"][0]["content"]) == 500
    assert parsed["entities"][0]["content_truncated"] is True


@pytest.mark.asyncio
async def test_mcp_download_entity_tool() -> None:
    from agentgraph.mcp.server import download_entity_tool

    fake_result = {"path": "/tmp/file.pdf", "bytes": 7, "filename": "file.pdf"}
    with patch(
        "agentgraph.graph.download.download_entity", new=AsyncMock(return_value=fake_result)
    ):
        result = await download_entity_tool("abc123", "/tmp")

    assert _mcp_data(result) == fake_result


@pytest.mark.asyncio
async def test_mcp_bookmark_entity_tool() -> None:
    from agentgraph.mcp.server import bookmark_entity_tool

    fake_result = _entity(title="My Doc")
    fake_result["bookmarked"] = True
    with patch(
        "agentgraph.graph.bookmark.bookmark_target", new=AsyncMock(return_value=fake_result)
    ):
        result = await bookmark_entity_tool("abc123")

    parsed = _mcp_data(result)
    assert parsed["entity"]["bookmarked"] is True


@pytest.mark.asyncio
async def test_mcp_bookmark_entity_tool_can_remove_bookmark() -> None:
    from agentgraph.mcp.server import bookmark_entity_tool

    fake_result = _entity(title="My Doc")
    fake_result["bookmarked"] = False
    with patch(
        "agentgraph.graph.bookmark.set_entity_bookmark", new=AsyncMock(return_value=fake_result)
    ) as set_bookmark:
        result = await bookmark_entity_tool("abc123", bookmarked=False)

    parsed = _mcp_data(result)
    assert parsed["entity"]["bookmarked"] is False
    set_bookmark.assert_awaited_once_with("abc123", False)


@pytest.mark.asyncio
async def test_mcp_delete_entity_tool() -> None:
    from agentgraph.mcp.server import delete_entity_tool

    fake_result = {"deleted": True, "entity": _entity(title="My Doc")}
    with patch("agentgraph.graph.delete.delete_entity", new=AsyncMock(return_value=fake_result)):
        result = await delete_entity_tool("abc123")

    parsed = _mcp_data(result)
    assert parsed["deleted"] is True


@pytest.mark.asyncio
async def test_mcp_delete_entities_tool() -> None:
    from agentgraph.mcp.server import delete_entities_tool

    fake_result = {"deleted_count": 2, "entities": [{"id": "one"}, {"id": "two"}]}
    with patch("agentgraph.graph.delete.delete_entities", new=AsyncMock(return_value=fake_result)):
        result = await delete_entities_tool(["first", "second"])

    parsed = _mcp_data(result)
    assert parsed == fake_result


@pytest.mark.asyncio
async def test_mcp_list_auth_providers_tool_returns_json() -> None:
    from agentgraph.mcp.server import list_auth_providers_tool

    fake_items = [
        {
            "provider": "google",
            "description": "Shared auth for gdocs, gdrive",
            "connectors": ["gdocs", "gdrive"],
            "shared": True,
            "auth_status": "ok",
            "auth_detail": "1 account(s)",
            "accounts": [],
        }
    ]

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_all_connectors", return_value=[]),
        patch(
            "agentgraph.connectors.status.auth_provider_status_items",
            new=AsyncMock(return_value=fake_items),
        ),
    ):
        result = await list_auth_providers_tool()

    assert _mcp_data(result) == {"items": fake_items}


@pytest.mark.asyncio
async def test_mcp_auth_status_exposes_slack_auth_method(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentgraph_connector_slack import SlackConnector

    from agentgraph.auth.credentials import save_platform
    from agentgraph.mcp.server import list_auth_providers_tool

    credentials_file = tmp_path / "credentials.json"
    monkeypatch.setattr("agentgraph.auth.credentials.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("agentgraph.auth.credentials.CREDENTIALS_FILE", credentials_file)
    save_platform(
        "slack",
        {
            "xoxc_token": "xoxc-T1-old",
            "d_cookie": "cookie",
            "team_id": "T1",
            "user_id": "U1",
        },
    )
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[SlackConnector()],
        ),
    ):
        result = await list_auth_providers_tool()

    parsed = _mcp_data(result)
    assert parsed["items"][0]["accounts"][0]["auth_method"] == "browser"


@pytest.mark.asyncio
async def test_mcp_remove_auth_provider_tool_removes_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentgraph.auth.credentials import load_platform, save_platform
    from agentgraph.mcp.server import remove_auth_provider_tool

    creds_file = tmp_path / "credentials.json"
    monkeypatch.setattr("agentgraph.auth.credentials.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("agentgraph.auth.credentials.CREDENTIALS_FILE", creds_file)
    save_platform("slack", {"xoxc_token": "xoxc-test", "d_cookie": "cookie"})

    result = await remove_auth_provider_tool("slack")

    parsed = _mcp_data(result)
    assert parsed == {"provider": "slack", "removed": True}
    assert load_platform("slack") is None


@pytest.mark.asyncio
async def test_mcp_authenticate_provider_dispatches_generic_connector_args() -> None:
    from agentgraph.mcp.server import authenticate_provider_tool

    captured: dict[str, object] = {}

    class AuthConnector:
        source = "example"
        auth_label = "example"
        appears_in_auth_status = True

        @classmethod
        def run_auth_flow_with_args(
            cls,
            args: list[str],
            account_id: str | None = None,
            add: bool = False,
        ) -> None:
            captured.update(args=args, account_id=account_id, add=add)

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[AuthConnector()],
        ),
    ):
        result = await authenticate_provider_tool(
            "example", ["--method", "custom"], "example:1", True
        )

    assert _mcp_data(result) == {"provider": "example", "authenticated": True}
    assert captured == {
        "args": ["--method", "custom"],
        "account_id": "example:1",
        "add": True,
    }


@pytest.mark.asyncio
async def test_mcp_list_connectors_tool_returns_json() -> None:
    from agentgraph.mcp.server import list_connectors_tool

    fake_items = [
        {
            "source": "gdocs",
            "description": "Google Docs",
            "auth_provider": "google",
            "shared_auth": True,
            "auth_status": "ok",
            "auth_detail": "1 account(s)",
            "accounts": [],
            "account_count": 1,
            "url_patterns": [],
            "polls": False,
            "poll_interval_seconds": None,
            "poll_delegates": [],
            "polled_by": ["gdrive"],
            "sync": "via gdrive poll",
            "last_synced_at": None,
            "last_sync": "never",
        }
    ]
    set_backend(_mock_backend(get_platform_last_synced_at=AsyncMock(return_value=None)))

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_all_connectors", return_value=[]),
        patch(
            "agentgraph.connectors.status.connector_status_items",
            new=AsyncMock(return_value=fake_items),
        ),
    ):
        result = await list_connectors_tool()

    assert _mcp_data(result) == {"items": fake_items}


@pytest.mark.asyncio
async def test_mcp_connector_command_queues_requested_poll() -> None:
    from agentgraph.mcp.server import run_connector_command_tool

    class Connector:
        source = "rss"

        @classmethod
        def run_cli_command(cls, args: list[str]) -> dict[str, Any]:
            return {"status": "ok", "args": args}

        @classmethod
        def command_effects(
            cls,
            args: list[str],
            result: dict[str, Any],
        ) -> ConnectorCommandEffects:
            _ = (args, result)
            return ConnectorCommandEffects(poll=True)

    poll_result = {"source": "rss", "status": "queued", "reason": None}
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=Connector()),
        patch(
            "agentgraph.server.sync.schedule_poll_connector",
            new=AsyncMock(return_value=poll_result),
        ) as schedule_poll,
    ):
        result = await run_connector_command_tool("rss", ["add", "https://example.com/feed.xml"])

    assert _mcp_data(result)["result"]["poll"] == poll_result
    schedule_poll.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_connector_command_executes_requested_entity_deletion() -> None:
    from agentgraph.mcp.server import run_connector_command_tool

    class Connector:
        source = "rss"

        @classmethod
        def run_cli_command(cls, args: list[str]) -> dict[str, Any]:
            return {"status": "ok", "args": args}

        @classmethod
        def command_effects(
            cls,
            args: list[str],
            result: dict[str, Any],
        ) -> ConnectorCommandEffects:
            _ = (args, result)
            return ConnectorCommandEffects(
                delete_entities=(EntityReference("rss", "feed/example"),),
            )

    deleted = [{"id": "feed", "platform": "rss", "platform_entity_id": "feed/example"}]
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=Connector()),
        patch(
            "agentgraph.connectors.command_effects.execute_deletions",
            new=AsyncMock(return_value=deleted),
        ) as execute_deletions,
    ):
        result = await run_connector_command_tool("rss", ["remove", "https://example.com/feed.xml"])

    assert _mcp_data(result)["result"]["deleted_entities"] == deleted
    execute_deletions.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_connector_command_executes_requested_fetch() -> None:
    from agentgraph.mcp.server import run_connector_command_tool

    class Connector:
        source = "web"

        @classmethod
        def run_cli_command(cls, args: list[str]) -> dict[str, Any]:
            return {"status": "ok", "url": args[1]}

        @classmethod
        def command_effects(
            cls,
            args: list[str],
            result: dict[str, Any],
        ) -> ConnectorCommandEffects:
            _ = (args, result)
            return ConnectorCommandEffects(
                fetch_references=(SourceReference("web", "document", "https://example.com/page"),)
            )

    fetched = [
        {
            "source": "web",
            "resource_type": "document",
            "resource_id": "https://example.com/page",
            "entities": 1,
            "persons": 0,
            "edges": 0,
        }
    ]
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=Connector()),
        patch(
            "agentgraph.connectors.command_effects.execute_fetches",
            new=AsyncMock(return_value=fetched),
        ) as execute_fetches,
    ):
        result = await run_connector_command_tool(
            "web", ["fetch", "https://example.com/page", "--compact"]
        )

    assert _mcp_data(result)["result"]["fetched"] == fetched
    execute_fetches.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_connector_web_fetch_size_limit_suggests_compact_command() -> None:
    from agentgraph_connector_web import WebConnector

    from agentgraph.mcp.server import run_connector_command_tool

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=WebConnector()),
        patch(
            "agentgraph.connectors.command_effects.execute_fetches",
            new=AsyncMock(
                side_effect=ValueError(
                    "Response too large for web document: limit is 2000000 bytes"
                )
            ),
        ),
    ):
        result = await run_connector_command_tool("web", ["fetch", "https://example.com/page"])

    error = _mcp_error(result)["message"]
    assert "Response too large for web document" in error
    assert (
        'run_connector_command_tool("web", ["fetch", "https://example.com/page", "--compact"])'
        in error
    )


@pytest.mark.asyncio
async def test_mcp_connector_command_reports_connector_load_error() -> None:
    from agentgraph.mcp.server import run_connector_command_tool

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=None),
        patch(
            "agentgraph.connectors.registry.get_connector_load_error",
            return_value="No module named 'curl_cffi'",
        ),
    ):
        result = await run_connector_command_tool("web", ["--help"])

    error = _mcp_error(result)
    assert error["code"] == "connector_load_failed"
    assert error["message"] == "Failed to load connector 'web': No module named 'curl_cffi'"


@pytest.mark.asyncio
async def test_mcp_connector_command_queues_requested_ingest_for_account() -> None:
    from agentgraph.mcp.server import run_connector_command_tool

    class Connector:
        source = "gmail"

        @classmethod
        def run_cli_command(cls, args: list[str]) -> dict[str, Any]:
            return {"status": "queued", "source": cls.source, "account_id": "user@example.com"}

        @classmethod
        def command_effects(
            cls,
            args: list[str],
            result: dict[str, Any],
        ) -> ConnectorCommandEffects:
            _ = (args, result)
            return ConnectorCommandEffects(ingest=True, ingest_account_id="user@example.com")

    created: list[Any] = []

    def fake_create_task(coro: Any) -> MagicMock:
        created.append(coro)
        coro.close()
        return MagicMock()

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=Connector()),
        patch("agentgraph.mcp.server.asyncio.create_task", side_effect=fake_create_task),
    ):
        result = await run_connector_command_tool(
            "gmail", ["ingest", "--account", "user@example.com"]
        )

    assert _mcp_data(result)["result"]["ingest"] == {
        "source": "gmail",
        "status": "started",
        "account_id": "user@example.com",
    }
    assert len(created) == 1


@pytest.mark.asyncio
async def test_mcp_connector_command_does_not_poll_after_validation_error() -> None:
    from agentgraph.mcp.server import run_connector_command_tool

    class Connector:
        source = "rss"

        @classmethod
        def run_cli_command(cls, args: list[str]) -> dict[str, Any]:
            _ = args
            raise ValueError("Not a valid RSS/Atom feed")

        @classmethod
        def command_effects(
            cls,
            args: list[str],
            result: dict[str, Any],
        ) -> ConnectorCommandEffects:
            _ = (args, result)
            return ConnectorCommandEffects(poll=True)

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=Connector()),
        patch("agentgraph.server.sync.schedule_poll_connector") as schedule_poll,
    ):
        result = await run_connector_command_tool("rss", ["add", "https://example.com/not-a-feed"])

    assert _mcp_error(result)["message"] == "Not a valid RSS/Atom feed"
    schedule_poll.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_poll_connectors_tool_starts_poll_tasks() -> None:
    from agentgraph.mcp.server import poll_connectors_tool

    class PollingConnector:
        source = "rss"
        poll_interval = object()

    class PassiveConnector:
        source = "gdocs"
        poll_interval = None

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[PollingConnector(), PassiveConnector()],
        ),
        patch(
            "agentgraph.server.sync.schedule_poll_connector",
            new=AsyncMock(return_value={"source": "rss", "status": "queued", "reason": None}),
        ) as schedule_poll,
    ):
        result = await poll_connectors_tool()

    assert _mcp_data(result) == {"polled": ["rss"], "already_running": [], "skipped": []}
    schedule_poll.assert_called_once()


@pytest.mark.asyncio
async def test_mcp_poll_connectors_tool_uses_source_connector_lookup() -> None:
    from agentgraph.mcp.server import poll_connectors_tool

    class PollingConnector:
        source = "rss"
        poll_interval = object()

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_connector", return_value=PollingConnector()
        ) as get_connector,
        patch("agentgraph.connectors.registry.get_all_connectors") as get_all_connectors,
        patch(
            "agentgraph.server.sync.schedule_poll_connector",
            new=AsyncMock(return_value={"source": "rss", "status": "queued", "reason": None}),
        ) as schedule_poll,
    ):
        result = await poll_connectors_tool("rss")

    assert _mcp_data(result) == {"polled": ["rss"], "already_running": [], "skipped": []}
    get_connector.assert_called_once_with("rss")
    get_all_connectors.assert_not_called()
    schedule_poll.assert_called_once()


@pytest.mark.asyncio
async def test_mcp_poll_connectors_tool_reports_already_running() -> None:
    from agentgraph.mcp.server import poll_connectors_tool

    class PollingConnector:
        source = "rss"
        poll_interval = object()

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors", return_value=[PollingConnector()]
        ),
        patch(
            "agentgraph.server.sync.schedule_poll_connector",
            new=AsyncMock(
                return_value={"source": "rss", "status": "already_running", "reason": None}
            ),
        ),
    ):
        result = await poll_connectors_tool()

    assert _mcp_data(result) == {"polled": [], "already_running": ["rss"], "skipped": []}


@pytest.mark.asyncio
async def test_mcp_poll_connectors_tool_reports_skipped_auth() -> None:
    from agentgraph.mcp.server import poll_connectors_tool

    class PollingConnector:
        source = "gmail"
        poll_interval = object()

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors", return_value=[PollingConnector()]
        ),
        patch(
            "agentgraph.server.sync.schedule_poll_connector",
            new=AsyncMock(
                return_value={
                    "source": "gmail",
                    "status": "skipped",
                    "reason": "authentication invalid: token expired",
                }
            ),
        ),
    ):
        result = await poll_connectors_tool()

    assert _mcp_data(result) == {
        "polled": [],
        "already_running": [],
        "skipped": [{"source": "gmail", "reason": "authentication invalid: token expired"}],
    }


@pytest.mark.asyncio
async def test_mcp_poll_connectors_tool_reports_unknown_source() -> None:
    from agentgraph.mcp.server import poll_connectors_tool

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=None),
    ):
        result = await poll_connectors_tool("missing")

    assert _mcp_error(result)["code"] == "connector_not_found"
