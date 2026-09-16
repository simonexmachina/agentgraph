+++
title = "get_entity_tool"
description = "MCP reference for get_entity_tool."
nav_title = "get_entity_tool"
section = "MCP"
order = 16
summary = "Use `get_entity_tool` when the agent has an entity target and needs the full stored payload, with optional stub hydration."
output = "mcp/get-entity.html"
source_path = "docs-src/mcp/get-entity.md"
+++

## Signature

```text
get_entity_tool(entity_id, resolve=false) -> structured MCP result
```

`entity_id` accepts a full UUID, unambiguous UUID prefix, platform reference, or
indexed HTTP(S) URL. The tool reads existing graph data and does not create an entity
for an unknown URL.

When `resolve=true` and the entity has no title or content, the tool fetches the stub
through its owning connector, persists the returned batch, and returns the refreshed
entity. Direct resolution does not update `observed_at`.

The entity is in `data.entity`. Its `is_stub` field makes the hydration decision
explicit; `source_url` is present when the connector has a canonical web URL.
