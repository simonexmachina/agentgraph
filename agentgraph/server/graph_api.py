"""Resource-oriented HTTP surface over the graph.

Named for the resources it exposes rather than any caller: the graph viewer, the CLI's
``server`` query transport, and anything else all use these same routes. Two callers
need different entity shapes, so presentation is opt-in via ``?include=display``;
without it a response is exactly what the equivalent in-process call returns, which is
what keeps ``agentgraph <cmd> --json`` byte-identical across transports
(``tests/test_query_parity.py``).

Missing entities return 404. The CLI maps that back to the ``None`` its in-process
counterparts return, so both transports behave the same from the caller's side.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from fastapi.routing import APIRoute

from agentgraph.server.presentation import with_display_name, with_display_names


class GraphAPIRoute(APIRoute):
    """Map graph-layer exceptions onto 400, preserving the exception class.

    Connectors signal their most common real failure with ``RuntimeError`` ("Slack
    credentials not configured. Run: agentgraph auth slack"), so catching only
    ``ValueError`` turned that into a 500 whose body lost the actionable message.
    The class name travels in the body because callers branch on it: the web
    connector's ``fetch_error_hint`` checks ``isinstance(error, ValueError)``.

    ``HTTPException`` is neither a ``ValueError`` nor a ``RuntimeError``, so a route
    raising 404 passes through untouched. FastAPI's ``RequestValidationError`` is also
    outside this hierarchy, so request validation still returns its usual 422.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except (ValueError, RuntimeError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"message": str(exc), "error_type": type(exc).__name__},
                ) from exc

        return handler


router = APIRouter(prefix="/api", tags=["graph"], route_class=GraphAPIRoute)

_NOT_FOUND = "Entity not found"


def _wants_display(include: str | None) -> bool:
    """Whether the caller asked for presentation fields. Comma-separated for growth."""
    if not include:
        return False
    return "display" in {part.strip() for part in include.split(",")}


# --- Literal paths and distinct prefixes -------------------------------------------
# Declared before the `{ref:path}` routes below: a path converter matches greedily, so
# a bare `{ref:path}` declared earlier would swallow "search" and the `/edges`-style
# suffixes. tests/test_route_resolution.py pins this ordering.


@router.get("/capabilities")
async def capabilities() -> dict[str, Any]:
    """Probe for clients deciding whether this server can serve them.

    A plain TCP or socket connect is not enough: a server predating these routes
    accepts the connection and then 404s every call, so the CLI's `auto` transport
    would hard-fail where it should fall back to in-process.
    """
    return {"status": "ok", "routes": "resource3"}


@router.post("/entities/search")
async def search_entities(
    filters: dict[str, str] = Body(default_factory=dict),
    query: str | None = Query(default=None),
    entity_types: list[str] | None = Query(default=None),
    limit: int = Query(default=10),
    min_score: float = Query(default=0.03),
    platform: str | None = Query(default=None),
    since: str | None = Query(default=None),
    authored_by_me: bool = Query(default=False),
    has_attachments: bool = Query(default=False),
    order_by: str | None = Query(default=None),
    include: str | None = Query(default=None),
    observed_since: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    """POST, not GET, because ``filters`` is an open-ended field/value mapping.

    Omitting ``query`` drops the retrieval legs, leaving the filters to select the
    rows — what the removed ``/entities/filter`` route used to do.
    """
    from agentgraph.graph.operations import summarize_entities
    from agentgraph.graph.query import search_entities as impl

    results = await impl(
        query,
        entity_types=entity_types or None,
        limit=limit,
        min_score=min_score,
        platform=platform,
        filters=filters or None,
        since=since,
        observed_since=observed_since,
        authored_by_me=authored_by_me,
        has_attachments=has_attachments,
        order_by=order_by,
    )
    summarized = summarize_entities(results)
    return with_display_names(summarized) if _wants_display(include) else summarized


@router.post("/entities/delete")
async def delete_entities(
    targets: list[str] = Body(..., embed=True, min_length=1),
) -> dict[str, Any]:
    from agentgraph.graph.delete import delete_entities as impl

    return await impl(targets)


@router.post("/fetches")
async def create_fetch(platform: str, resource_id: str) -> dict[str, Any]:
    """Fetch a platform resource that may not be in the graph yet."""
    from agentgraph.graph.fetch import fetch_entity

    return await fetch_entity(platform, resource_id)


@router.post("/persons/unify")
async def unify_persons(
    canonical_id: str,
    duplicate_ids: list[str] = Query(default_factory=list),
) -> dict[str, Any]:
    from agentgraph.graph.person import unify_persons as impl

    return await impl(canonical_id, duplicate_ids)


@router.get("/graph/traverse/{ref:path}")
async def traverse(
    ref: str,
    max_depth: int = Query(default=2),
    resolve: bool = Query(default=False),
) -> dict[str, Any]:
    from agentgraph.graph.operations import traverse_entity

    entity, result = await traverse_entity(ref, max_depth=max_depth, resolve=resolve)
    if entity is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return {"entity": entity, "result": result}


# --- Entity subresources: suffix routes before the bare `{ref:path}` ---------------


@router.get("/entities/{ref:path}/edges")
async def entity_edges(
    ref: str,
    edge_type: str | None = Query(default=None),
    direction: str = Query(default="both"),
    include: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return the resolved entity alongside its edges; callers need both."""
    from agentgraph.graph.operations import get_entity_edges

    entity, edges = await get_entity_edges(ref, edge_type=edge_type, direction=direction)
    if entity is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    if _wants_display(include):
        entity = with_display_name(entity)
    return {"entity": entity, "edges": edges}


@router.post("/entities/{ref:path}/bookmark")
async def bookmark_entity(
    ref: str,
    bookmarked: bool = Query(default=True),
) -> dict[str, Any]:
    from agentgraph.graph.bookmark import bookmark_target, set_entity_bookmark

    if bookmarked:
        return await bookmark_target(ref)
    return await set_entity_bookmark(ref, False)


@router.post("/entities/{ref:path}/fetch")
async def fetch_entity_by_ref(ref: str) -> dict[str, Any]:
    """Re-fetch an entity already in the graph, by its internal UUID."""
    from agentgraph.graph.fetch import fetch_entity_by_id

    return await fetch_entity_by_id(ref)


@router.post("/entities/{ref:path}/download")
async def download_entity(
    ref: str,
    output_path: str | None = Query(default=None),
) -> dict[str, Any]:
    """Download an entity's source file.

    The server writes the file, so callers must send an absolute ``output_path``; a
    relative one would resolve against the server's working directory.
    """
    from agentgraph.graph.download import download_entity as impl

    return await impl(ref, output_path)


@router.get("/entities/{ref:path}")
async def get_entity(
    ref: str,
    resolve: bool = Query(default=False),
    include: str | None = Query(default=None),
    content_limit: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    from agentgraph.graph.operations import get_entity_details

    entity = await get_entity_details(ref, resolve=resolve, content_limit=content_limit)
    if entity is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return with_display_name(entity) if _wants_display(include) else entity


@router.delete("/entities/{ref:path}")
async def delete_entity(ref: str) -> dict[str, Any]:
    from agentgraph.graph.delete import delete_entity as impl

    return await impl(ref)
