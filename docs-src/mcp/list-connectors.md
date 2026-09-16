+++
title = "list_connectors_tool"
description = "MCP reference for list_connectors_tool."
nav_title = "list_connectors_tool"
section = "MCP"
order = 11
summary = "Use `list_connectors_tool` when an agent needs connector availability, credential state, source freshness, or valid platform values."
output = "mcp/list-connectors.html"
source_path = "docs-src/mcp/list-connectors.md"
+++

## Signature

```text
list_connectors_tool(verify=false) -> structured MCP result
```

Use this tool when source availability, freshness, authentication, or valid platform
values matter. Normal graph reads do not need to call it first. Set `verify=true` only
for a live provider credential check.

## Returns

- connector `source`
- `description`
- connector-declared `entity_types`, each with its shared `name`, connector-local
  `resource_type`, and `description`
- authentication provider, shared-auth, and account-count metadata
- `auth_status`, `auth_detail`, and `auth_verified`, or null for connectors that do not use credentials
- `url_patterns`
- polling metadata, delegation, and sync timestamps/summary

The full installed entity type catalog is also embedded in the
`search_entities_tool` description returned during MCP tool discovery, so an agent
does not need to call this tool before searching.
