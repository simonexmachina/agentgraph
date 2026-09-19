+++
title = "mcp-config"
description = "CLI reference for agentgraph mcp-config."
nav_title = "mcp-config"
section = "Reference"
order = 25
summary = "`agentgraph mcp-config` prints local stdio MCP setup instructions for supported desktop and TUI clients."
output = "commands/mcp-config.html"
source_path = "docs-src/commands/mcp-config.md"
+++

## Synopsis

```bash
agentgraph mcp-config
```

## Use it for

- ChatGPT Desktop Work Mode and Codex local MCP setup
- Claude Desktop and Claude Code local MCP setup

## Example

```bash
agentgraph mcp-config
```

For ChatGPT Desktop Work Mode, add a local MCP server in the MCP configuration screen. Enter the printed `agentgraph` executable in **Command to launch** and `mcp-serve` in **Arguments**. The desktop app and Codex TUI share the same host MCP configuration.

For Claude Desktop, add the printed JSON to `~/Library/Application Support/Claude/claude_desktop_config.json`. For Claude Code, use the printed user-scoped `claude mcp add` command so the server is available across projects and in the local Code tab.

The CLI skill remains available for terminal workflows or clients without MCP. Remote ChatGPT Web and Claude cloud sessions require a separately hosted MCP server.
