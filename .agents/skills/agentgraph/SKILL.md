---
name: agentgraph
description: Use AgentGraph through its CLI or MCP tools to search and traverse selected local context, retrieve full source-backed evidence, fetch missing or stale resources, and troubleshoot connector availability.
---

# AgentGraph skill

AgentGraph is a local graph of selected messages, documents, people, feeds, pages,
and relationships. Use its CLI or connected MCP tools to investigate that context;
do not query its database or connector internals directly.

Prefer the connected MCP tools when AgentGraph is available through MCP. Use the
`agentgraph` CLI when MCP is unavailable or when a terminal script is the natural
interface. Do not query AgentGraph's SQLite database or connector internals directly.

## Investigate

1. Start with `search` for discovery. If the user supplied a graph ID, platform
   reference, or indexed URL, use `get` directly instead.
2. Read the full entity before making a source-backed claim. Search results and graph
   traversals contain bounded snippets.
3. Inspect direct edges or traverse only when relationships help answer the question.
4. Resolve a stub or fetch a known resource when missing or stale content blocks the
   answer, then repeat the read.
5. Compare source dates and contents. Cite the source URL or entity ID and separate
   source facts from conclusions drawn across sources.

Use search filters when the type, source, author, time window, observation, or
attachments are already known. A search without a query lists matching entities by
date; a query ranks them by relevance.

## Guardrails

- A stub has neither a title nor content.
- Use `source_created_at` and `source_updated_at` for source chronology; local
  `created_at` and `updated_at` describe the graph record.
- Browser observation alone updates `observed_at`; fetch, poll, and ingest do not
  indicate that the user viewed a resource.
- Check connector or authentication state only when availability, freshness, or a
  graph operation calls it into question.
- Confirm Person merges, deletion, credential removal, and removal of bookmark
  protection with the user.
- A multi-target deletion resolves every target before changing the graph; it either
  deletes the resolved set or leaves the graph unchanged.

## References

- Read [references/commands.md](references/commands.md) for command syntax, MCP
  mappings, filters, targets, stubs, and result limits.
- Read [references/data-model.md](references/data-model.md) for entity types,
  relationships, attachments, timestamps, and retention.
- Read [references/operations.md](references/operations.md) for connector discovery,
  authentication, fetching, downloads, polling, connector-owned commands, and server
  transport.
