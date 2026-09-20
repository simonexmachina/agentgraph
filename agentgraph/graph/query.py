"""Shared graph query layer used by both MCP tools and CLI commands."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, cast
from urllib.parse import urlparse

from agentgraph.core.context import get_backend
from agentgraph.core.storage import EdgeResult, EntityResult


@lru_cache(maxsize=256)
def _cached_query_embedding(query: str) -> tuple[float, ...]:
    from agentgraph.graph.embeddings import encode_query

    return tuple(encode_query(query))


def clear_query_embedding_cache() -> None:
    _cached_query_embedding.cache_clear()


def _enrich_web_url(entities: list[EntityResult]) -> None:
    """Populate metadata.web_url from the connector for entities that don't store it."""
    from agentgraph.connectors.registry import get_connector

    connectors: dict[str, Any] = {}
    for entity in entities:
        meta = entity.get("metadata")
        metadata = cast(dict[str, Any], meta) if isinstance(meta, dict) else None
        if metadata is not None and metadata.get("web_url"):
            continue
        platform = entity.get("platform")
        if not isinstance(platform, str):
            continue
        if platform not in connectors:
            connectors[platform] = get_connector(platform)
        connector = connectors[platform]
        if connector is None:
            continue
        platform_entity_id = entity.get("platform_entity_id")
        url = connector.entity_url(platform_entity_id if isinstance(platform_entity_id, str) else "")
        if url:
            if metadata is None:
                entity["metadata"] = {"web_url": url}
            else:
                metadata["web_url"] = url


async def search_entities(
    query: str | None = None,
    entity_types: list[str] | None = None,
    limit: int = 10,
    min_score: float = 0.03,
    platform: str | None = None,
    filters: dict[str, str] | None = None,
    since: str | None = None,
    authored_by_me: bool = False,
    has_attachments: bool = False,
    order_by: str | None = None,
    content_limit: int | None = None,
    observed_since: str | None = None,
) -> list[EntityResult]:
    """Select entities by filter, ranked by hybrid relevance when a query is given.

    With ``query`` the vector and full-text legs are fused via RRF and every filter
    is applied as a SQL predicate. Without it there is no retrieval leg: the filters
    alone select the rows, ``order_by`` sorts them, and ``min_score`` is inert.
    """
    embedding = (
        list(await asyncio.to_thread(_cached_query_embedding, query))
        if query is not None
        else None
    )
    since_dt = parse_since(since) if since else None
    observed_since_dt = parse_since(observed_since) if observed_since else None
    authored_by: list[str] | None = _resolve_me() if authored_by_me else None
    # `platform` is ergonomic shorthand for the same predicate `filters` can carry, so
    # an explicit filter wins instead of ANDing two contradictory platform clauses.
    if filters and "platform" in filters:
        platform = None
    backend = get_backend()
    results = await backend.search_entities(
        embedding,
        query,
        entity_types,
        limit,
        min_score,
        platform=platform,
        filters=filters,
        since=since_dt,
        observed_since=observed_since_dt,
        authored_by=authored_by,
        has_attachments=has_attachments,
        order_by=order_by,
        content_limit=content_limit,
    )
    _enrich_web_url(results)
    return results


async def get_entity(
    entity_id: str, content_limit: int | None = None
) -> EntityResult | None:
    """Fetch a single entity by UUID, unambiguous UUID prefix, or platform ref.

    Platform ref formats accepted:
      - ``"{platform}/{platform_entity_id}"``
      - ``"{platform}/{resource_type}/{platform_entity_id}"``  (resource_type ignored)
    """
    backend = get_backend()
    entity: EntityResult | None
    if "/" in entity_id:
        from agentgraph.connectors.base import RESOURCE_TYPE_TO_ENTITY_TYPE
        from agentgraph.connectors.registry import get_connector

        platform, pid = entity_id.split("/", 1)
        prefix, separator, remainder = pid.partition("/")
        connector = get_connector(platform)
        resource_types = set(RESOURCE_TYPE_TO_ENTITY_TYPE)
        if connector is not None:
            resource_types.update(definition.resource_type for definition in connector.entity_types)
        if separator and prefix in resource_types:
            pid = remainder
        entity = await backend.get_entity_by_platform(platform, pid, content_limit=content_limit)
    elif len(entity_id) == 36 or (len(entity_id) == 32 and "-" not in entity_id):
        entity = await backend.get_entity_by_id(entity_id, content_limit=content_limit)
    else:
        # UUID prefix — must be unambiguous
        results = await backend.get_entities_by_id_prefix(entity_id, content_limit=content_limit)
        if len(results) > 1:
            raise ValueError(
                f"Ambiguous prefix {entity_id!r} matches {len(results)} entities"
            )
        entity = results[0] if results else None
    if entity is not None:
        _enrich_web_url([entity])
    return entity


