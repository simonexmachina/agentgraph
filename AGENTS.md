## Git Authority

- Commit each completed and validated logical unit as you work; do not wait until the end of the session.
- Keep implementation and its tests in the same commit.
- Stage only files that belong to the current task. Never include unrelated user changes in a commit.

## Questions and Changes

When the user asks a question, answer it without changing code unless they explicitly request implementation or a fix is an unambiguously necessary improvement.

## CLI / MCP / Skill Parity

Whenever a CLI command is added or changed (`agentgraph/cli.py`, `agentgraph/cli_query.py`), apply the equivalent change to the MCP server (`agentgraph/mcp/server.py`) and update the agentgraph skill (`.agents/skills/agentgraph/SKILL.md`) in the same pass. All three must stay in sync.

## Shell / Scripting

- **Use `jq` for JSON parsing** in shell scripts and one-liners — prefer it over Python for command-line JSON manipulation.

## Server Logs

- For AgentGraph server runtime diagnostics, read `~/.agentgraph/agentgraph.log`. Do not use MCP transport logs as a substitute.

## Documentation Website

- In this repository, “website” means the public documentation site hosted on GitHub Pages, built from `docs-src/` and previewed with `uv run python scripts/serve_docs.py` at `http://127.0.0.1:8001/`.
- The local `agentgraph serve` endpoint and its `/viewer` route are the graph viewer, not the public website.

## Chrome Extension Publishing

- Publish the Chrome extension only through the GitHub Actions `Extension Publish` workflow. Do not upload builds through the Chrome Web Store dashboard.
- A version bump in `extension/manifest.json`, `extension/package.json`, and `extension/package-lock.json`, followed by a pushed `extension-v*` tag, triggers publishing. The workflow publishes the ZIP to Chrome Web Store and retains it as a workflow artifact; it does not create a GitHub Release.
- The repository requires `CWS_CLIENT_ID`, `CWS_CLIENT_SECRET`, and `CWS_REFRESH_TOKEN` Actions secrets for Chrome Web Store publishing.

## Connector / Core Boundary

Connector-specific logic must live in the connector package (`packages/agentgraph-connector-*/`), not in the main `agentgraph/` package. Violations to watch for:

- **Hardcoded platform names** in `agentgraph/graph/`, `agentgraph/backends/`, or `agentgraph/server/` (outside `server/router.py`). If you're checking `if platform == "slack"` in core code, it belongs in the connector.
- **Platform metadata field names** accessed directly in the main package (e.g. `metadata["slack_user_id"]`, `metadata["gmail_message_id"]`). Connectors own their metadata schema.
- **Hardcoded platform lists** in CLI, migration, or UI code (e.g. `["slack", "gmail", "discord"]`). Use the connector registry to discover platforms dynamically.
- **Importing connector modules** from within `agentgraph/` core code.

The correct pattern: connectors expose standardised interfaces (`BaseConnector` methods, `EntityBatch` output, `resolve_me()` hook) that core code calls generically. If you need platform-specific behaviour, add a hook to `BaseConnector` that the connector overrides.

When a connector needs behaviour that core does not currently provide, treat that as an architectural decision. Ask the human how to provide the needed capability while preserving separation of concerns instead of adding connector-specific branches or commands to core. Prefer generic core extension points, such as connector-owned command hooks, where the connector package owns parsing and behaviour and core only dispatches through the registry.

## Development Standards

- **Write tests as you go.** Every feature bean gets tests in the same commit. Unit tests for pure logic; integration tests (marked `@pytest.mark.integration`) for anything touching the database or external APIs. Integration tests are skipped by default (`pytest -m "not integration"`).
- Unit tests are not required for public documentation website content.
- **Strongly typed Python.** All code uses type hints throughout — function signatures, return types, class attributes. Pydantic models at all data boundaries. Pyright in strict mode for static checking.
