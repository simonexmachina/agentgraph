+++
title = "run_connector_command_tool"
description = "MCP reference for run_connector_command_tool."
nav_title = "run_connector_command_tool"
section = "MCP"
order = 14
summary = "Use `run_connector_command_tool` to run connector-owned commands through the same generic dispatch as `agentgraph connector`."
output = "mcp/run-connector-command.html"
source_path = "docs-src/mcp/run-connector-command.md"
+++

## Signature

```text
run_connector_command_tool(source, args) -> structured MCP result
```

## Arguments

- `source`: connector source, such as `rss`
- `args`: connector-owned command and arguments, such as `["add", "https://example.com/feed.xml"]`

Historical ingest is connector-owned. Gmail exposes `["ingest"]` and
`["ingest", "--account", "user@example.com"]`; there is no separate
`ingest_connector_tool`. The web connector accepts
`["fetch", "https://example.com/page", "--compact"]` for a one-off compact HTML
fetch, `["observe", "<url-or-prefix>"]` to add a browser observation rule, and
`["observe", "<url-or-prefix>", "--remove"]` to remove one. Discover other command
sets with `args=["--help"]`.

The connector-defined payload is in `data.result`; `data.help` is returned for
`args=["--help"]` or `["help"]`. Use connector help to discover source-specific
commands.
