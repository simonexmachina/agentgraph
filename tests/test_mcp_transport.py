"""The MCP server reaches the graph the same way the CLI does.

Two things differ from the CLI, both because this process is long-lived rather than
one-shot: the resolved transport is cached, and it is re-resolved when the server it
was pointing at goes away.
"""

from __future__ import annotations

# pyright: reportPrivateUsage=false, reportUnusedFunction=false
from collections.abc import Iterator
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agentgraph.mcp import server as mcp_server
from agentgraph.query_client import HttpQueryClient, InProcessQueryClient


@pytest.fixture(autouse=True)
def _reset_module_state() -> Iterator[None]:
    """The transport and backend are process-wide, so clear them between tests."""
    mcp_server._client = None
    mcp_server._backend_started = False
    yield
    mcp_server._client = None
    mcp_server._backend_started = False


@pytest.mark.asyncio
async def test_transport_is_resolved_once_and_cached() -> None:
    calls: list[int] = []

    def resolve() -> InProcessQueryClient:
        calls.append(1)
        return InProcessQueryClient()

    with (
        patch.object(mcp_server, "resolve_query_client", resolve),
        patch.object(mcp_server, "_ensure_backend", AsyncMock()),
    ):
        first = await mcp_server._query_client()
        second = await mcp_server._query_client()

    assert first is second
    assert len(calls) == 1, "a long-lived server must not re-probe on every tool call"


@pytest.mark.asyncio
async def test_server_transport_does_not_open_a_backend() -> None:
    """The point of the server transport: no SQLite, no embedding model in-process."""
    ensure = AsyncMock()
    http_client = HttpQueryClient("http://127.0.0.1:8765")

    with (
        patch.object(mcp_server, "resolve_query_client", lambda: http_client),
        patch.object(mcp_server, "_ensure_backend", ensure),
    ):
        client = await mcp_server._query_client()

    assert client is http_client
    ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_in_process_transport_opens_the_backend() -> None:
    ensure = AsyncMock()

    with (
        patch.object(mcp_server, "resolve_query_client", InProcessQueryClient),
        patch.object(mcp_server, "_ensure_backend", ensure),
    ):
        await mcp_server._query_client()

    ensure.assert_awaited_once()


@pytest.mark.asyncio
async def test_transport_is_re_resolved_when_the_server_goes_away() -> None:
    """A desktop MCP session can outlive several server restarts."""
    resolved: list[Any] = [
        HttpQueryClient("http://127.0.0.1:8765"),
        InProcessQueryClient(),
    ]

    with (
        patch.object(mcp_server, "resolve_query_client", lambda: resolved.pop(0)),
        patch.object(mcp_server, "_ensure_backend", AsyncMock()),
    ):
        attempts: list[str] = []

        async def operation(client: Any) -> str:
            attempts.append(type(client).__name__)
            if isinstance(client, HttpQueryClient):
                raise ConnectionError("server is not available")
            return "ok"

        assert await mcp_server._with_client(operation) == "ok"

    assert attempts == ["HttpQueryClient", "InProcessQueryClient"]


@pytest.mark.asyncio
async def test_in_process_connection_error_is_not_retried() -> None:
    """Only an unreachable server warrants re-resolving; a local error is real."""
    with (
        patch.object(mcp_server, "resolve_query_client", InProcessQueryClient),
        patch.object(mcp_server, "_ensure_backend", AsyncMock()),
    ):
        attempts: list[str] = []

        async def operation(client: Any) -> str:
            attempts.append(type(client).__name__)
            raise ConnectionError("disk gone")

        with pytest.raises(ConnectionError):
            await mcp_server._with_client(operation)

    assert attempts == ["InProcessQueryClient"]


@pytest.mark.asyncio
async def test_poll_is_queued_on_the_server_when_one_is_reachable() -> None:
    """The server owns the poll scheduler, so queueing there matches the CLI."""
    connector = type("C", (), {"source": "rss"})()
    queued = {"source": "rss", "status": "queued", "reason": None}

    with (
        patch("agentgraph.cli_sync.queue_connector_poll", return_value=queued) as queue,
        patch("agentgraph.server.sync.schedule_poll_connector", new=AsyncMock()) as local,
    ):
        result = await mcp_server._queue_poll(connector)

    assert result == queued
    queue.assert_called_once_with("rss")
    local.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_falls_back_in_process_without_a_server() -> None:
    """A server-less install must keep polling, as it did before."""
    connector = type("C", (), {"source": "rss"})()
    local_result = {"source": "rss", "status": "queued", "reason": None}

    with (
        patch(
            "agentgraph.cli_sync.queue_connector_poll",
            side_effect=ConnectionError("not available"),
        ),
        patch(
            "agentgraph.server.sync.schedule_poll_connector",
            new=AsyncMock(return_value=local_result),
        ) as local,
        patch.object(mcp_server, "_ensure_backend", AsyncMock()),
    ):
        result = await mcp_server._queue_poll(connector)

    assert result == local_result
    local.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_reports_a_server_rejection_as_skipped() -> None:
    connector = type("C", (), {"source": "rss"})()

    with patch(
        "agentgraph.cli_sync.queue_connector_poll",
        side_effect=ValueError("No connector registered for source 'rss'"),
    ):
        result = await mcp_server._queue_poll(connector)

    assert result["status"] == "skipped"
    assert "No connector registered" in str(result["reason"])


@pytest.mark.asyncio
async def test_tools_use_the_resolved_transport() -> None:
    """A tool must go through the transport, not import the graph layer directly."""
    from agentgraph.mcp.server import get_entity_tool

    entity = {
        "id": "e1",
        "entity_type": "Document",
        "platform": "web",
        "platform_entity_id": "https://example.com/page",
        "created_at": None,
        "updated_at": None,
    }
    client = HttpQueryClient(
        "http://t",
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=entity)),
    )

    with patch.object(mcp_server, "resolve_query_client", lambda: client):
        result = await get_entity_tool("e1")

    assert result.isError is False
    structured = result.structuredContent
    assert isinstance(structured, dict)
    data = cast(dict[str, Any], structured["data"])
    returned = cast(dict[str, Any], data["entity"])
    assert returned["id"] == entity["id"]
    assert returned["platform_entity_id"] == entity["platform_entity_id"]
    assert returned["is_stub"] is True
