+++
title = "AgentGraph"
description = "AgentGraph is a local-first CLI and MCP server that turns the sources you choose into a graph of messages, documents, people, feeds, pages, and relationships that your agent can use for reasoning."
nav_title = "Overview"
section = "Start"
order = 10
summary = ""
output = "index.html"
source_path = "docs-src/index.md"
+++

## A perception layer for coding agents
AgentGraph is a local-first, self-hosted CLI and MCP server that turns the sources you choose into a graph of messages, documents, people, feeds, pages, and relationships that your agent can use for reasoning.

<div class="home-actions">
  <a class="primary" href="/demo.html">Try the demo</a>
  <a href="/install.html">Install</a>
  <a href="https://github.com/simonexmachina/agentgraph">GitHub</a>
</div>

## Connect your sources

AgentGraph observes the sources that you give it access to, so that it can provide your AI agents with the context they need.

<div class="perception-flow" role="img" aria-label="Selected sources flow through connectors into a local AgentGraph, which an existing coding agent can search and traverse through MCP or the CLI.">
  <div><strong>Selected sources</strong><span>Gmail, Drive, Slack, Discord, RSS, Web</span></div>
  <span class="flow-arrow" aria-hidden="true">&rarr;</span>
  <div><strong>Local graph</strong><span>Content, people, relationships</span></div>
  <span class="flow-arrow" aria-hidden="true">&rarr;</span>
  <div><strong>Your agent</strong><span>Search and traverse through MCP or CLI</span></div>
</div>

## Connectors

Connectors provide access to the services you use. AgentGraph provides a number of connectors for common services, but new connectors can also be added to allow integration with other services. You can extend AgentGraph by adding connectors for other online services and APIs.

<ul class="connector-coverage">
  <li><strong>Gmail</strong><span>Email threads and participants</span></li>
  <li><strong>Google Drive, Docs, Sheets</strong><span>Files, content, and ownership</span></li>
  <li><strong>Slack</strong><span>Messages, threads, and people</span></li>
  <li><strong>Discord</strong><span>Channels, threads, and people</span></li>
  <li><strong>RSS</strong><span>Feeds, posts, and authors</span></li>
  <li><strong>Web</strong><span>Pages and bookmarks</span></li>
</ul>

<div class="connector-promise"><strong>Bring any service into your agent's world.</strong> Use the bundled connectors today, or build one for any other source you want.</div>

[Explore connectors](/connectors.html) or [build your own](/extending.html).

## Local by design

Indexed content is stored in SQLite on your machine. Source API calls run from your machine under your credentials. The project does not operate a hosted graph service or receive indexed content through a project-controlled backend.

An MCP client you connect can read content from the local graph and is governed by that client's data practices. Read the [Privacy Policy](/privacy.html), [Terms of Service](/terms.html), and [retention model](/retention.html).

## Explore the docs

- [Install](/install.html) to connect sources and an optional MCP client.
- [How it works](/how-it-works.html) for observation, fetch, refresh, and the graph model.
- [CLI reference](/commands/) and [MCP tools](/mcp/) for the complete interfaces.
