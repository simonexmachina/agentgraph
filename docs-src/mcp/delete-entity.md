+++
title = "delete_entity_tool"
description = "MCP reference for delete_entity_tool."
nav_title = "delete_entity_tool"
section = "MCP"
order = 26
summary = "Use `delete_entity_tool` to remove a graph entity once the user has identified it as stale or incorrect."
output = "mcp/delete-entity.html"
source_path = "docs-src/mcp/delete-entity.md"
+++

## Signature

```text
delete_entity_tool(entity_id) -> structured MCP result
```

## Returns

- `deleted: true`
- the deleted entity
- an error message if the entity cannot be found

Connected edges are removed with the entity.
