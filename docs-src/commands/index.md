+++
title = "Commands"
description = "Command reference for the AgentGraph CLI, including MCP client setup."
nav_title = "Commands"
section = "Reference"
order = 10
summary = "The CLI is the operator-facing surface for the local graph. Each command now has its own page so flags, examples, and adjacent workflows stay readable."
output = "commands/index.html"
source_path = "docs-src/commands/index.md"
aliases = ["commands.html"]
+++

## Query

- [`search`](/commands/search.html) - hybrid semantic and lexical search, plus structured filtering by entity type, metadata, time, and attachments.
- [`get`](/commands/get.html) - fetch one entity by UUID, prefix, or platform ref.
- [`edges`](/commands/edges.html) - list connected graph edges for one entity.
- [`traverse`](/commands/traverse.html) - walk the neighborhood around one entity.

## Fetch and files

- [`fetch`](/commands/fetch.html) - trigger connector fetch by platform and platform ID.
- [`fetch-entity`](/commands/fetch-entity.html) - re-fetch by internal entity UUID.
- [`download`](/commands/download.html) - download the source file for a graph entity.
- [`bookmark`](/commands/bookmark.html) - protect an entity or URL from expiration.
- [`delete`](/commands/delete.html) - remove an entity and its connected edges.

## Sync and connectors

- [`connector`](/commands/connector.html) - discover and run commands owned by an installed connector.
- [`list-connectors`](/commands/list-connectors.html) - inspect installed connectors and auth state.
- [`poll`](/commands/poll.html) - run background polling now.

## Auth and setup

- [`onboard`](/commands/onboard.html) - interactive connector setup.
- [`auth`](/commands/auth.html) - manage connector authentication.
- [`install-skill`](/commands/install-skill.html) - install the bundled agentgraph skill for an agent.

## Server and transport

- [`serve`](/commands/serve.html) - run the local AgentGraph HTTP server and pollers.
- [`mcp-config`](/commands/mcp-config.html) - print MCP client configuration.
- [`mcp-serve`](/commands/mcp-serve.html) - run the MCP server over stdio, SSE, or streaming HTTP.

## Maintenance and demos

- [`unify-persons`](/commands/unify-persons.html) - merge confirmed duplicate Person entities.
- [`demo`](/commands/demo.html) - seed the self-contained fictional demonstration graph.
