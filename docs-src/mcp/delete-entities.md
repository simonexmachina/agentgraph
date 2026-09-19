+++
title = "delete_entities_tool"
description = "MCP reference for delete_entities_tool."
nav_title = "delete_entities_tool"
section = "MCP"
order = 27
summary = "Use `delete_entities_tool` to atomically remove several graph entities after the user has identified the full set."
output = "mcp/delete-entities.html"
source_path = "docs-src/mcp/delete-entities.md"
+++

## Signature

```text
delete_entities_tool(entity_ids) -> structured MCP result
```

## Returns

- `deleted_count`
- compact references for the deleted entities
- an error without changing the graph when any target cannot be resolved

Every target must resolve before deletion begins. Duplicate references to the same
entity are deleted once, and connected edges are removed with each entity.
