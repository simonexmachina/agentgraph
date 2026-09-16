+++
title = "bookmark_entity_tool"
description = "MCP reference for bookmark_entity_tool."
nav_title = "bookmark_entity_tool"
section = "MCP"
order = 25
summary = "Use `bookmark_entity_tool` to add or remove bookmark protection for an entity or URL."
output = "mcp/bookmark-entity.html"
source_path = "docs-src/mcp/bookmark-entity.md"
+++

## Signature

```text
bookmark_entity_tool(entity_id, bookmarked = true) -> structured MCP result
```

## Arguments

- `entity_id`: entity UUID, UUID prefix, platform/entity reference, or HTTP(S) URL
- `bookmarked`: `true` to protect the entity, `false` to remove protection

## Returns

- updated entity JSON with its bookmark state
- an error message if the entity cannot be found
