+++
title = "Extending"
description = "Extend AgentGraph with custom connectors and a type-checked example implementation."
nav_title = "Extending"
section = "Development"
order = 30
summary = "AgentGraph can be extended with custom connectors for your own tools and integrations. This page explains the `BaseConnector` contract and links to a type-checked example."
output = "extending.html"
source_path = "docs-src/extending.md"
+++

## Why extend AgentGraph

AgentGraph is designed to make custom connectors normal. The bundled services are proof of the connector pattern, not the boundary of the product.

- Build connectors for internal tools, private APIs, or niche SaaS products that are specific to your team.
- Keep your own integration logic outside the core package by shipping it as a separate connector package.
- Reuse the same fetch, poll, auth, and graph-upsert model that the built-in Slack, Discord, Google Docs, Drive, Sheets, Gmail, and RSS connectors use.

See [Connectors](/connectors.html) for accurate current coverage, authentication, and context paths across the bundled packages.

<div class="connector-promise"><strong>Bring any service into your agent's world.</strong> A connector can wrap an API, export, webhook, local database, browser-accessible surface, or structured file format.</div>

## Connector behavior

- **Browser observation:** supported URLs trigger targeted fetches after the configured observation threshold.
- **Polling:** connectors that support it store cursor state and fetch changes on a schedule.
- **Ingest:** some connectors expose a broader one-shot historical ingest beyond poll behavior.
- **Download metadata:** file-backed entities can expose `metadata.download_url` and `metadata.mime_type` when an agent needs source bytes.

## Connector interface

`BaseConnector` is the author-facing contract for custom integrations. A connector subclass declares its identity and URL ownership, returns graph-shaped batches from `fetch()`, and can optionally participate in auth, background polling, and historical ingest.

## Authoring a connector

A connector is a Python package that subclasses `BaseConnector`, implements the required fetch path, and registers itself through the `agentgraph.connectors` entry point group. This is the extension point you use to teach AgentGraph about your own systems.

### Base contract

The required shape is small: resolve the URLs your connector owns, implement `fetch()`, and optionally implement polling, ingest, auth, and user identity hooks. Refer to `BaseConnector` in the installed package for the complete, current type-checked contract.

- `source` is the stable connector identifier used by the CLI, MCP server, and registry.
- `url_patterns` declares static browser URL patterns for observation-based fetches. Connectors can override `observation_url_patterns()` to provide derived patterns. These are Chrome match patterns, not filesystem globs: `*` in the path spans `/`, so `https://docs.google.com/document/*` matches `/document/d/abc/edit`. A host may start with `*.` to cover a domain and its subdomains, and an optional `:port` is honoured — a pattern without a port matches any port, one with a port requires it.
- `fetch_policy` controls when a targeted fetch should be skipped because a resource is still fresh.
- `entity_types` declares connector-local resource names that map to shared graph entity
  types. Different connectors may reuse both the resource name and entity type name.
- `can_handle(self, url) -> bool` is the required URL ownership check.
- `resolve_url(self, url)` returns the fetchable resource behind a URL; `resolve_observation_url(self, url, meta)` can asynchronously resolve browser observations and attach generic fetch metadata.
- `fetch(self, resource_type, resource_id, meta=None, account_id=None) -> EntityBatch` is the required runtime fetch hook.
- `normalise_fetch_id(self, resource_id, entity_type) -> tuple[str, ResourceType]` lets a connector translate stored IDs into fetchable IDs when they differ.
- `poll_interval`, `poll_delegates`, `poll_account_ids()`, `poll()`, and `ingest()` are the optional background refresh hooks.
- `run_auth_flow()`, `list_accounts()`, `get_authenticated_user()`, `verify_auth()`, `current_user_id()`, and `current_user_ids()` are the auth and operator-facing hooks.

The contract is intentionally generic: core AgentGraph code calls these hooks without knowing platform-specific field names or APIs.

### Custom entity types

Declare an immutable `EntityTypeDefinition` for every connector-specific type that
should participate in discovery, URL-reference stubs, and default refresh routing:

```python
entity_types: ClassVar[tuple[EntityTypeDefinition, ...]] = (
    EntityTypeDefinition(
        name="Project",
        resource_type="project",
        description="A project tracked in Example.",
    ),
)
```

`name` joins the shared graph vocabulary, so all connectors that emit `Project` can be
queried together. `resource_type` is local to the connector: Jira and Linear can both
use `project` without conflict, and each receives that value in its own `fetch()` call.
Names and resource types must be unique within one connector. The description is
included in MCP discovery metadata so agents learn installed types without making a
separate connector-discovery call.

Storage remains permissive for compatibility: an undeclared `entity_type` can still be
stored and searched, but declaring it is what enables automatic discovery and routing.

### Example implementation

The [type-checked custom connector example](https://github.com/simonexmachina/agentgraph/blob/main/examples/custom_connector.py) shows URL resolution, targeted fetches, polling, and historical ingest. Adapt its placeholder API helpers and add provider-specific authentication in your connector package.

### Output model

- `EntityRecord` for core or connector-defined graph entities.
- `EntityMetadataPatch` for connector-declared, non-material metadata changes to an existing entity. Patches merge metadata and refresh `synced_at` without changing content, `updated_at`, or publishing an entity upsert event.
- `PersonRecord` for authors and participants.
- `EdgeRecord` for authored, posted-in, replied-to, mentions, references, and similar relationships.

### Packaging

Register the connector in `pyproject.toml`.

```toml
[project.entry-points."agentgraph.connectors"]
myplatform = "agentgraph_connector_myplatform:MyConnector"
```
