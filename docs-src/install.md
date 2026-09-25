+++
title = "Install"
description = "Install AgentGraph with uv, configure optional connectors, background services, and MCP transports."
nav_title = "Install"
section = "Start"
order = 20
summary = "Install the local application, connect sources and configure MCP."
output = "install.html"
source_path = "docs-src/install.md"
+++

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

<aside class="heads-up">
  <p class="heads-up-label">Heads up</p>
  <p>If you see a warning "Google hasn’t verified this app" it's caused by <a href="https://issuetracker.google.com/issues/499336447">this bug in GCP</a>, and you will need to choose <em>Advanced</em> and explicitly allow access.</p>
</aside>

## Install the browser extension

Install the [AgentGraph Chrome Extension](https://chromewebstore.google.com/detail/agentgraph-extension/iilkfclglabllelhjacijldknapbhidi?authuser=0&hl=en-AU) from the Chrome Web Store.

After the extension is installed, start `agentgraph serve`, then open a supported resource and keep it focused past the default three-second observation threshold.

## Install the agentgraph skill

The following command installs the `agentgraph` skill in `~/.agents/skills` and `~/.claude/skills`:

```bash
agentgraph install-skill
```

## Connect an MCP client

MCP is the recommended connection for **ChatGPT Desktop Work Mode**, **Codex**,
**Claude Desktop**, and **Claude Code**.

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

How those tools reach the graph follows
[`AGENTGRAPH_QUERY_TRANSPORT`](configuration.html#agentgraph-query-transport): by
default the local server when one is reachable, otherwise the database directly.

## Run in the background

`agentgraph serve` should be a long-running process to host the viewer and provide continuous polling of the connected sources.

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
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now agentgraph
```
