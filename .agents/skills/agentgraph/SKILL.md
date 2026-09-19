---
name: agentgraph
description: Use AgentGraph through its CLI or MCP tools to search and traverse selected local context, retrieve full source-backed evidence, fetch missing or stale resources, and troubleshoot connector availability.
---

# AgentGraph skill

AgentGraph is a local knowledge graph for the agent the user already uses. It stores
selected messages, documents, people, feeds, pages, and relationships; the agent is
responsible for searching that graph, comparing evidence, and explaining its reasoning.

Prefer the connected MCP tools when AgentGraph is available through MCP. Use the
`agentgraph` CLI when MCP is unavailable or when a terminal script is the natural
interface. Do not query AgentGraph's SQLite database or connector internals directly.

## Commands that use the local server

`agentgraph poll` and connector or authentication commands that queue a poll or
historical ingest always call the local AgentGraph server. Reads (`search`, `get`,
`edges`, `traverse`) and `fetch`/`download` use it when it is reachable and
otherwise read the database directly, so they work with no server running.

Run ordinary commands without setting `AGENTGRAPH_QUERY_TRANSPORT`, even when the
server is stopped. The default `auto` transport tries the Unix
socket, then TCP, then falls back to in-process automatically.

Many agent sandboxes deny loopback TCP but allow an allowlisted Unix socket, so the
server listens on both and clients prefer the socket. If a command fails because the
sandbox blocked it:

- For `poll`, ingest, or auth commands there is no fallback — request permission to
  contact the local server, or ask the user to allowlist the socket
  (`~/.agentgraph/agentgraph.sock`), then retry.
- For reads, retry with `AGENTGRAPH_QUERY_TRANSPORT=in-process` only after an actual
  sandbox connection failure. This bypasses server probes for that retry.

To diagnose server connectivity explicitly, use
`AGENTGRAPH_QUERY_TRANSPORT=server agentgraph search x --limit 1`; it reports whether
the server is reachable instead of silently falling back.

See the Coding agent sandboxes section of the configuration docs for the per-agent
allowlist settings.

## Investigation workflow

1. Discover likely entities with `agentgraph search "<query>" --json` or
   `search_entities_tool`.
2. Open promising results with `agentgraph get <target> --json` or
   `get_entity_tool` to read full content and source metadata. Search results
   contain bounded snippets and set `content_truncated` when content was shortened.
3. Follow relationships with `agentgraph edges`, `agentgraph traverse`,
   `get_edges_tool`, or `traverse_graph_tool`.
4. If an entity is a stub, or known context is stale, resolve or re-fetch it through
   the owning connector and then repeat the read or traversal.
5. Compare source dates and contents. Distinguish source facts from inference and cite
   each source URL or entity identifier used.

Start with search unless the user already supplied a graph ID, platform reference, or
known indexed URL. `search` covers both jobs: pass a query string for ranked
discovery, and add or use only its filters (`--type`, `--platform`, `--filter`,
`--since`, `--observed-since`, `--mine`, `--has-attachments`) when the entity type or
constraints are already known. Omitting the query string turns it into a
deterministic listing ordered by date.

Use `--observed-since 2d` (MCP: `observed_since="2d"`) for entities observed in
the browser within the last two days. It accepts the same relative durations and
ISO timestamps as `--since`, which filters `updated_at`. Both cutoffs are inclusive;
when combined, both must match. Never-observed entities are excluded by
`--observed-since`. Add `--order-by observed_at` to sort by observation time.

## Context lifecycle

`agentgraph list-connectors --json` (MCP: `list_connectors_tool`) reports each
connector's declared resource `entity_types`, including standard graph types,
with their names, resource types, and source-specific descriptions. Person
identities are handled separately. An empty list means the connector has not
declared resource types; it does not mean the source has no indexed entities.
The CLI table displays `-` for this empty list.

- **Connect** is setup: installed connectors and their authentication/configuration
  determine which selected sources AgentGraph can access.
- **Observe** records human attention to a supported browser URL and triggers a
  targeted connector fetch.
- **Fetch** retrieves missing or stale context at the agent's request.
- **Refresh** uses polling or connector-owned ingest commands to update configured or
  already-known context.

Only browser observation updates `observed_at`. Direct fetch, polling, and ingest can
change graph content without implying that the human viewed it.

## Evidence rules

- Use full entity content before making a source-backed claim; do not treat a search
  snippet as the complete source.
- Treat an entity with no title and no content as an unresolved stub.
- Use source timestamps for chronology. `created_at` and `updated_at` describe the
  local graph record; `source_created_at` and `source_updated_at` describe the source.
- Chat uploads live on `Message.metadata.attachments`. Gmail attachments are
  `Document` stubs referenced by their owning `Email` and are downloaded separately.
- Inspect connector and auth state only when freshness matters, a fetch is required,
  or a graph operation reports a connector or credential problem.
- Never merge Person entities without user confirmation. Treat delete, credential
  removal, and unbookmarking as destructive actions.

## References

- Read [references/commands.md](references/commands.md) for CLI syntax, MCP mappings,
  target formats, stub resolution, and result-size behavior.
- Read [references/data-model.md](references/data-model.md) for entity types, edges,
  attachment representation, timestamps, and retention semantics.
- Read [references/operations.md](references/operations.md) for connectors, auth,
  polling, connector-owned commands, downloads, bookmarks, deletion, person merging,
  server setup, and skill installation.
