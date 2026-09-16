+++
title = "poll_connectors_tool"
description = "MCP reference for poll_connectors_tool."
nav_title = "poll_connectors_tool"
section = "MCP"
order = 23
summary = "Use `poll_connectors_tool` to trigger background polling for one connector or all polling connectors."
output = "mcp/poll-connectors.html"
source_path = "docs-src/mcp/poll-connectors.md"
+++

## Signature

```text
poll_connectors_tool(source = null) -> structured MCP result
```

## Arguments

- `source`: optional connector source. Omit it to poll every connector with a configured `poll_interval`.

## Returns

- `polled`: connector sources whose background poll was started
- `already_running`: connector sources whose poll was already active
- `skipped`: connector sources not queued, with a reason such as missing auth
- an error message if a requested connector source is not registered

A connector with no direct poll can still be refreshed by another connector. Inspect
`polled_by` and `sync` from `list_connectors_tool` before treating `polled: []` as
evidence that its graph data is stale.
