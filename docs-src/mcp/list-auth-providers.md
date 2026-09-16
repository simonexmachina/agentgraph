+++
title = "list_auth_providers_tool"
description = "MCP reference for list_auth_providers_tool."
nav_title = "list_auth_providers_tool"
section = "MCP"
order = 12
summary = "Use `list_auth_providers_tool` to inspect credential-backed provider authentication state, including shared providers such as Google."
output = "mcp/list-auth-providers.html"
source_path = "docs-src/mcp/list-auth-providers.md"
+++

## Signature

```text
list_auth_providers_tool(verify=false) -> structured MCP result
```

Set `verify=true` only when credential validity is uncertain and a live provider API
check is needed.

## Returns

- provider key and description
- connector sources that use the provider
- shared-provider flag
- `auth_status`, `auth_detail`, and `auth_verified`
- authenticated account rows, including identity labels, `auth_method`, and auth state

Connectors that only need configuration, such as RSS, and connectors that need
no setup, such as generic web, are omitted. Use `list_connectors_tool` to
inspect all installed connectors.
