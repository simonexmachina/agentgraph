"""Observation cutoffs apply consistently across search modes and transports."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from agentgraph.backends.sqlite.backend import SQLiteBackend
from agentgraph.backends.sqlite.vector import pack_embedding
from agentgraph.cli import app
from agentgraph.connectors.base import EntityBatch, EntityRecord
from agentgraph.core.context import clear_backend, set_backend
from agentgraph.graph.query import search_entities
from agentgraph.query_client import HttpQueryClient, InProcessQueryClient, QueryClient
from agentgraph.server.app import app as server_app


@pytest.mark.parametrize(
    ("value", "delta"),
    [("30m", timedelta(minutes=30)), ("12h", timedelta(hours=12)), ("2d", timedelta(days=2))],
)
async def test_observed_since_parses_relative_cutoff(value: str, delta: timedelta) -> None:
    backend = MagicMock(search_entities=AsyncMock(return_value=[]))
    set_backend(backend)
    before = datetime.now(UTC)

    await search_entities(observed_since=value)

    cutoff = backend.search_entities.call_args.kwargs["observed_since"]
    assert before - delta <= cutoff <= datetime.now(UTC) - delta
    assert backend.search_entities.call_args.kwargs["since"] is None


async def test_observed_since_parses_iso_and_preserves_since() -> None:
    backend = MagicMock(search_entities=AsyncMock(return_value=[]))
    set_backend(backend)

    await search_entities(observed_since="2026-09-09T10:00:00+10:00", since="2026-09-08T00:00:00Z")

    kwargs = backend.search_entities.call_args.kwargs
    assert kwargs["observed_since"] == datetime(2026, 9, 9, tzinfo=UTC)
    assert kwargs["since"] == datetime(2026, 9, 8, tzinfo=UTC)


async def test_invalid_observed_since_does_not_query_backend() -> None:
    backend = MagicMock(search_entities=AsyncMock(return_value=[]))
    set_backend(backend)

    with pytest.raises(ValueError):
        await search_entities(observed_since="invalid")

    backend.search_entities.assert_not_awaited()


@pytest.mark.parametrize("query", [None, "alpha"])
def test_cli_forwards_observed_since(query: str | None) -> None:
    args = ["search", *([query] if query else []), "--observed-since", "2d", "--json"]
    with (
        patch("agentgraph.cli_query._run_with_client", side_effect=_run_search),
        patch.object(InProcessQueryClient, "search", new=AsyncMock(return_value=[])) as search,
    ):
        result = CliRunner().invoke(app, args)

    assert result.exit_code == 0, result.output
    search.assert_awaited_once()
    assert search.call_args.args[0] == query
    assert search.call_args.kwargs["observed_since"] == "2d"


def _run_search(operation: Callable[[QueryClient], Awaitable[object]]) -> object:
    # The CLI owns the event loop; exercise its operation without opening a database.
    async def run() -> object:
        return await operation(InProcessQueryClient())

    return asyncio.run(run())


@pytest.mark.parametrize("query", [None, "alpha"])
async def test_mcp_forwards_observed_since(query: str | None) -> None:
    from agentgraph.mcp import server

    client = InProcessQueryClient()
    with (
        patch.object(server, "_query_client", new=AsyncMock(return_value=client)),
        patch.object(client, "search", new=AsyncMock(return_value=[])) as search,
    ):
        result = await server.search_entities_tool(query, observed_since="2d", since="12h")

    assert result.isError is False
    assert result.structuredContent == {
        "status": "ok",
        "data": {
            "entities": [],
            "limit": 10 if query else 50,
            "returned": 0,
            "has_more": False,
        },
    }
    assert search.call_args.kwargs["observed_since"] == "2d"
    assert search.call_args.kwargs["since"] == "12h"


@pytest.fixture
async def observed_backend() -> AsyncIterator[SQLiteBackend]:
    backend = SQLiteBackend(":memory:", vector_mode="numpy")
    await backend.initialize()
    set_backend(backend)
    # Recent update times deliberately disagree with observation times.
    stamps = {
        "recent": ("2026-09-10T00:00:00Z", "2026-09-10T00:00:00Z"),
        "boundary": ("2026-09-09T00:00:00Z", "2026-09-01T00:00:00Z"),
        "old": ("2026-09-08T23:59:59Z", "2026-09-11T00:00:00Z"),
        "never": (None, "2026-09-11T00:00:00Z"),
    }
    try:
        await backend.upsert_batch(
            EntityBatch(
                entities=[
                    EntityRecord(
                        entity_type="Document",
                        platform="web",
                        platform_entity_id=name,
                        title="Alpha",
                        content="alpha content",
                        metadata={"web_url": f"https://example.com/{name}"},
                    )
                    for name in stamps
                ]
            ),
            person_embeddings={},
            entity_embeddings={},
        )
        conn = backend._conn_or_raise()
        for name, (observed, updated) in stamps.items():
            await conn.execute(
                "UPDATE entities SET observed_at = ?, updated_at = ?, content_embedding = ? "
                "WHERE platform_entity_id = ?",
                (observed, updated, pack_embedding([1.0, 0.0]), name),
            )
        await conn.commit()
        yield backend
    finally:
        clear_backend()
        await backend.close()


@pytest.mark.integration
@pytest.mark.parametrize("query", [None, "alpha", "semantic"])
async def test_observed_cutoff_filters_before_limits_in_every_search_mode(
    observed_backend: SQLiteBackend,
    query: str | None,
) -> None:
    backend = observed_backend
    vector = [1.0, 0.0] if query else None
    cutoff = datetime(2026, 9, 9, tzinfo=UTC)

    results = await backend.search_entities(
        vector, query, None, 10, 0.0, observed_since=cutoff, order_by="observed_at"
    )
    assert [row["platform_entity_id"] for row in results] == ["recent", "boundary"]

    combined = await backend.search_entities(
        vector, query, None, 10, 0.0, observed_since=cutoff, since=cutoff
    )
    assert [row["platform_entity_id"] for row in combined] == ["recent"]

    # The old/never-observed rows have the newest update times and would displace
    # eligible rows if a cutoff were applied after taking the requested limit.
    limited = await backend.search_entities(
        vector, query, None, 1, 0.0, observed_since=cutoff, order_by="updated_at"
    )
    assert len(limited) == 1
    assert limited[0]["platform_entity_id"] in {"recent", "boundary"}

    unfiltered = await backend.search_entities(vector, query, None, 10, 0.0)
    assert len(unfiltered) == 4


@pytest.mark.integration
@pytest.mark.parametrize("query", [None, "alpha", "semantic"])
@pytest.mark.parametrize("cutoff", ["2026-09-09T10:00:00+10:00", "2d"])
async def test_observed_cutoff_matches_across_transports(
    observed_backend: SQLiteBackend,
    query: str | None,
    cutoff: str,
) -> None:
    local = InProcessQueryClient()
    remote = HttpQueryClient("http://test", transport=httpx.ASGITransport(app=server_app))
    with (
        patch("agentgraph.graph.query._cached_query_embedding", return_value=(1.0, 0.0)),
        patch("agentgraph.graph.query.datetime", wraps=datetime) as clock,
    ):
        clock.now.return_value = datetime(2026, 9, 11, tzinfo=UTC)
        local_results = await local.search(
            query, None, 10, 0.0, None, observed_since=cutoff, order_by="observed_at"
        )
        remote_results = await remote.search(
            query, None, 10, 0.0, None, observed_since=cutoff, order_by="observed_at"
        )

    assert local_results == remote_results
    assert [row["platform_entity_id"] for row in local_results] == ["recent", "boundary"]
