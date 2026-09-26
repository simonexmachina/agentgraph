+++
title = "How AgentGraph works"
description = "How observation, fetch, refresh and retention fit together."
nav_title = "How it works"
section = "Start"
order = 40
summary = "Context enters AgentGraph through browser observation, agent-initiated fetches, and refresh. Every path uses connector packages to produce one local graph."
output = "how-it-works.html"
source_path = "docs-src/how-it-works.md"
+++

AgentGraph provides a CLI and an MCP server so your agents have a searchable, traversable representation of your digital world – messages, documents, people, feeds, and web pages.

<figure class="architecture-figure architecture-figure-fit" tabindex="0">
  <img src="/assets/diagrams/architecture-overview-dark.svg" alt="Observe, Fetch, and Refresh converge on connector packages that read selected services and write to a local graph. Agents access the graph through the CLI or MCP, while Expiry applies the retention model.">
</figure>

## Three ways context enters

### Observe

When your browser remains focused for 3 seconds on a page that has an installed connector, the Chrome extension sends the URL to the local server.

The owning connector then fetches the resource through its source API and inserts entities, people, and edges into the graph.

Unknown URLs are ignored - this is targeted capture, not a copy of arbitrary browsing. The extension talks only to `localhost` and does not send any page content.

### Fetch

An agent can request a resource directly with `agentgraph fetch <platform> <resource-id>` or refresh an existing graph entity with `agentgraph fetch-entity <entity-id>`.

### Refresh

Connectors poll source APIs for changes to keep the entity updated in the graph when the resource changes.

## The graph model

Connectors add the following items to the graph:

- **Entities:** channels, messages, email threads, documents, spreadsheets, folders, and web or feed documents.
- **People:** source identities, unified automatically when connectors provide the same canonical email and mergeable manually with confirmation otherwise.
- **Edges:** relationships such as `authored`, `participated_in`, `posted_in`, `replied_to`, `mentions`, `contains`, and `references`.

When a connector discovers a linked resource without fetching its full contents, AgentGraph creates a **stub**: a lightweight placeholder that preserves the entity and its relationships. A stub is hydrated with the complete resource only when it is explicitly fetched or resolved.

## Attention and retention

Observation provides an explicit signal about which resources mattered to the user. Observable entities, including Messages, expire after `AGENTGRAPH_RETENTION_DAYS`, which defaults to 90 days, measured from their latest observation or their local insertion time if never observed. Messages can also be deleted when their parent channel or thread expires, while people remain only while connected to the graph.

Entities can be bookmarked to protect them from automatic expiration.

See [Entity retention](/retention.html) for the complete retention policy.

## Where the data goes

The graph stores content, metadata, embeddings, edges, observations, and connector cursors in a local SQLite database. Calls to source APIs run from your machine using credentials stored in the AgentGraph config directory.
