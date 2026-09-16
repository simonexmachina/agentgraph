"""Idempotent upsert layer for entities and edges."""

from __future__ import annotations

import asyncio
from typing import Any

from agentgraph.connectors.base import EntityBatch
from agentgraph.core.context import get_backend


async def upsert_batch(batch: EntityBatch) -> None:
    """Persist an EntityBatch to the graph, generating embeddings as needed."""
    person_embeddings, entity_embeddings = await asyncio.to_thread(_build_embeddings, batch)

    upserted_entities = await get_backend().upsert_batch(
        batch, person_embeddings, entity_embeddings
    )
    await _link_references(batch, upserted_entities)
    await notify_entity_upserts(upserted_entities)


async def notify_entity_upserts(entities: list[dict[str, Any]]) -> None:
    """Publish committed entity snapshots with their complete incident edges."""

    if not entities:
        return

    from agentgraph.connectors.feed import (
        EntityUpsertMutation,
        edge_snapshot_from_edge,
        entity_snapshot_from_entity,
        mutation_target_from_entity,
        notify_feed_connectors,
    )

    entity_ids = [str(entity["id"]) for entity in entities]
    edges = await get_backend().get_edges_for_entities(entity_ids)
    edges_by_entity_id: dict[str, list[dict[str, Any]]] = {
        entity_id: [] for entity_id in entity_ids
    }
    for edge in edges:
        source_id = str(edge["source_entity_id"])
        target_id = str(edge["target_entity_id"])
        if source_id in edges_by_entity_id:
            edges_by_entity_id[source_id].append(edge)
        if target_id != source_id and target_id in edges_by_entity_id:
            edges_by_entity_id[target_id].append(edge)

    for entity, entity_id in zip(entities, entity_ids, strict=True):
        await notify_feed_connectors(
            EntityUpsertMutation(
                target=mutation_target_from_entity(entity),
                entity=entity_snapshot_from_entity(entity),
                edges=[edge_snapshot_from_edge(edge) for edge in edges_by_entity_id[entity_id]],
            )
        )


def _build_embeddings(
    batch: EntityBatch,
) -> tuple[dict[str, list[float] | None], dict[str, list[float] | None]]:
    from agentgraph.graph.embeddings import encode_passage

    person_embeddings: dict[str, list[float] | None] = {}
    for p in batch.persons:
        canonical_key = p.canonical_email or f"{p.platform}:{p.platform_user_id}"
        text = " ".join(filter(None, [p.display_name, p.canonical_email]))
        vec: list[float] | None = encode_passage(text) if text else None
        person_embeddings[canonical_key] = vec
        person_embeddings[p.platform_user_id] = vec  # also indexed by user_id for edge resolution

    entity_embeddings: dict[str, list[float] | None] = {}
    for e in batch.entities:
        if not e.is_stub and e.content and e.content_searchable:
            text = f"{e.title or ''} {e.content}".strip()
            entity_embeddings[e.platform_entity_id] = encode_passage(text)
        else:
            entity_embeddings[e.platform_entity_id] = None

    return person_embeddings, entity_embeddings


async def _link_references(
    batch: EntityBatch,
    upserted_entities: list[dict[str, Any]],
) -> None:
    """Create cross-platform reference edges for inserted or changed content."""

    from agentgraph.graph.link import link_entity_to_urls

    upserted_refs = {
        (str(entity["platform"]), str(entity["platform_entity_id"]))
        for entity in upserted_entities
    }
    linked_refs: set[tuple[str, str]] = set()
    for entity in batch.entities:
        ref = (entity.platform, entity.platform_entity_id)
        if (
            entity.content
            and entity.content_searchable
            and ref in upserted_refs
            and ref not in linked_refs
        ):
            linked_refs.add(ref)
            await link_entity_to_urls(entity.platform_entity_id, entity.platform, entity.content)
