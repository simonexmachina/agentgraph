+++
title = "search"
description = "CLI reference for agentgraph search."
nav_title = "search"
section = "Reference"
order = 11
summary = "`agentgraph search` is the one read command for finding entities: give it a question, a set of filters, or both."
output = "commands/search.html"
source_path = "docs-src/commands/search.md"
+++

## Synopsis

```bash
agentgraph search [QUERY] [--type TYPE] [--platform PLATFORM] [--filter key=value] \
                  [--since 24h] [--observed-since 2d] [--mine] [--has-attachments] \
                  [--limit N] [--order-by FIELD] [--min-score SCORE] [--json]
```

`QUERY` is optional, and every filter applies either way:

- **With `QUERY`** - hybrid full-text and vector retrieval, filters applied as hard predicates, ordered by relevance score. `--limit` defaults to 10.
- **Without `QUERY`** - no retrieval at all: the filters select the entities and `--order-by` sorts them, newest first. `--min-score` is ignored. `--limit` defaults to 50.

## Use it for

- broad discovery from natural-language text
- narrowing results to one entity type, platform, or time window
- listing recent entities in one source, with no query at all
- finding your own authored entities
- finding `Message` entities that contain uploaded files or images
- listing Gmail attachment `Document` stubs referenced by email threads
- returning machine-readable results with `--json`

## Key arguments

- `QUERY` - optional free-text query. Omit it to select by filters alone.
- `--type` - repeatable entity type filter such as `Message` or `Document`
- `--platform` - source filter such as `slack`, `gmail`, or `gdocs`. Shorthand for
  `--filter platform=...`; an explicit `--filter` wins.
- `--filter` - repeatable `key=value` filter against columns or metadata
- `--since` - only entities with `updated_at` at or after an ISO timestamp or
  relative cutoff such as `12h`, `30m`, `2d`
- `--observed-since` - only entities with `observed_at` at or after an ISO timestamp
  or relative cutoff, excluding never-observed entities. Browser observation sets
  this timestamp; fetching and syncing do not. When combined with `--since`, both
  cutoffs must match. Neither cutoff changes ordering.
- `--mine` - only entities authored by the authenticated user
- `--has-attachments` - only `Message` entities with chat-style attachments; Gmail
  email attachments are `Document` stubs
- `--order-by` - sort by a date column (`created_at`, `updated_at`,
  `source_created_at`, `source_updated_at`, `observed_at`, `synced_at`) instead of
  relevance. With a `QUERY`, results are relevance-filtered but date-sorted.
- `--min-score` - suppress weak matches; ignored without a `QUERY`

## Examples

```bash
agentgraph search "project kickoff notes" --type Document --limit 10
agentgraph search "images from standup" --type Message --platform slack --json
agentgraph search "atlas" --since 7d --platform slack
agentgraph search --observed-since 2d --order-by observed_at
agentgraph search "project notes" --observed-since 12h --json
agentgraph search --filter platform=slack --since 24h --limit 20
agentgraph search --type Message --has-attachments --since 7d --json
agentgraph search --type Document --filter platform=gmail --json
```
