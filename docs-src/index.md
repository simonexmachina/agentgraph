+++
title = "AgentGraph"
description = "AgentGraph gives coding agents a local perception layer for the email, documents, chats, feeds, and pages you choose. Explore your context through MCP or the CLI."
nav_title = "Overview"
section = "Start"
order = 10
summary = ""
output = "index.html"
source_path = "docs-src/index.md"
+++

<div class="home-intro">
  <p class="home-tagline">The perception layer for coding agents.</p>
  <p class="positioning">Turn the email, documents, chats, feeds, and pages you choose into a local graph your existing agent can search and navigate through MCP or the CLI.</p>
  <p class="home-boundary">AgentGraph supplies context to your agent. It does not run an agent of its own.</p>
</div>

<div class="home-actions">
  <a class="primary" href="/demo.html">Try the demo</a>
  <a href="/install.html">Install</a>
  <a href="https://github.com/simonexmachina/agentgraph">GitHub</a>
</div>

## Trace a decision across sources

<p class="home-demo-intro">In the fictional Atlas demo, an agent investigates a decision spread across an email thread, a Slack discussion, a Drive plan, and research documents.</p>

<div class="demo-story">
  <p class="demo-question"><span>Ask your agent</span>Before I reply to Maya, what did she require, what did engineering agree, and does the Drive plan match?</p>
  <div class="demo-evidence">
    <div><strong>Gmail</strong><p>Maya requires five-minute synchronization by September 30.</p></div>
    <div><strong>Slack</strong><p>Engineering agrees on webhook delivery with idempotent consumers.</p></div>
    <div><strong>Drive</strong><p>The plan still proposes hourly batches and an October 15 date.</p></div>
  </div>
  <p class="demo-result"><strong>The useful answer:</strong> the agent can flag the conflicting plan, check the supporting research, and link each claim to its source.</p>
</div>

[Run the complete fictional demo](/demo.html)

## Your agent knows the repo. What about the rest?

Coding agents work well because the source of truth is on disk. They can search files, follow references, inspect history, and build a model of a system. Your other context is scattered across conversations, documents, feeds, and pages. AgentGraph makes the sources you select similarly searchable and navigable.

<div class="perception-flow" role="img" aria-label="Selected sources flow through connectors into a local AgentGraph, which an existing coding agent can search and traverse through MCP or the CLI.">
  <div><strong>Selected sources</strong><span>Gmail, Drive, Slack, Discord, RSS, Web</span></div>
  <span class="flow-arrow" aria-hidden="true">&rarr;</span>
  <div><strong>Local graph</strong><span>Content, people, relationships</span></div>
  <span class="flow-arrow" aria-hidden="true">&rarr;</span>
  <div><strong>Your coding agent</strong><span>Search and traverse through MCP or CLI</span></div>
</div>

## How context gets there

- **Observe:** a supported page you keep focused signals what mattered; its connector fetches the resource.
- **Fetch:** your agent or the CLI requests a specific missing or stale resource.
- **Refresh:** connector polling keeps known resources current.

The local graph also applies an [expiry and retention model](/retention.html). See [How AgentGraph works](/how-it-works.html) for the full data flow.

## Connect the world you use

<ul class="connector-coverage">
  <li><strong>Gmail</strong><span>Email threads and participants</span></li>
  <li><strong>Google Drive, Docs, Sheets</strong><span>Files, content, and ownership</span></li>
  <li><strong>Slack</strong><span>Messages, threads, and people</span></li>
  <li><strong>Discord</strong><span>Channels, threads, and people</span></li>
  <li><strong>RSS</strong><span>Feeds, posts, and authors</span></li>
  <li><strong>Web</strong><span>Pages and bookmarks</span></li>
</ul>

<div class="connector-promise"><strong>Bring any service into your agent's world.</strong> Use the bundled connectors today, or build one for an internal system, a niche tool, an export, or another source you care about.</div>

[Explore connectors](/connectors.html) or [build your own](/extending.html).

## Local by design

Indexed content is stored in SQLite on your machine. Source API calls run from your machine under your credentials. The project does not operate a hosted graph service or receive indexed content through a project-controlled backend.

An MCP client you connect can read content from the local graph and is governed by that client's data practices. Read the [Privacy Policy](/privacy.html), [Terms of Service](/terms.html), and [retention model](/retention.html).

## Explore the docs

- [Install](/install.html) to connect sources and an optional MCP client.
- [How it works](/how-it-works.html) for observation, fetch, refresh, and the graph model.
- [CLI reference](/commands/) and [MCP tools](/mcp/) for the complete interfaces.
