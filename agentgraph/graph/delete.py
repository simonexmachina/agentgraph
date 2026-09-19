"""Delete graph entities."""

from __future__ import annotations

from typing import Any

from agentgraph.core.context import get_backend
from agentgraph.graph.query import get_entity


async def delete_entity(target: str) -> dict[str, Any]:
    """Delete an entity by UUID, UUID prefix, platform ref, or URL."""
    entity = await get_entity(target)
    if entity is None:
        raise ValueError(f"Entity {target!r} not found")
    deleted = await get_backend().delete_entity(entity["id"])
    await _notify_deleted(deleted)
    return {"deleted": True, "entity": deleted}


async def delete_entities(targets: list[str]) -> dict[str, Any]:
    """Atomically delete a fully resolved set of graph entities."""
    if not targets:
        raise ValueError("At least one entity target is required")

    resolved_by_id: dict[str, dict[str, Any]] = {}
    for target in targets:
        entity = await get_entity(target)
        if entity is None:
            raise ValueError(f"Entity {target!r} not found")
        resolved_by_id.setdefault(str(entity["id"]), entity)

    deleted = await get_backend().delete_entities(list(resolved_by_id))
    await _notify_deleted_entities(deleted)
    return {
        "deleted_count": len(deleted),
        "entities": [_entity_reference(entity) for entity in deleted],
    }


async def delete_platform_entity(platform: str, platform_entity_id: str) -> dict[str, Any] | None:
    """Delete an external entity if it is present in the graph."""
    entity = await get_backend().get_entity_by_platform(platform, platform_entity_id)
    if entity is None:
        return None
    deleted = await get_backend().delete_entity(entity["id"])
    await _notify_deleted(deleted)
    return {"deleted": True, "entity": deleted}


async def delete_platform_entities(
    targets: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Atomically delete present platform entities, ignoring already absent targets."""
    unique_targets = list(dict.fromkeys(targets))
    backend = get_backend()
    entities: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for platform, platform_entity_id in unique_targets:
        entity = await backend.get_entity_by_platform(platform, platform_entity_id)
        if entity is not None and str(entity["id"]) not in seen_ids:
            entities.append(entity)
            seen_ids.add(str(entity["id"]))
    if not entities:
        return []

    deleted = await backend.delete_entities([str(entity["id"]) for entity in entities])
    await _notify_deleted_entities(deleted)
    return deleted


async def _notify_deleted(entity: dict[str, Any]) -> None:
    from agentgraph.connectors.feed import (
        TombstoneMutation,
        mutation_target_from_entity,
        notify_feed_connectors,
    )

    await notify_feed_connectors(
        TombstoneMutation(target=mutation_target_from_entity(entity))
    )


async def _notify_deleted_entities(entities: list[dict[str, Any]]) -> None:
    from agentgraph.connectors.feed import (
        TombstoneBatchMutation,
        mutation_target_from_entity,
        notify_feed_connectors,
    )

    await notify_feed_connectors(
        TombstoneBatchMutation(targets=[mutation_target_from_entity(entity) for entity in entities])
    )


def _entity_reference(entity: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(entity["id"]),
        "entity_type": str(entity["entity_type"]),
        "platform": str(entity["platform"]),
        "platform_entity_id": str(entity["platform_entity_id"]),
    }
