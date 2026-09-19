"""AgentGraph MCP server.

Exposes the knowledge graph as MCP tools so AI agents can search, retrieve, and
traverse entities directly.

Run via ``agentgraph mcp-serve``, which speaks stdio by default — the transport used by
ChatGPT Desktop Work Mode, Codex, Claude Desktop, and Claude Code. ``agentgraph
mcp-config`` prints the setup for each. Tools reach the graph through the same
transport the CLI uses; see ``agentgraph.query_client``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, cast

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel

from agentgraph.core.context import get_backend
from agentgraph.mcp.models import (
    EdgesData,
    EntityData,
    FetchData,
    GraphEdge,
    SearchData,
    SuccessData,
    ToolData,
    TraverseData,
    entity_from_record,
    entity_reference_from_record,
)
from agentgraph.perf import timed
from agentgraph.query_client import HttpQueryClient, QueryClient, resolve_query_client

logger = logging.getLogger(__name__)
MCP_INSTRUCTIONS = """AgentGraph is a local graph of selected messages, documents,
people, feeds, pages, and relationships. For source-backed questions, search broadly,
open promising entities for full content, traverse relevant relationships, and cite
source URLs or entity IDs. Search and query return bounded snippets; get returns the
full stored entity. Resolve or fetch stubs and stale context only when needed. Read
structured `data` on success; expected errors have an error flag and code. Source
operations may contact configured services and do not record human attention in
observed_at. Confirm destructive actions and Person merges with the user."""
mcp = FastMCP("AgentGraph", instructions=MCP_INSTRUCTIONS)

# --- Backend access -----------------------------------------------------------------
# Tools reach the graph the same way the CLI does, through AGENTGRAPH_QUERY_TRANSPORT:
# in-process against the database, or through the local server. Two things differ from
# the CLI, both because this process is long-lived rather than one-shot.

_transport_lock = asyncio.Lock()
_backend_lock = asyncio.Lock()
_client: QueryClient | None = None
_backend_started = False
# Strong refs to detached ingest tasks; without them the loop may collect a running
# task mid-sweep.
_ingest_tasks: set[asyncio.Task[None]] = set()


def _tool_result(data: BaseModel, *, is_error: bool = False) -> CallToolResult:
    """Return JSON text and structured content from one declared output schema."""
    payload = data.model_dump(mode="json")
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
        isError=is_error,
    )


def _success(data: Any) -> CallToolResult:
    return _tool_result(SuccessData[Any](data=data))


def _failure(code: str, message: str, recovery: str | None = None) -> CallToolResult:
    from agentgraph.mcp.models import ErrorData

    return _tool_result(ErrorData(code=code, message=message, recovery=recovery), is_error=True)


async def _ensure_backend() -> None:
    """Open the storage backend on first use.

    Deferred rather than opened at startup so a server-transport process never opens
    SQLite or imports the embedding model. The connector, demo, and poll-fallback tools
    still need it, so it cannot be skipped outright. Opening it here also means the
    connection is created on the loop that will use it.
    """
    global _backend_started
    async with _backend_lock:
        if _backend_started:
            return
        from agentgraph.core.context import set_backend
        from agentgraph.core.runtime import create_backend

        backend = create_backend()
        await backend.initialize()
        set_backend(backend)
        _backend_started = True
        logger.info("MCP opened the storage backend")


async def _query_client() -> QueryClient:
    """Return the resolved transport, resolving once and caching it."""
    global _client
    async with _transport_lock:
        if _client is None:
            _client = resolve_query_client()
            logger.info("MCP query transport: %s", _client.label)
        client = _client
    if client.needs_backend:
        await _ensure_backend()
    return client


async def _reset_client() -> None:
    global _client
    async with _transport_lock:
        _client = None


async def _with_client[T](operation: Callable[[QueryClient], Awaitable[T]]) -> T:
    """Run one graph operation, re-resolving the transport if the server has gone.

    The CLI resolves per invocation, so a server that stops between commands is picked
    up naturally. This process can hold a transport for days, so a server restart would
    otherwise leave every tool failing; on a connection error the cached transport is
    dropped and `auto` gets to fall back to in-process.
    """
    client = await _query_client()
    try:
        return await operation(client)
    except ConnectionError:
        if not isinstance(client, HttpQueryClient):
            raise
        logger.warning("Query transport %s is unreachable; re-resolving", client.label)
        await _reset_client()
        retried = await _query_client()
        return await operation(retried)


async def _queue_poll(connector: Any) -> dict[str, Any]:
    """Queue a poll on the server, falling back to running it here.

    The server owns the poll scheduler and its already-running registry, so queueing
    there matches the CLI and avoids two processes polling one connector at once. It
    also outlives this process, which a desktop MCP host may stop at any time.

    When no server is reachable the poll still runs locally, so a server-less setup
    behaves as it did before.
    """
    from agentgraph.cli_sync import queue_connector_poll

    try:
        return dict(await asyncio.to_thread(queue_connector_poll, connector.source))
    except ConnectionError:
        logger.info("No server reachable; polling %s in-process", connector.source)
    except ValueError as exc:
        return {"source": connector.source, "status": "skipped", "reason": str(exc)}

    from agentgraph.server.sync import schedule_poll_connector

    await _ensure_backend()
    return dict(await schedule_poll_connector(connector))


async def _queue_ingest(connector: Any, account_id: str | None) -> dict[str, Any]:
    """Queue a historical ingest on the server, falling back to running it here."""
    from agentgraph.cli_sync import queue_connector_ingest

    try:
        return dict(await asyncio.to_thread(queue_connector_ingest, connector.source, account_id))
    except ConnectionError:
        logger.info("No server reachable; ingesting %s in-process", connector.source)
    except ValueError as exc:
        return {"source": connector.source, "status": "skipped", "reason": str(exc)}

    from agentgraph.server.sync import run_ingest

    await _ensure_backend()
    account_ids = [account_id] if account_id else None
    # Detached on purpose: a full history sweep outlasts any tool call.
    task = asyncio.create_task(run_ingest(connector, account_ids=account_ids))
    _ingest_tasks.add(task)
    task.add_done_callback(_ingest_tasks.discard)
    return {"source": connector.source, "status": "started", "account_id": account_id}


def _tool_annotations(
    title: str,
    *,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
    open_world: bool,
) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )


def entity_type_catalog_description() -> str:
    """Format installed entity types for MCP tool discovery metadata."""
    from agentgraph.connectors.registry import get_entity_type_catalog

    lines = ["Entity types available in this MCP process:"]
    for name, descriptions in get_entity_type_catalog():
        details = "; ".join(
            description if source == "core" else f"{source}: {description}"
            for source, description in descriptions
        )
        lines.append(f"  - {name}: {details}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# list_connectors — connector discovery for agents
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=_tool_annotations(
        "List AgentGraph connectors",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=True,
    )
)
async def list_connectors_tool(
    verify: bool = False,
) -> Annotated[CallToolResult, ToolData[dict[str, Any]]]:
    """
    List all installed connectors and their capabilities.

    The equivalent ``agentgraph list-connectors`` CLI command renders these
    status fields as a table; this MCP tool returns the structured JSON rows.

    Use this when source availability, freshness, authentication, or valid
    platform values matter. Normal graph reads do not need to call it first.
    For search_entities_tool, scope a source with platform="...".
    Set verify=true only when a live provider credential check is needed.

    Returns:
        Structured data with an `items` list of connector objects, each with:
          - source: platform name to pass as the platform= argument
          - description: what this connector ingests
          - entity_types: connector-declared resource entity types, including core types,
            as name, resource_type, and description objects (Person identities are separate)
            An empty list means no declared resource types, shown as "-" in the CLI table.
          - auth_provider: shared auth provider key (e.g. "google"), or null
            for connectors that do not use credentials
          - auth_status: "ok" | "missing" | "invalid", or null when no auth is used
          - auth_detail: aggregate auth summary or error message; null if missing
          - auth_verified: true when credentials were live-checked with provider APIs
          - shared_auth: true when multiple connectors share the same auth provider
          - account_count: number of authenticated accounts for that provider
          - url_patterns: URL patterns this connector recognises
          - polls: true if this connector has its own background poll
          - poll_interval_seconds: direct poll interval, or null
          - poll_delegates: connector sources refreshed by this connector's poll
          - polled_by: connector sources whose poll refreshes this connector
          - sync: human-readable sync summary
          - last_synced_at: latest entity sync timestamp for this connector source, or null
          - last_sync: human-readable last-sync label
    """
    from agentgraph.connectors.registry import bootstrap, get_all_connectors
    from agentgraph.connectors.status import connector_status_items

    bootstrap()
    await _ensure_backend()
    all_connectors = get_all_connectors()
    result = await connector_status_items(all_connectors, get_backend(), verify=verify)
    return _success({"items": result})


@mcp.tool(
    annotations=_tool_annotations(
        "List AgentGraph authentication providers",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=True,
    )
)
async def list_auth_providers_tool(
    verify: bool = False,
) -> Annotated[CallToolResult, ToolData[dict[str, Any]]]:
    """
    List credential-backed authentication providers and their current account/auth state.

    Connectors that require only configuration, or no setup at all, are omitted.
    Use list_connectors_tool to inspect all installed connectors.

    Returns:
        Structured data with an `items` list of auth provider objects, each with:
          - provider: auth provider key such as "google" or "slack"
          - description: provider summary
          - connectors: connector sources that use this provider
          - shared: true when multiple connectors use the same provider
          - auth_status: "ok" | "missing" | "invalid"
          - auth_detail: aggregate auth summary or error message; null if missing
          - auth_verified: true when credentials were live-checked with provider APIs
          - accounts: authenticated account rows with account_id, label, workspace_id,
            email, auth_method, auth_status, and auth_detail
    """
    from agentgraph.connectors.registry import bootstrap, get_all_connectors
    from agentgraph.connectors.status import auth_provider_status_items

    bootstrap()
    result = await auth_provider_status_items(get_all_connectors(), verify=verify)
    return _success({"items": result})


@mcp.tool(
    annotations=_tool_annotations(
        "Remove AgentGraph provider credentials",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=False,
    )
)
async def remove_auth_provider_tool(
    provider: str, account_id: str | None = None
) -> Annotated[CallToolResult, ToolData[dict[str, Any]]]:
    """
    Remove stored credentials for an authentication provider.

    This is the MCP equivalent of:
        agentgraph auth remove <provider> [--account <account-id>]

    Removing credentials stops authenticated connector operations such as
    background polling, but does not delete already indexed graph data.

    Args:
        provider: Auth provider key such as "google", "slack", or "discord".
        account_id: Optional account ID. When omitted, all credentials for the
            provider are removed.

    Returns:
        Structured data with provider, removed, and account_id when supplied.
    """
    from agentgraph.auth.credentials import remove_platform, remove_platform_account

    removed = (
        remove_platform_account(provider, account_id)
        if account_id is not None
        else remove_platform(provider)
    )
    result: dict[str, object] = {
        "provider": provider,
        "removed": removed,
    }
    if account_id is not None:
        result["account_id"] = account_id
    return _success(result)


@mcp.tool(
    annotations=_tool_annotations(
        "Authenticate an AgentGraph provider",
        read_only=False,
        destructive=False,
        idempotent=False,
        open_world=True,
    )
)
async def authenticate_provider_tool(
    provider: str,
    args: list[str] | None = None,
    account_id: str | None = None,
    add: bool = False,
) -> Annotated[CallToolResult, ToolData[dict[str, Any]]]:
    """Authenticate a credential-backed provider through its connector-owned flow.

    This is the MCP equivalent of:
        agentgraph auth <provider> [--account <account-id>] [--add] [provider options]

    Slack accepts ``--method``, ``--client-id``, ``--xoxc-token``, and
    ``--d-cookie`` in ``args``. A Client ID implies OAuth; use the CLI for guided
    app setup.
    """
    import contextlib
    import io

    from agentgraph.connectors.registry import bootstrap, get_all_connectors
    from agentgraph.connectors.status import run_auth_provider_flow

    def _run() -> str:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            run_auth_provider_flow(
                get_all_connectors(),
                provider,
                account_id=account_id,
                add=add,
                args=args,
            )
        return output.getvalue()

    bootstrap()
    try:
        output = await asyncio.to_thread(_run)
    except ValueError as exc:
        return _failure("authentication_failed", str(exc))
    result: dict[str, object] = {"provider": provider, "authenticated": True}
    if output:
        result["output"] = output
    return _success(result)


@mcp.tool(
    annotations=_tool_annotations(
        "Run an AgentGraph connector command",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=True,
    )
)
async def run_connector_command_tool(
    source: str, args: list[str]
) -> Annotated[CallToolResult, ToolData[dict[str, Any]]]:
    """
    Run a connector-owned command.

    This is the MCP equivalent of:
        agentgraph connector <source> <args...>

    Core dispatches generically; the connector owns command names and parsing.

    Args:
        source: Connector source, e.g. "rss".
        args: Connector command and arguments. Use ["--help"] to discover
            commands for the selected source.

    Returns:
        Structured data with source and connector result, or an error.
    """
    from agentgraph.connectors.registry import bootstrap, get_connector, get_connector_load_error

    bootstrap()
    connector = get_connector(source)
    if connector is None:
        if error := get_connector_load_error(source):
            return _failure(
                "connector_load_failed", f"Failed to load connector {source!r}: {error}"
            )
        return _failure("connector_not_found", f"Unknown connector {source!r}")
    if args in (["--help"], ["help"]):
        return _success({"source": source, "help": type(connector).cli_help()})
    effects = None
    try:
        result = type(connector).run_cli_command(args)
        effects = type(connector).command_effects(args, result)
        if effects.reset_cursors:
            from agentgraph.connectors.command_effects import execute_cursor_resets

            await _ensure_backend()
            result["reset_cursors"] = await execute_cursor_resets(effects)
        if effects.delete_entities:
            from agentgraph.connectors.command_effects import execute_deletions

            await _ensure_backend()
            result["deleted_entities"] = await execute_deletions(effects)
        if effects.fetch_references:
            from agentgraph.connectors.command_effects import execute_fetches

            await _ensure_backend()
            result["fetched"] = await execute_fetches(effects)
        if effects.poll:
            result["poll"] = await _queue_poll(connector)
        if effects.ingest:
            result["ingest"] = await _queue_ingest(connector, effects.ingest_account_id)
        return _success({"source": source, "result": result})
    except (NotImplementedError, OSError, ValueError) as exc:
        hint = None
        if effects is not None:
            from agentgraph.connectors.command_effects import fetch_effect_error_hint

            hint = fetch_effect_error_hint(effects, exc, "mcp")
        error = f"{exc}\n{hint}" if hint else str(exc)
        return _failure("connector_command_failed", error)


@mcp.tool(
    annotations=_tool_annotations(
        "Add AgentGraph demo fixtures",
        read_only=False,
        destructive=False,
        idempotent=True,
        open_world=False,
    )
)
async def add_demo_tool() -> Annotated[CallToolResult, ToolData[dict[str, Any]]]:
    """Add the fictional Atlas demo fixtures to the configured database."""
    from agentgraph.config import get_config_paths
    from agentgraph.demo import add_demo

    await _ensure_backend()
    return _success(await add_demo(get_config_paths()[0]))


@mcp.tool(
    annotations=_tool_annotations(
        "Remove AgentGraph demo fixtures",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=False,
    )
)
async def remove_demo_tool() -> Annotated[CallToolResult, ToolData[dict[str, Any]]]:
    """Remove marked Atlas demo fixtures from the configured database."""
    from agentgraph.config import get_config_paths
    from agentgraph.demo import remove_demo

    await _ensure_backend()
    return _success(await remove_demo(get_config_paths()[0]))


async def _enrich_results(results: list[dict[str, Any]]) -> None:
    """Let each owning connector apply result presentation fixes in-place."""
    from agentgraph.connectors.registry import bootstrap, get_connector

    bootstrap()
    by_platform: dict[str, list[dict[str, Any]]] = {}
    for entity in results:
        platform = entity.get("platform")
        if isinstance(platform, str):
            by_platform.setdefault(platform, []).append(entity)

    async def _enrich_one(platform: str, entities: list[dict[str, Any]]) -> None:
        connector = get_connector(platform)
        if connector is None:
            return
        try:
            with timed("mcp.enrich_results", platform=platform, count=len(entities)):
                await connector.enrich_results(entities)
        except Exception:
            logger.exception("Connector %s failed to enrich MCP results", platform)

    if by_platform:
        await asyncio.gather(
            *(_enrich_one(platform, entities) for platform, entities in by_platform.items())
        )


# ---------------------------------------------------------------------------
# search_entities — hybrid vector + full-text, RRF fused
# ---------------------------------------------------------------------------


async def search_entities_tool(
    query: str | None = None,
    entity_types: list[str] | None = None,
    platform: str | None = None,
    filters: dict[str, Any] | None = None,
    since: str | None = None,
    authored_by_me: bool = False,
    has_attachments: bool = False,
    limit: int | None = None,
    order_by: str | None = None,
    min_score: float = 0.03,
    refresh: bool = False,
    observed_since: str | None = None,
) -> Annotated[CallToolResult, ToolData[SearchData]]:
    """
    Search or filter the knowledge graph.

    Use a query for ranked discovery. Omit it to list entities matching only
    filters, newest first. Results contain bounded snippets; use
    get_entity_tool before making source-based claims.

    To find chat uploads, use entity_types=["Message"] with
    has_attachments=True. Gmail attachments are Document stubs referenced by
    their Email entity.

    Args:
        query: Optional natural-language search query. Omit to select purely by
            the filters below.
        entity_types: Entity types to include.
        platform: Source to include.
        filters: Exact column or metadata key/value matches.
        since: Updated-at cutoff as an ISO timestamp or relative duration.
        observed_since: Observation cutoff using the same format as since.
        authored_by_me: Restrict to entities authored by the authenticated user.
        has_attachments: Restrict to entities with attachments.
        limit: Maximum results; defaults to 10 with a query and 50 without.
        order_by: Date field used for descending order.
        min_score: Relevance threshold for query search; default 0.03.
        refresh: Refresh connector-owned presentation metadata before returning.

    Returns:
        Structured data containing entities, returned, limit, and has_more.
    """
    # MCP clients send non-string filter values (numbers, bools), but every
    # predicate compares against text columns or JSON scalars.
    str_filters: dict[str, str] = {k: str(v) for k, v in (filters or {}).items()}
    resolved_limit = limit if limit is not None else (10 if query else 50)
    try:
        results = await _with_client(
            lambda client: client.search(
                query,
                entity_types,
                resolved_limit + 1,
                min_score,
                platform,
                filters=str_filters,
                since=since,
                observed_since=observed_since,
                authored_by_me=authored_by_me,
                has_attachments=has_attachments,
                order_by=order_by,
            )
        )
    except ValueError as exc:
        return _failure("invalid_search", str(exc))
    has_more = len(results) > resolved_limit
    results = results[:resolved_limit]
    if refresh:
        await _enrich_results(results)
    return _success(
        SearchData(
            entities=[entity_from_record(result) for result in results],
            limit=resolved_limit,
            returned=len(results),
            has_more=has_more,
        )
    )


mcp.tool(
    description=(
        f"{inspect.cleandoc(search_entities_tool.__doc__ or '')}\n\n"
        f"{entity_type_catalog_description()}"
    ),
    annotations=_tool_annotations(
        "Search AgentGraph entities",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=True,
    ),
)(search_entities_tool)


# ---------------------------------------------------------------------------
# get_entity — full entity by UUID
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=_tool_annotations(
        "Get a full AgentGraph entity",
        read_only=False,
        destructive=False,
        idempotent=True,
        open_world=True,
    )
)
async def get_entity_tool(
    entity_id: str, resolve: bool = False
) -> Annotated[CallToolResult, ToolData[EntityData]]:
    """
    Retrieve full details for a single existing entity.

    Args:
        entity_id: Entity UUID, unambiguous UUID prefix, platform ref, or indexed
            HTTP(S) URL.
        resolve: If true and the entity is a stub, fetch it through its owning
            connector before returning. Defaults to false.

    Returns:
        Structured data with the entity, or an error if it is not found.
    """
    try:
        entity = await _with_client(lambda client: client.get_entity(entity_id, resolve))
        if entity is None:
            return _failure("entity_not_found", f"Entity {entity_id!r} not found")
        return _success(EntityData(entity=entity_from_record(entity)))
    except ValueError as exc:
        return _failure("invalid_entity_reference", str(exc))


# ---------------------------------------------------------------------------
# get_edges — edges connected to an entity
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=_tool_annotations(
        "List AgentGraph entity edges",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=False,
    )
)
async def get_edges_tool(
    entity_id: str,
    edge_type: str | None = None,
    direction: str = "both",
) -> Annotated[CallToolResult, ToolData[EdgesData]]:
    """
    List edges connected to an entity.

    Args:
        entity_id: Entity UUID, unambiguous UUID prefix, or platform ref.
        edge_type: Optional edge type filter (e.g. "authored", "posted_in",
            "replied_to", "mentions", "collaborated").
        direction: "in" (incoming), "out" (outgoing), or "both" (default).

    Returns:
        Structured data with the canonical entity reference and its edges.
    """
    try:
        entity, edges = await _with_client(
            lambda client: client.edges(entity_id, edge_type, direction)
        )
        if entity is None:
            return _failure("entity_not_found", f"Entity {entity_id!r} not found")
        return _success(
            EdgesData(
                entity=entity_reference_from_record(entity),
                edges=[GraphEdge.model_validate(edge) for edge in edges],
            )
        )
    except ValueError as exc:
        return _failure("invalid_entity_reference", str(exc))


# ---------------------------------------------------------------------------
# traverse_graph — BFS neighbourhood
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=_tool_annotations(
        "Traverse the AgentGraph neighborhood",
        read_only=False,
        destructive=False,
        idempotent=True,
        open_world=True,
    )
)
async def traverse_graph_tool(
    entity_id: str,
    max_depth: int = 2,
    resolve: bool = False,
) -> Annotated[CallToolResult, ToolData[TraverseData]]:
    """
    Traverse the knowledge graph from a starting entity using BFS.

    Useful for discovering the context around a message or document:
    who authored it, which channel it appeared in, what it references, etc.

    Args:
        entity_id: Entity UUID, unambiguous UUID prefix, or platform ref.
        max_depth: Maximum number of hops to traverse (default 2, max 4).
            A depth of 0 returns only the starting entity.
        resolve: If true, fetch stub nodes through their owning connectors and
            repeat the traversal before returning. Defaults to false.

    Returns:
        Structured data with nodes, edges, and the applied max_depth.
    """
    try:
        depth = min(max(max_depth, 0), 4)
        entity, result = await _with_client(
            lambda client: client.traverse(entity_id, depth, resolve)
        )
        if entity is None:
            return _failure("entity_not_found", f"Entity {entity_id!r} not found")

        # Trim content on nodes to keep response size manageable.
        for node in result.get("nodes", []):
            if node.get("content") and len(str(node["content"])) > 300:
                node["content"] = str(node["content"])[:300] + "…"
                node["content_truncated"] = True
            else:
                node["content_truncated"] = False
        return _success(
            TraverseData(
                nodes=[entity_from_record(node) for node in result.get("nodes", [])],
                edges=[GraphEdge.model_validate(edge) for edge in result.get("edges", [])],
                max_depth=depth,
            )
        )
    except ValueError as exc:
        return _failure("invalid_entity_reference", str(exc))


# ---------------------------------------------------------------------------
# fetch_entity — trigger connector re-ingestion
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=_tool_annotations(
        "Fetch an AgentGraph source resource",
        read_only=False,
        destructive=False,
        idempotent=True,
        open_world=True,
    )
)
async def fetch_entity_tool(
    platform: str, resource_id: str
) -> Annotated[CallToolResult, ToolData[FetchData]]:
    """
    Trigger a connector fetch for a platform entity.

    Forces re-ingestion of a specific resource from its source platform,
    persisting updated content, people, and edges before returning.

    Args:
        platform: Platform name (e.g. "gdocs", "slack", "discord", "gmail", "rss").
        resource_id: Platform-specific entity ID.

    Returns:
        Structured data with a canonical entity reference and ingestion counts.
    """
    try:
        result = await _with_client(lambda client: client.fetch(platform, resource_id))
        entity = result.get("entity")
        return _success(
            FetchData(
                entity=entity_reference_from_record(cast(dict[str, Any], entity))
                if isinstance(entity, dict)
                else None,
                entities=int(result["entities"]),
                metadata_patches=int(result["metadata_patches"]),
                persons=int(result["persons"]),
                edges=int(result["edges"]),
            )
        )
    except ValueError as exc:
        from agentgraph.connectors.registry import get_connector

        connector = get_connector(platform)
        hint = (
            connector.fetch_error_hint(resource_id, exc, "mcp") if connector is not None else None
        )
        error = f"{exc}\n{hint}" if hint else str(exc)
        return _failure("fetch_failed", error)


# ---------------------------------------------------------------------------
# fetch_entity_by_id — re-ingest by internal UUID
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=_tool_annotations(
        "Refresh an AgentGraph entity",
        read_only=False,
        destructive=False,
        idempotent=True,
        open_world=True,
    )
)
async def fetch_entity_by_id_tool(entity_id: str) -> Annotated[CallToolResult, ToolData[FetchData]]:
    """
    Trigger a connector fetch for an entity by its internal UUID.

    Looks up the entity's platform and platform-specific ID, then forces
    re-ingestion from the source platform and persists the returned batch
    before returning.

    Args:
        entity_id: Internal entity UUID (the id field from graph nodes).

    Returns:
        Structured data with a canonical entity reference and ingestion counts.
    """
    try:
        result = await _with_client(lambda client: client.fetch_entity(entity_id))
        entity = result.get("entity")
        return _success(
            FetchData(
                entity=entity_reference_from_record(cast(dict[str, Any], entity))
                if isinstance(entity, dict)
                else None,
                entities=int(result["entities"]),
                metadata_patches=int(result["metadata_patches"]),
                persons=int(result["persons"]),
                edges=int(result["edges"]),
            )
        )
    except ValueError as exc:
        return _failure("fetch_failed", str(exc))


# ---------------------------------------------------------------------------
# poll_connectors — trigger background connector polling
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=_tool_annotations(
        "Poll AgentGraph connectors",
        read_only=False,
        destructive=False,
        idempotent=False,
        open_world=True,
    )
)
async def poll_connectors_tool(
    source: str | None = None,
) -> Annotated[CallToolResult, ToolData[dict[str, Any]]]:
    """
    Trigger a background poll for one connector or all polling connectors.

    This is the MCP equivalent of:
        agentgraph poll [<source>]

    Args:
        source: Optional connector source to poll. When omitted, all connectors
            with poll_interval configured are polled.

    Returns:
        Structured data with queued, already-running, and skipped connector sources.
    """
    from agentgraph.connectors.registry import bootstrap, get_all_connectors, get_connector

    bootstrap()
    if source is not None:
        connector = get_connector(source)
        if connector is None:
            return _failure("connector_not_found", f"No connector registered for source {source!r}")
        connectors = [connector]
    else:
        connectors = get_all_connectors()

    polled: list[str] = []
    already_running: list[str] = []
    skipped: list[dict[str, str | None]] = []
    for connector in connectors:
        if connector.poll_interval is None:
            continue
        result = await _queue_poll(connector)
        if result["status"] == "queued":
            polled.append(connector.source)
        elif result["status"] == "already_running":
            already_running.append(connector.source)
        else:
            skipped.append({"source": connector.source, "reason": result["reason"]})

    return _success({"polled": polled, "already_running": already_running, "skipped": skipped})


# ---------------------------------------------------------------------------
# download_entity — authenticated source-file download
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=_tool_annotations(
        "Download an AgentGraph source file",
        read_only=False,
        destructive=False,
        idempotent=True,
        open_world=True,
    )
)
async def download_entity_tool(
    entity_id: str, output_path: str | None = None
) -> Annotated[CallToolResult, ToolData[dict[str, Any]]]:
    """
    Download an entity's source file using the connector's stored auth.

    Supports entity UUIDs, UUID prefixes, and platform refs such as
    "gdrive/file-id" or "gmail/document/attachment/<message-id>/<attachment-id>"
    when those resolve to a graph entity. The file is written to output_path
    when supplied, or to the MCP server's current directory using the source
    filename.

    Args:
        entity_id: Entity UUID, UUID prefix, or platform/entity_id reference.
        output_path: Optional output file path or directory.

    Returns:
        Structured data with path, byte count, filename, platform, and MIME type.
    """
    try:
        result = await _with_client(lambda client: client.download(entity_id, output_path))
        return _success(result)
    except ValueError as exc:
        return _failure("download_failed", str(exc))


# ---------------------------------------------------------------------------
# bookmark_entity — protect an entity from expiration
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=_tool_annotations(
        "Set AgentGraph bookmark protection",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    )
)
async def bookmark_entity_tool(
    entity_id: str, bookmarked: bool = True
) -> Annotated[CallToolResult, ToolData[dict[str, Any]]]:
    """
    Set or remove bookmark protection for an entity or URL.

    Supports entity UUIDs, UUID prefixes, and platform refs such as
    "gdrive/file-id" when those resolve to a graph entity. HTTP(S) URLs are
    fetched through an owning connector when possible when adding a bookmark,
    otherwise through the generic web connector.

    Args:
        entity_id: Entity UUID, UUID prefix, platform/entity_id reference, or URL.
        bookmarked: True to add bookmark protection; false to remove it.

    Returns:
        Structured data with the updated entity and its bookmark state.
    """
    try:
        result = await _with_client(lambda client: client.bookmark(entity_id, bookmarked))
        return _success({"entity": entity_from_record(result).model_dump(mode="json")})
    except ValueError as exc:
        return _failure("bookmark_failed", str(exc))


# ---------------------------------------------------------------------------
# delete_entity — remove an entity
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=_tool_annotations(
        "Delete an AgentGraph entity",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )
)
async def delete_entity_tool(entity_id: str) -> Annotated[CallToolResult, ToolData[dict[str, Any]]]:
    """
    Delete an entity from the graph.

    Supports entity UUIDs, UUID prefixes, platform refs such as
    "gdrive/file-id", and HTTP(S) URLs when those resolve to a graph entity.
    Connected edges are deleted with the entity.

    Args:
        entity_id: Entity UUID, UUID prefix, platform/entity_id reference, or URL.

    Returns:
        Structured data with deleted and the deleted entity.
    """
    try:
        result = await _with_client(lambda client: client.delete(entity_id))
        entity = result.get("entity")
        return _success(
            {
                "deleted": bool(result.get("deleted")),
                "entity": entity_from_record(cast(dict[str, Any], entity)).model_dump(mode="json")
                if isinstance(entity, dict)
                else None,
            }
        )
    except ValueError as exc:
        return _failure("delete_failed", str(exc))


@mcp.tool(
    annotations=_tool_annotations(
        "Delete multiple AgentGraph entities",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )
)
async def delete_entities_tool(
    entity_ids: list[str],
) -> Annotated[CallToolResult, ToolData[dict[str, Any]]]:
    """
    Atomically delete multiple entities from the graph.

    Every target must resolve before any entity is deleted. Targets support entity UUIDs,
    UUID prefixes, platform refs, and indexed HTTP(S) URLs. Duplicate references to the
    same entity are deleted once. Connected edges are deleted with their entity.

    Args:
        entity_ids: One or more entity UUIDs, UUID prefixes, platform references, or URLs.

    Returns:
        Structured data with the deleted count and compact deleted entity references.
    """
    try:
        result = await _with_client(lambda client: client.delete_many(entity_ids))
        return _success(result)
    except ValueError as exc:
        return _failure("delete_failed", str(exc))


# ---------------------------------------------------------------------------
# unify_persons — manually merge duplicate Person entities
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=_tool_annotations(
        "Unify duplicate AgentGraph people",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )
)
async def unify_persons_tool(
    primary_entity_id: str,
    duplicate_entity_ids: list[str],
) -> Annotated[CallToolResult, ToolData[dict[str, Any]]]:
    """
    Merge duplicate Person entities that refer to the same human.

    Use this only when the user has confirmed that the Person entities are the
    same person. The primary Person keeps its ID; edges from duplicate Persons
    are rewired to the primary; duplicate metadata such as platform user IDs is
    folded into the primary; duplicate Person entities are removed.

    Args:
        primary_entity_id: Person entity ID, UUID prefix, or platform ref to keep.
        duplicate_entity_ids: Duplicate Person entity IDs, UUID prefixes, or
            platform refs to merge into the primary.

    Returns:
        Structured data with the updated primary Person and merged duplicate IDs.
    """
    try:
        result = await _with_client(
            lambda client: client.unify_persons(primary_entity_id, duplicate_entity_ids)
        )
        primary = result.get("primary")
        return _success(
            {
                "primary": entity_from_record(cast(dict[str, Any], primary)).model_dump(mode="json")
                if isinstance(primary, dict)
                else None,
                "merged_ids": result.get("merged_ids", []),
                "merged_count": result.get("merged_count", 0),
            }
        )
    except ValueError as exc:
        return _failure("person_merge_failed", str(exc))
