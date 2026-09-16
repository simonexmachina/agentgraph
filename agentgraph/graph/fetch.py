"""Connector fetch trigger — shared by CLI, MCP, and server API."""

from __future__ import annotations

from typing import Any, cast

from agentgraph.core.context import get_backend


async def fetch_entity(platform: str, resource_id: str) -> dict[str, Any]:
    """Trigger a connector fetch for a known platform entity.

    Resolves the entity type from the backend (falling back to Document), resets
    synced_at so the connector treats the entity as stale, and runs a targeted
    fetch. Returns the canonical entity when it exists, plus ingestion counts.
    """
    from agentgraph.connectors.registry import get_connector
    from agentgraph.graph.upsert import upsert_batch

    connector = get_connector(platform)
    if connector is None:
        raise ValueError(f"No connector registered for platform '{platform}'")

    backend = get_backend()
    entity = await backend.get_entity_by_platform(platform, resource_id)
    raw_entity_type = (entity or {}).get("entity_type") or "Document"
    entity_type = str(raw_entity_type)
    entity_meta = (entity or {}).get("metadata")
    meta = cast(dict[str, Any], entity_meta) if isinstance(entity_meta, dict) else None

    resource_id, resource_type = connector.normalise_fetch_id(resource_id, entity_type)

    await backend.reset_synced_at(platform, resource_id)

    batch = await connector.fetch(resource_type=resource_type, resource_id=resource_id, meta=meta)
    await upsert_batch(batch)
    fetched_entity = await backend.get_entity_by_platform(platform, resource_id)
    return {
        "entity": fetched_entity,
        "entities": len(batch.entities),
        "metadata_patches": len(batch.metadata_patches),
        "persons": len(batch.persons),
        "edges": len(batch.edges),
    }


async def fetch_entity_by_id(entity_id: str) -> dict[str, Any]:
    """Trigger a connector fetch for an entity identified by its internal UUID.

    Looks up platform and platform_entity_id from the backend, then delegates to
    fetch_entity.  Raises ValueError if the entity is not found.
    """
    ref = await get_backend().get_entity_platform_ref(entity_id)
    if ref is None:
        raise ValueError(f"Entity not found: {entity_id}")
    return await fetch_entity(platform=ref[0], resource_id=ref[1])


async def fetch_url(url: str, meta: dict[str, str] | None = None) -> dict[str, Any]:
    """Fetch an HTTP(S) URL through observation resolution, with web fallback."""
    from agentgraph.connectors.registry import bootstrap, get_connector
    from agentgraph.graph.upsert import upsert_batch
    from agentgraph.server.router import classify_observation_url

    bootstrap()
    ref = await classify_observation_url(url, meta=meta)
    connector = get_connector(ref.source) if ref is not None else get_connector("web")
    if connector is None:
        raise ValueError("No connector available to fetch this URL")

    fetch_meta = dict(meta or {})
    if ref is not None and ref.fetch_meta:
        fetch_meta.update(ref.fetch_meta)
    resource_type = ref.resource_type if ref is not None else "document"
    resource_id = ref.resource_id if ref is not None else url
    batch = await connector.fetch(
        resource_type=resource_type,
        resource_id=resource_id,
        meta=fetch_meta or None,
    )
    if batch.has_writes():
        await upsert_batch(batch)

    backend = get_backend()
    entity = None
    if ref is not None:
        entity = await backend.get_entity_by_platform(ref.source, ref.resource_id)
    if entity is None:
        for candidate in batch.entities:
            entity = await backend.get_entity_by_platform(
                candidate.platform, candidate.platform_entity_id
            )
            if entity is not None:
                break

    return {
        "source": ref.source if ref is not None else "web",
        "resource_type": resource_type,
        "resource_id": resource_id,
        "entity": entity,
        "entities": len(batch.entities),
        "metadata_patches": len(batch.metadata_patches),
        "persons": len(batch.persons),
        "edges": len(batch.edges),
    }
