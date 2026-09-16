+++
title = "MCP tools"
description = "Reference for the AgentGraph MCP tool surface."
nav_title = "MCP tools"
section = "MCP"
order = 10
summary = "These are the tools exposed by `agentgraph mcp-serve`. They mirror the graph operations available in the CLI, but are shaped for agent clients."
output = "mcp/index.html"
source_path = "docs-src/mcp/index.md"
+++

## Responses

Every tool returns typed structured content. On success, read
`{"status": "ok", "data": ...}`. Expected failures set the MCP `isError` flag
and return `{"status": "error", "code": ..., "message": ..., "recovery": ...}`.
Use the error code to choose a recovery step; `recovery` is present only when one is
available.

Search results include `returned` and `has_more`. Entity payloads include `is_stub`
and `source_url` when the connector provides a canonical web URL. A stub has neither
a title nor content; fetch it before relying on source details.

## Discovery

- [`list_connectors_tool`](/mcp/list-connectors.html) - inspect installed connectors, auth state, and valid platform values.
- [`list_auth_providers_tool`](/mcp/list-auth-providers.html) - inspect provider-level authentication state.
- [`authenticate_provider_tool`](/mcp/authenticate-provider.html) - run connector-owned provider authentication.
- [`remove_auth_provider_tool`](/mcp/remove-auth-provider.html) - remove stored credentials for an auth provider.
- [`run_connector_command_tool`](/mcp/run-connector-command.html) - run connector-owned commands.

## Query and traversal

- [`search_entities_tool`](/mcp/search-entities.html) - hybrid search across the graph, with structured filtering by type and metadata.
- [`get_entity_tool`](/mcp/get-entity.html) - retrieve one entity by ID, platform reference, or indexed URL.
- [`get_edges_tool`](/mcp/get-edges.html) - inspect direct edges.
- [`traverse_graph_tool`](/mcp/traverse-graph.html) - build a local subgraph from one entity.

## Fetch and files

- [`fetch_entity_tool`](/mcp/fetch-entity.html) - fetch by platform and platform ID.
- [`fetch_entity_by_id_tool`](/mcp/fetch-entity-by-id.html) - fetch by internal entity UUID.
- [`download_entity_tool`](/mcp/download-entity.html) - download the source file behind an entity.
- [`poll_connectors_tool`](/mcp/poll-connectors.html) - trigger background polling for one connector or all polling connectors.

## State

- [`bookmark_entity_tool`](/mcp/bookmark-entity.html) - add or remove bookmark protection.
- [`delete_entity_tool`](/mcp/delete-entity.html) - remove an entity and its connected edges.
- [`add_demo_tool`](/mcp/add-demo.html) - add the fictional Atlas demo fixtures.
- [`remove_demo_tool`](/mcp/remove-demo.html) - remove the marked Atlas demo fixtures.
- [`unify_persons_tool`](/mcp/unify-persons.html) - merge duplicate Person entities after confirmation.
