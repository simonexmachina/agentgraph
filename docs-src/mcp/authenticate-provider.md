+++
title = "authenticate_provider_tool"
description = "MCP reference for authenticate_provider_tool."
nav_title = "authenticate_provider_tool"
section = "MCP"
order = 13
summary = "Use `authenticate_provider_tool` to dispatch a provider's connector-owned authentication flow."
output = "mcp/authenticate-provider.html"
source_path = "docs-src/mcp/authenticate-provider.md"
+++

## Signature

```text
authenticate_provider_tool(provider, args = null, account_id = null, add = false) -> structured MCP result
```

## Arguments

- `provider`: auth provider key, such as `google`, `slack`, or `discord`
- `args`: connector-owned options; Slack accepts `--method oauth|browser`,
  `--client-id`, `--xoxc-token`, and `--d-cookie`. A Client ID implies OAuth.
- `account_id`: optional existing identity to replace
- `add`: add another identity and make it the default

Slack OAuth waits for the local PKCE callback. For MCP, pass `--client-id`, configure
`AGENTGRAPH_SLACK_CLIENT_ID`, or reuse an account with a stored Client ID; use the CLI
for guided admin-permission and app-creation prompts. Browser credential arguments
select the explicit fallback. The result reports `authenticated: true` or an error
string.
