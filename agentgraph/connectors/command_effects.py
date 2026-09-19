"""Generic execution of connector-owned command effects."""

from __future__ import annotations

from typing import Any, Literal

from agentgraph.connectors.base import ConnectorCommandEffects
from agentgraph.graph.delete import delete_platform_entity


def fetch_effect_error_hint(
    effects: ConnectorCommandEffects,
    error: Exception,
    audience: Literal["cli", "mcp"],
) -> str | None:
    """Return recovery guidance for a failed single-reference command fetch."""
    if len(effects.fetch_references) != 1:
        return None
    from agentgraph.connectors.registry import get_connector

    reference = effects.fetch_references[0]
    connector = get_connector(reference.source)
    if connector is None:
        return None
    hint_factory = getattr(connector, "fetch_error_hint", None)
    if not callable(hint_factory):
        return None
    hint = hint_factory(reference.resource_id, error, audience)
    return hint if isinstance(hint, str) else None


async def execute_deletions(effects: ConnectorCommandEffects) -> list[dict[str, Any]]:
    """Delete connector-requested entities, ignoring ones already absent."""
    deleted: list[dict[str, Any]] = []
    for reference in effects.delete_entities:
        result = await delete_platform_entity(reference.platform, reference.platform_entity_id)
        if result is not None:
            deleted.append(result["entity"])
    return deleted


async def execute_cursor_resets(effects: ConnectorCommandEffects) -> list[str]:
    """Clear connector-requested cursors and return the sources that were reset."""
    from agentgraph.core.context import get_backend

    backend = get_backend()
    for source in effects.reset_cursors:
        await backend.clear_cursor(source)
    return list(effects.reset_cursors)


async def execute_fetches(effects: ConnectorCommandEffects) -> list[dict[str, Any]]:
    """Fetch connector-owned references and persist their returned batches."""
    from agentgraph.connectors.registry import get_connector
    from agentgraph.graph.upsert import upsert_batch

    fetched: list[dict[str, Any]] = []
    for reference in effects.fetch_references:
        connector = get_connector(reference.source)
        if connector is None:
            raise ValueError(f"No connector registered for source {reference.source!r}")
        batch = await connector.fetch(
            resource_type=reference.resource_type,
            resource_id=reference.resource_id,
            meta=reference.fetch_meta,
        )
        if batch.has_writes():
            await upsert_batch(batch)
        fetched.append(
            {
                "source": reference.source,
                "resource_type": reference.resource_type,
                "resource_id": reference.resource_id,
                "entities": len(batch.entities),
                "metadata_patches": len(batch.metadata_patches),
                "persons": len(batch.persons),
                "edges": len(batch.edges),
            }
        )
    return fetched
