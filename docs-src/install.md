+++
title = "Install"
description = "Install AgentGraph with uv, configure optional connectors, background services, and MCP transports."
nav_title = "Install"
section = "Start"
order = 20
summary = "Install the local application, connect the sources you need, and configure optional MCP clients."
output = "install.html"
source_path = "docs-src/install.md"
+++

The install flow below uses the default SQLite backend.

## Prerequisites

AgentGraph expects Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Install AgentGraph

Install AgentGraph with every first-party connector:

```bash
uv tool install 'agentgraph-server[all]'
```

This makes the `agentgraph` command available in your shell.

Or install only the connector support you need:

```bash
uv tool install 'agentgraph-server'
uv tool install 'agentgraph-server[google]'
uv tool install 'agentgraph-server[slack]'
uv tool install 'agentgraph-server[discord]'
uv tool install 'agentgraph-server[rss]'
uv tool install 'agentgraph-server[web]'
```

## Start the server

Start the server in a terminal session. It keeps that terminal occupied; use another
terminal for onboarding and other commands. See below to run it in the background.

```bash
agentgraph serve
```

## Connect sources

Run guided onboarding to set up each installed connector that provides an interactive flow:

```bash
agentgraph onboard
```

## Google auth warning

The Google auth flow for AgentGraph will show an unverified/unsafe app warning because of [this bug in GCP](https://issuetracker.google.com/issues/499336447). To continue, choose _Advanced_ and  and explicitly allow access.

## Install the agentgraph skill

The following command installs the `agentgraph` skill in `~/.agents/skills` and `~/.claude/skills`:

```bash
agentgraph install-skill
```

## Install the browser extension

Install the [AgentGraph Chrome Extension](https://chromewebstore.google.com/detail/agentgraph-extension/iilkfclglabllelhjacijldknapbhidi?authuser=0&hl=en-AU) from the Chrome Web Store.

After the extension is installed, start `agentgraph serve`, then open a supported resource and keep it focused past the default three-second observation threshold.

## Connect an MCP client

AgentGraph ships an MCP server, so any local MCP client can search and traverse the
graph. MCP is the recommended connection for **ChatGPT Desktop Work Mode**, **Codex**,
**Claude Desktop**, and **Claude Code**. To print the setup for all of them:

```bash
agentgraph mcp-config
```

**Codex** and **Claude Code** register the server from your terminal:

```bash
codex mcp add agentgraph -- "$(which agentgraph)" mcp-serve
claude mcp add --transport stdio --scope user agentgraph -- "$(which agentgraph)" mcp-serve
```

**ChatGPT Desktop Work Mode** takes the same command through its MCP configuration
screen: enter the printed executable under **Command to launch** and `mcp-serve` under
**Arguments**.

**Claude Desktop** reads a config file — add the printed JSON to
`~/Library/Application Support/Claude/claude_desktop_config.json`.

Every client runs the same `agentgraph mcp-serve` process and exposes the same tools.
How those tools reach the graph follows
[`AGENTGRAPH_QUERY_TRANSPORT`](configuration.html#agentgraph-query-transport): by
default the local server when one is reachable, otherwise the database directly, so
the MCP client works whether or not `agentgraph serve` is running.

The installed skill is a CLI fallback for terminal workflows and clients without MCP.
ChatGPT Web and Claude cloud sessions need a separately hosted remote MCP server and
are not covered by this local setup.

## Run in the background

`agentgraph serve` needs to be long-running to provide ongoing polling and to support the viewer.

### macOS launchd

```bash
cat > ~/Library/LaunchAgents/com.agentgraph.serve.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.agentgraph.serve</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(which agentgraph)</string>
    <string>serve</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.agentgraph.serve.plist
```

### Linux systemd

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/agentgraph.service <<EOF
[Unit]
Description=AgentGraph local knowledge graph server
After=network.target

[Service]
ExecStart=$(which agentgraph) serve
Restart=on-failure
RestartSec=5
StandardOutput=append:/tmp/agentgraph-serve.log
StandardError=append:/tmp/agentgraph-serve.log

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now agentgraph
```
