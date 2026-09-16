+++
title = "fetch_entity_by_id_tool"
description = "MCP reference for fetch_entity_by_id_tool."
nav_title = "fetch_entity_by_id_tool"
section = "MCP"
order = 21
summary = "Use `fetch_entity_by_id_tool` when the agent has an internal graph UUID and wants the source re-fetched."
output = "mcp/fetch-entity-by-id.html"
source_path = "docs-src/mcp/fetch-entity-by-id.md"
+++

## Signature

```text
fetch_entity_by_id_tool(entity_id) -> structured MCP result
```

The tool looks up the owning platform and source resource ID, calls that connector, and persists the returned entities, people, and edges. Use it to hydrate a stub reached through search or traversal, or to refresh an existing entity before continuing an investigation.

Direct fetch does not update `observed_at`. Browser observation is the only path that records human attention.
