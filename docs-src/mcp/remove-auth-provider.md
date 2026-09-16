+++
title = "remove_auth_provider_tool"
description = "MCP reference for remove_auth_provider_tool."
nav_title = "remove_auth_provider_tool"
section = "MCP"
order = 13
summary = "Use `remove_auth_provider_tool` to remove stored credentials for an authentication provider."
output = "mcp/remove-auth-provider.html"
source_path = "docs-src/mcp/remove-auth-provider.md"
+++

## Signature

```text
remove_auth_provider_tool(provider, account_id = null) -> structured MCP result
```

## Parameters

- `provider`: auth provider key, such as `google`, `slack`, or `discord`
- `account_id`: optional account ID; omit it to remove all credentials for the provider

## Returns

- `provider`
- `removed`: true when credentials were found and removed
- `account_id` when supplied

Removing credentials stops authenticated connector operations such as background
polling, but does not delete already indexed graph data.
