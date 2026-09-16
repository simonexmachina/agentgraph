+++
title = "traverse_graph_tool"
description = "MCP reference for traverse_graph_tool."
nav_title = "traverse_graph_tool"
section = "MCP"
order = 18
summary = "Use `traverse_graph_tool` to build a bounded local graph around one entity when direct search results are not enough."
output = "mcp/traverse-graph.html"
source_path = "docs-src/mcp/traverse-graph.md"
+++

## Signature

```text
traverse_graph_tool(entity_id, max_depth=2, resolve=false) -> structured MCP result
```

## Notes

- `entity_id` accepts a full UUID, unambiguous UUID prefix, or platform reference
- depth is clamped between 0 and 4; depth 0 returns only the starting entity
- `resolve=true` fetches stub nodes through their owning connectors and repeats the
  traversal before returning
- node content is bounded and sets `content_truncated` when shortened; use
  `get_entity_tool` for full entity content
