# AgentGraph

**Give the AI agent you already use a searchable world.**

AgentGraph is a local-first, self-hosted CLI and MCP server that turns the sources you choose into a searchable graph for the AI agent you already use. Connect Gmail, Google Drive, Slack, Discord, RSS, and web pages; AgentGraph translates them into local entities, people, relationships, and searchable content.

> **AgentGraph stores context; your agent reasons over it.** It is not an agent, chatbot, hosted graph service, or replacement for the tools you already use.

[See the demo](docs-src/demo.md) · [Install](docs-src/install.md) · [Documentation](https://agentgraph.simonwa.de/) · [Chrome extension](https://chromewebstore.google.com/detail/agentgraph-extension/iilkfclglabllelhjacijldknapbhidi)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs-src/assets/diagrams/architecture-overview-dark.svg">
  <img src="docs-src/assets/diagrams/architecture-overview-light.svg" alt="Observe, Fetch, and Refresh converge on connector packages that read selected services and write to a local graph. Agents access the graph through the CLI or MCP, while Expiry applies the retention model.">
</picture>

## Why AgentGraph

Coding agents work well because their source of truth is already on disk. They can search files, follow references, inspect history, and build a model of the system without the user pasting every relevant detail.

The rest of a person's digital context is fragmented across messages, documents, feeds, and web pages. AgentGraph makes the parts you choose locally searchable and navigable through MCP, so a coding agent can investigate that context instead of starting every conversation blind.

## How context enters the graph

AgentGraph builds context in three ways:

1. **Observe:** the Chrome extension recognizes a supported URL, waits until you have kept it focused for the observation threshold, and reports the resource to your local AgentGraph server. The owning connector fetches it through the source API.
2. **Fetch:** your agent or the CLI can request a specific resource. The connector adds or refreshes its entities, people, and edges before returning.
3. **Refresh:** connector polling and ingest keep configured or already-known resources current in the background.

Observation is also an attention signal. Browser observation updates `observed_at`; direct fetch, polling, ingest, and source changes do not. Observable entities expire after 90 days by default unless they are observed again or bookmarked. See [How AgentGraph works](docs-src/how-it-works.md) and [Entity retention](docs-src/retention.md).

The extension sends recognized URLs, plus the Gmail thread identifier where required, to the local server. It does not upload arbitrary page content to an AgentGraph-operated service.

## Trace a decision across sources

The launch demo asks an existing coding agent:

> Before I reply to Maya, reconstruct the Atlas synchronization decision. What did she require, what did engineering agree, does the Drive plan match, and which research supports the decision? Flag contradictions and link every source.

The agent uses the Graph skill and AgentGraph CLI to search a fictional Gmail thread, traverse a Slack discussion and its authors, check a stale Drive plan, and compare two research documents. Everything is included in one deterministic fixture; no provider credentials, browser observation, or MCP setup are required. [Run the demo](docs-src/demo.md).

## What each connector makes perceptible

| Connector | What it contributes | Context paths |
| --- | --- | --- |
| Gmail | Email threads, participants, subjects, bodies, and attachment references | Observe, fetch, poll, ingest |
| Google Drive, Docs, Sheets | Folders, files, content, ownership, authorship, and containment | Observe, fetch, poll |
| Slack | Channels and DMs, messages, replies, authors, mentions, and attachments | Observe, fetch, poll |
| Discord | Channels, DMs, threads, messages, authors, mentions, and attachments | Observe, fetch, poll |
| RSS | Feeds, posts, dates, authors, and publication relationships | Observe, fetch, poll, ingest |
| Web | Configured pages and bookmarks with titles, text, metadata, and URLs | Observe, fetch |

These connectors are proof of the pattern, not the boundary of the product. A connector can translate an API, export, webhook, local database, browser surface, or structured file into AgentGraph's shared model.

**Bring any service into your agent's world.** Teams can keep private connectors for internal systems, individuals can connect niche tools, and open-source contributors can publish integrations for the wider community. See [Connectors](docs-src/connectors.md) and [Extending AgentGraph](docs-src/extending.md).

## Quickstart

AgentGraph requires Python 3.12+, [`uv`](https://docs.astral.sh/uv/), and Chrome for browser observation.

```bash
git clone https://github.com/simonexmachina/agentgraph
cd agentgraph
uv sync --extra all
source .venv/bin/activate
agentgraph onboard
agentgraph serve
```

Install the [AgentGraph Chrome extension](https://chromewebstore.google.com/detail/agentgraph-extension/iilkfclglabllelhjacijldknapbhidi), browse a supported resource, then verify that context landed:

```bash
agentgraph list-connectors
agentgraph search "project kickoff notes"
agentgraph traverse <entity-id> --depth 2
```

Connect the agent you already use:

```bash
agentgraph mcp-config
```

Use the printed instructions to connect ChatGPT Desktop Work Mode, Codex, Claude Desktop, or Claude Code to the local stdio server. MCP is the preferred connection; the installed skill provides CLI fallback workflows. See [Install](docs-src/install.md) for the complete setup path.

## What AgentGraph is and is not

| AgentGraph is | AgentGraph is not |
| --- | --- |
| Local-first infrastructure | An AI agent or autonomous assistant |
| A CLI and MCP server | A replacement for your agent |
| A searchable graph of selected context | A chatbot or LLM |
| An open connector architecture | A hosted integration SaaS |
| Semantic search plus typed entities and edges | Only a vector database |
| Self-hosted and open source | A project-operated store for your graph data |

## Local data and privacy

Indexed content is stored in SQLite on your machine. Provider credentials stay in the AgentGraph config directory, and source API calls run directly from your machine under your credentials. AgentGraph does not operate a hosted graph service or send indexed content to a project-controlled backend.

An MCP client you connect can receive content from the local graph, subject to that client's data practices. Read the complete [Privacy Policy](docs-src/privacy.md), [Terms of Service](docs-src/terms.md), and [retention model](docs-src/retention.md).

## Interfaces

| Surface | Use it for | Entry point |
| --- | --- | --- |
| MCP | Let an existing agent search, traverse, fetch, and manage context | `agentgraph mcp-config`, `agentgraph mcp-serve` |
| CLI | Search, fetch, ingest, debug, and operate the graph | `agentgraph search`, `agentgraph fetch`, `agentgraph serve` |
| Browser extension | Turn focused browsing on supported URLs into observations | [Chrome Web Store](https://chromewebstore.google.com/detail/agentgraph-extension/iilkfclglabllelhjacijldknapbhidi) |
| Viewer | Inspect entities, people, relationships, and observation state | `http://127.0.0.1:8765/viewer` |

## Documentation

- [Install](docs-src/install.md)
- [Install](docs-src/install.md)
- [How AgentGraph works](docs-src/how-it-works.md)
- [Connectors](docs-src/connectors.md)
- [Configuration](docs-src/configuration.md)
- [Commands](docs-src/commands/index.md)
- [MCP tools](docs-src/mcp/index.md)
- [Extending](docs-src/extending.md)

## Contributing

See [AGENTS.md](AGENTS.md) for repository-specific development rules. Before opening a change, run:

```bash
uv run pytest tests/ -m "not integration and not browser" -q
uv run pyright
uv run ruff check agentgraph/ packages/ scripts/ tests/
```

## License

See [LICENSE](LICENSE).