async def get_entity_by_url(
    url: str, content_limit: int | None = None
) -> EntityResult | None:
    """Fetch a single existing entity by URL without fetching or creating it."""
    from agentgraph.server.router import stored_url_candidates

    backend = get_backend()
    for ref in stored_url_candidates(url):
        if content_limit is None:
            entity = await backend.get_entity_by_platform(ref.source, ref.resource_id)
        else:
            entity = await backend.get_entity_by_platform(
                ref.source, ref.resource_id, content_limit=content_limit
            )
        if entity is not None:
            _enrich_web_url([entity])
            return entity
    return None


async def get_edges(
    entity_id: str,
    edge_type: str | None = None,
    direction: str = "both",
) -> list[EdgeResult]:
    return await get_backend().get_edges(entity_id, edge_type, direction)


async def traverse_graph(
    entity_id: str,
    max_depth: int = 2,
    content_limit: int | None = None,
) -> dict[str, Any]:
    return await get_backend().traverse_graph(
        entity_id, max_depth, content_limit=content_limit
    )


async def query_by_filter(
    entity_type: str,
    filters: dict[str, str],
    limit: int = 50,
    order_by: str = "observed_at",
    since: str | None = None,
    authored_by_me: bool = False,
    has_attachments: bool = False,
) -> list[EntityResult]:
    """Single-type filtered read: ``search_entities`` with no query string."""
    return await search_entities(
        None,
        entity_types=[entity_type],
        limit=limit,
        filters=filters,
        since=since,
        authored_by_me=authored_by_me,
        has_attachments=has_attachments,
        order_by=order_by,
    )


async def list_entities(
    entity_types: list[str] | None = None,
    platform: str | None = None,
    since: str | None = None,
    limit: int = 50,
    content_limit: int | None = None,
) -> list[EntityResult]:
    since_dt = parse_since(since) if since else None
    results = await get_backend().list_entities(
        entity_types, platform, since_dt, limit, content_limit=content_limit
    )
    _enrich_web_url(results)
    return results


async def list_entities_page(
    entity_types: list[str] | None = None,
    platform: str | None = None,
    since: str | None = None,
    limit: int = 50,
    offset: int = 0,
    order_by: str | None = "observed_at",
    order_dir: str = "desc",
    content_limit: int | None = None,
) -> tuple[list[EntityResult], int]:
    since_dt = parse_since(since) if since else None
    results, total = await get_backend().list_entities_page(
        entity_types,
        platform,
        since_dt,
        limit,
        offset,
        order_by,
        order_dir,
        content_limit=content_limit,
    )
    _enrich_web_url(results)
    return results, total


async def get_edges_for_entities(entity_ids: list[str]) -> list[EdgeResult]:
    return await get_backend().get_edges_for_entities(entity_ids)


async def get_entities_by_ids(
    entity_ids: list[str], content_limit: int | None = None
) -> list[EntityResult]:
    results = await get_backend().get_entities_by_ids(entity_ids, content_limit=content_limit)
    _enrich_web_url(results)
    return results


def _resolve_me() -> list[str] | None:
    """Return the current user's canonical identifiers by polling registered connectors."""
    from agentgraph.connectors.registry import get_all_connectors
    user_ids: list[str] = []
    for connector in get_all_connectors():
        for user_id in type(connector).current_user_ids():
            if user_id not in user_ids:
                user_ids.append(user_id)
    return user_ids or None


def is_http_url(target: str) -> bool:
    parsed = urlparse(target)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def parse_since(since: str) -> datetime:
    """Parse a relative duration (12h, 30m, 2d) or ISO timestamp string."""
    m = re.fullmatch(r"(\d+)(h|m|d)", since.strip())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"h": timedelta(hours=n), "m": timedelta(minutes=n), "d": timedelta(days=n)}[unit]
        return datetime.now(UTC) - delta
    return datetime.fromisoformat(since)
