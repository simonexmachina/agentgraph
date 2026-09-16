+++
title = "unify_persons_tool"
description = "MCP reference for unify_persons_tool."
nav_title = "unify_persons_tool"
section = "MCP"
order = 27
summary = "Use `unify_persons_tool` to merge duplicate Person entities after confirming they refer to the same human."
output = "mcp/unify-persons.html"
source_path = "docs-src/mcp/unify-persons.md"
+++

## Signature

```text
unify_persons_tool(primary_entity_id, duplicate_entity_ids) -> structured MCP result
```

## Arguments

- `primary_entity_id`: Person entity ID, UUID prefix, or platform ref to keep
- `duplicate_entity_ids`: duplicate Person entity IDs, UUID prefixes, or platform refs to merge into the primary

## Returns

- updated canonical Person, including identity metadata and the merged Person identities
- duplicate IDs that were merged
- an error message if an entity is missing or is not a Person
