+++
title = "list-connectors"
description = "CLI reference for agentgraph list-connectors."
nav_title = "list-connectors"
section = "Reference"
order = 19
summary = "`agentgraph list-connectors` is the operator view of installed connectors, credential state where applicable, URL ownership, and sync behavior."
output = "commands/list-connectors.html"
source_path = "docs-src/commands/list-connectors.md"
+++

## Synopsis

```bash
agentgraph list-connectors [--verify] [--json]
agentgraph connector <source> <command> [args...] [--json]
agentgraph connector <source> --help
```

## Example

```bash
agentgraph list-connectors
agentgraph list-connectors --json
agentgraph list-connectors --verify
agentgraph connector rss add https://simonwillison.net/atom/everything/
agentgraph connector rss remove https://simonwillison.net/atom/everything/
agentgraph connector rss --help
agentgraph connector rss import-opml feeds.opml --all
agentgraph connector rss import-opml feeds.opml --select 1,3-5
agentgraph connector web observe http://localhost:3000/*
agentgraph connector web observe http://localhost:3000/content/research.md
agentgraph connector web list
agentgraph connector web observe http://localhost:3000/* --remove
agentgraph connector web fetch https://www.thecgo.org/research/energy-superabundance/ --compact
```

RSS `add` validates each supplied URL as an RSS or Atom feed before saving, then
queues an RSS poll. Invalid feeds and HTML pages are rejected without changing
the connector configuration.

RSS `remove` removes exact configured feed URLs without fetching or validating them.

For RSS OPML imports, omit `--all` and `--select` in an interactive terminal to choose
feeds with a checkbox prompt.

The web connector stores browser observation rules with `observe`; add `--remove` to
remove a rule. A URL
without a trailing `/*` observes that exact URL; a URL ending in `/*` observes every URL
under that literal prefix. Rules are stored in `~/.agentgraph/config.toml`, or in
`config.yaml` when that file exists. The browser extension refreshes the rules from the
running server periodically.

Web is a required dependency of `agentgraph-server`, so `list-connectors` includes it
even when no web URLs are configured. Removing it from an environment is undone by
normal `uv run` synchronization while the core package declares that dependency.

Web fetches preserve the original response by default. Use `--compact` for a one-off
fetch when a page exceeds the size limit because of inline styles, scripts, or comments:

```bash
agentgraph connector web fetch https://example.com/large-page --compact
```

This removes those non-content blocks while the response is streamed, before the size
limit is applied. Compaction affects only that command; bookmarks, observations, RSS
article hydration, and background fetches retain the original HTML behavior.

Connectors that do not use credentials, such as RSS and generic web, omit auth
status in the human-readable table and report `null` auth fields in JSON.

The table includes connector-declared entity type names. JSON output includes each
declaration's shared `name`, connector-local `resource_type`, and `description` under
`entity_types`. Core types inherited without a connector-specific mapping are shown as
`core` in the table and are not repeated in that connector's JSON declarations.

Use `--verify` only when credential validity is uncertain; it performs live provider
API checks before reporting connector status.

`Last synced` is the timestamp of the connector's most recent successful poll, including
polls that returned no events. Connectors with account-scoped cursors report the latest
successful poll across their accounts; a connector whose cursor was reset reports `never`
until its next successful poll.
