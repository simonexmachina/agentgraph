"""CLI graph commands, run in-process or through the local server.

Which transport is used comes from ``AGENTGRAPH_QUERY_TRANSPORT``; see
``agentgraph.query_client``. The transport is resolved before any backend is opened,
so the server path never touches SQLite or imports the embedding model.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from rich.console import Console
from rich.table import Table

from agentgraph.core.runtime import backend_context
from agentgraph.graph.operations import is_stub
from agentgraph.query_client import QueryClient, resolve_query_client

console = Console()


async def _with_backend[T](operation: Callable[[], Awaitable[T]]) -> T:
    from agentgraph.connectors.registry import bootstrap

    bootstrap()
    async with backend_context():
        return await operation()


def _report_failure(
    exc: Exception,
    error_hint: Callable[[Exception], str | None] | None,
) -> None:
    console.print("[red]AgentGraph command failed:[/red] ", end="")
    console.print(str(exc), markup=False, highlight=False)
    if error_hint is None:
        return
    # Hints resolve connectors, which the server transport has not bootstrapped.
    # Only pay for that import when a command has actually failed.
    from agentgraph.connectors.registry import bootstrap

    bootstrap()
    hint = error_hint(exc)
    if hint:
        console.print(hint, markup=False, highlight=False)


def _run[T](
    operation: Callable[[], Awaitable[T]],
    error_hint: Callable[[Exception], str | None] | None = None,
) -> T:
    """Run one graph operation in-process with a configured backend."""
    try:
        return asyncio.run(_with_backend(operation))
    except Exception as exc:
        _report_failure(exc, error_hint)
        raise SystemExit(1) from exc


def _run_with_client[T](
    operation: Callable[[QueryClient], Awaitable[T]],
    error_hint: Callable[[Exception], str | None] | None = None,
) -> T:
    """Resolve the transport, then run one operation through it."""
    try:
        client = resolve_query_client()
    except ConnectionError as exc:
        console.print(str(exc), markup=False, highlight=False)
        raise SystemExit(1) from exc

    async def run() -> T:
        if not client.needs_backend:
            return await operation(client)
        return await _with_backend(lambda: operation(client))

    try:
        return asyncio.run(run())
    except Exception as exc:
        _report_failure(exc, error_hint)
        raise SystemExit(1) from exc


def run_graph_operation[T](
    operation: Callable[[], Awaitable[T]],
    error_hint: Callable[[Exception], str | None] | None = None,
) -> T:
    """Run a graph operation in-process, for callers holding their own coroutines."""
    return _run(operation, error_hint=error_hint)


# Ranked search returns a tight, hand-inspectable set; a filter-only listing is a
# browse, so it gets the wider window the removed `query` command used.
_RANKED_LIMIT = 10
_FILTER_LIMIT = 50


def cmd_search(
    query: str | None = None,
    entity_types: list[str] | None = None,
    limit: int | None = None,
    min_score: float = 0.03,
    as_json: bool = False,
    platform: str | None = None,
    filters: dict[str, str] | None = None,
    since: str | None = None,
    authored_by_me: bool = False,
    has_attachments: bool = False,
    order_by: str | None = None,
    observed_since: str | None = None,
) -> None:
    # An empty or whitespace-only argument is an absent query, not a query for
    # nothing: this keeps the limit default, the renderer, and the backend's
    # `query_text is None` branch all agreeing on which mode we are in.
    query = query.strip() or None if query is not None else None
    resolved_limit = limit if limit is not None else (
        _RANKED_LIMIT if query else _FILTER_LIMIT
    )

    async def operation(client: QueryClient) -> list[dict[str, Any]]:
        return await client.search(
            query,
            entity_types or None,
            resolved_limit,
            min_score,
            platform,
            filters=filters,
            since=since,
            observed_since=observed_since,
            authored_by_me=authored_by_me,
            has_attachments=has_attachments,
            order_by=order_by,
        )

    results = _run_with_client(operation)

    if as_json:
        console.print_json(json.dumps(results, default=str))
        return
    if not results:
        console.print("[dim]No results.[/dim]")
        return

    title = f'Search: "{query}"' if query else "Entities"
    table = Table(title=title, show_lines=True)
    table.add_column("ID", style="dim", no_wrap=True, max_width=8)
    table.add_column("Type")
    table.add_column("Platform")
    table.add_column("Title / Content", ratio=1)
    # Without a query there are no relevance scores to show.
    if query:
        table.add_column("Score", justify="right")
    for result in results:
        snippet = (result.get("title") or result.get("content") or "")[:120]
        row = [
            str(result["id"])[:8],
            str(result["entity_type"]),
            str(result["platform"]),
            str(snippet),
        ]
        if query:
            row.append(f"{result['score']:.4f}" if result.get("score") else "—")
        table.add_row(*row)
    console.print(table)


def _print_field(label: str, value: object) -> None:
    console.print(f"\n[bold]{label}:[/bold] ", end="")
    console.print(str(value), markup=False, highlight=False)


def _print_entity(entity: dict[str, Any]) -> None:
    """Render an entity in the detail format used by ``agentgraph get``."""
    console.print(f"[bold]{entity['entity_type']}[/bold] — {entity['platform']}")
    console.print(f"[dim]{entity['id']}[/dim]")
    if entity.get("title"):
        _print_field("Title", entity["title"])
    if entity.get("content"):
        console.print("\n[bold]Content:[/bold]")
        console.print(str(entity["content"]), markup=False, highlight=False)
    if entity.get("metadata"):
        _print_field("Metadata", entity["metadata"])


def cmd_get(entity_id: str, as_json: bool, resolve: bool = False) -> None:
    async def operation(client: QueryClient) -> dict[str, Any] | None:
        return await client.get_entity(entity_id, resolve)

    entity = _run_with_client(operation)
    if entity is None:
        console.print(f"[red]Entity {entity_id!r} not found.[/red]")
        return
    if as_json:
        console.print_json(json.dumps(entity, default=str))
        return
    _print_entity(entity)


def cmd_edges(
    entity_id: str,
    edge_type: str | None,
    direction: str,
    as_json: bool,
) -> None:
    async def operation(
        client: QueryClient,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        return await client.edges(entity_id, edge_type, direction)

    entity, edges = _run_with_client(operation)
    if entity is None:
        console.print(f"[red]Entity {entity_id!r} not found.[/red]")
        return
    if as_json:
        console.print_json(json.dumps(edges, default=str))
        return
    if not edges:
        console.print("[dim]No edges found.[/dim]")
        return

    canonical_id = str(entity["id"])
    table = Table(title=f"Edges for {canonical_id[:8]}…", show_lines=True)
    table.add_column("Type")
    table.add_column("Direction")
    table.add_column("Other end")
    table.add_column("Platform")
    for edge in edges:
        if edge.get("source_entity_id") == canonical_id:
            direction_label = "→ out"
            other = edge.get("target_ref") or edge.get("target_entity_id") or "?"
        else:
            direction_label = "← in"
            other = edge.get("source_ref") or edge.get("source_entity_id") or "?"
        table.add_row(
            str(edge["edge_type"]),
            direction_label,
            str(other),
            str(edge.get("platform") or ""),
        )
    console.print(table)


def cmd_traverse(
    entity_id: str,
    max_depth: int,
    as_json: bool,
    resolve: bool = False,
) -> None:
    async def operation(
        client: QueryClient,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        return await client.traverse(entity_id, max_depth, resolve)

    entity, result = _run_with_client(operation)
    if entity is None:
        console.print(f"[red]Entity {entity_id!r} not found.[/red]")
        return
    if as_json:
        console.print_json(json.dumps(result, default=str))
        return

    nodes = result.get("nodes", [])
    edges = result.get("edges", [])
    console.print(f"[bold]Traversal:[/bold] {len(nodes)} nodes, {len(edges)} edges")
    table = Table(title="Nodes", show_lines=True)
    table.add_column("ID", style="dim", max_width=8)
    table.add_column("Type")
    table.add_column("Platform")
    table.add_column("Title")
    for node in nodes:
        stub_marker = " [dim](stub)[/dim]" if is_stub(node) else ""
        table.add_row(
            str(node["id"])[:8],
            str(node["entity_type"]),
            str(node["platform"]),
            str(node.get("title") or "") + stub_marker,
        )
    console.print(table)


def _print_fetch_result(result: dict[str, Any]) -> None:
    console.print(
        f"[green]Fetched:[/green] {result['entities']} entities, "
        f"{result['persons']} persons, {result['edges']} edges"
    )


def cmd_fetch(platform: str, resource_id: str, as_json: bool) -> None:
    async def operation(client: QueryClient) -> dict[str, Any]:
        return await client.fetch(platform, resource_id)

    def error_hint(error: Exception) -> str | None:
        from agentgraph.connectors.registry import get_connector

        connector = get_connector(platform)
        return connector.fetch_error_hint(resource_id, error, "cli") if connector is not None else None

    result = _run_with_client(operation, error_hint=error_hint)
    if as_json:
        console.print_json(json.dumps(result, default=str))
        return
    _print_fetch_result(result)


def cmd_fetch_entity(entity_id: str, as_json: bool) -> None:
    async def operation(client: QueryClient) -> dict[str, Any]:
        return await client.fetch_entity(entity_id)

    result = _run_with_client(operation)
    if as_json:
        console.print_json(json.dumps(result, default=str))
        return
    _print_fetch_result(result)


def cmd_download(entity_id: str, output_path: str | None, as_json: bool) -> None:
    async def operation(client: QueryClient) -> dict[str, Any]:
        return await client.download(entity_id, output_path)

    result = _run_with_client(operation)
    if as_json:
        console.print_json(json.dumps(result, default=str))
        return
    console.print(
        f"[green]Downloaded:[/green] {result['filename']} "
        f"({result['bytes']} bytes) → {result['path']}"
    )


def cmd_bookmark(target: str, bookmarked: bool, as_json: bool) -> None:
    async def operation(client: QueryClient) -> dict[str, Any]:
        return await client.bookmark(target, bookmarked)

    result = _run_with_client(operation)
    if as_json:
        console.print_json(json.dumps(result, default=str))
        return
    label = result.get("title") or result.get("platform_entity_id") or result["id"]
    action = "Bookmarked" if bookmarked else "Bookmark removed"
    console.print(f"[green]{action}:[/green] {label} [{result['id'][:8]}]")


def cmd_delete(targets: list[str], as_json: bool) -> None:
    if len(targets) == 1:
        _cmd_delete_one(targets[0], as_json)
        return

    async def operation(client: QueryClient) -> dict[str, Any]:
        return await client.delete_many(targets)

    result = _run_with_client(operation)
    if as_json:
        console.print_json(json.dumps(result, default=str))
        return
    console.print(f"[green]Deleted {result['deleted_count']} entities.[/green]")


def _cmd_delete_one(target: str, as_json: bool) -> None:
    async def operation(client: QueryClient) -> dict[str, Any]:
        return await client.delete(target)

    result = _run_with_client(operation)
    if as_json:
        console.print_json(json.dumps(result, default=str))
        return
    entity = result["entity"]
    label = entity.get("title") or entity.get("platform_entity_id") or entity["id"]
    console.print(f"[green]Deleted:[/green] {label} [{entity['id'][:8]}]")


def cmd_unify_persons(
    primary_entity_id: str,
    duplicate_entity_ids: list[str],
    as_json: bool,
) -> None:
    async def operation(client: QueryClient) -> dict[str, Any]:
        return await client.unify_persons(primary_entity_id, duplicate_entity_ids)

    result = _run_with_client(operation)
    if as_json:
        console.print_json(json.dumps(result, default=str))
        return
    console.print(
        f"[green]Unified:[/green] {result['merged_count']} duplicate person(s). "
        "Canonical person:"
    )
    _print_entity(result["primary"])
