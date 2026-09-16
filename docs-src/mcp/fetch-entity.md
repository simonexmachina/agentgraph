+++
title = "fetch_entity_tool"
description = "MCP reference for fetch_entity_tool."
nav_title = "fetch_entity_tool"
section = "MCP"
order = 20
summary = "Use `fetch_entity_tool` to force connector re-ingestion by platform name and platform-specific resource ID."
output = "mcp/fetch-entity.html"
source_path = "docs-src/mcp/fetch-entity.md"
+++

## Signature

```text
fetch_entity_tool(platform, resource_id) -> structured MCP result
```

## Use it when

- a search result or graph edge identifies a source resource that has not been hydrated;
- a linked page is missing from the graph;
- the agent has a platform-specific resource ID and needs current source content; or
- a known resource is stale and should be re-ingested before reasoning over it.

The owning connector fetches the resource and persists its complete returned batch of entities, metadata patches, people, and edges. A metadata patch records a successful non-material refresh without emitting an entity upsert event. Direct fetch does not set `observed_at`; it records retrieval by the agent, not human browser attention.

`data.entity` is the canonical graph reference after ingestion when one was stored.
The remaining `data` fields are the upsert counts.

For an existing internal graph UUID, use [`fetch_entity_by_id_tool`](/mcp/fetch-entity-by-id.html).
