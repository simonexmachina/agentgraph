+++
title = "Connectors"
description = "What AgentGraph's bundled connectors let an agent perceive and how to build connectors for other services."
nav_title = "Connectors"
section = "Start"
order = 50
summary = "Connectors translate source-specific resources into AgentGraph entities, people, edges, observations, and searchable content."
toc = false
output = "connectors.html"
source_path = "docs-src/connectors.md"
aliases = ["rss.html"]
+++

AgentGraph's bundled connectors are integrations for common services, and examples of an open connector pattern. Each connector owns its URLs, authentication, fetch logic, refresh behavior, and source metadata while returning the same graph-shaped output to core AgentGraph.

| Connector | What it lets the agent perceive |
| --- | --- |
| Gmail | Email threads, participants, subjects, bodies, and attachment references |
| Google Drive | Folders and files, content, ownership, and containment |
| Google Docs | Document content, ownership, authorship, and source dates |
| Google Sheets | Sheet values, ownership, authorship, and source dates |
| Slack | Channels and DMs, messages, replies, authors, mentions, and attachments |
| Discord | Channels, DMs, threads, messages, authors, mentions, and attachments |
| RSS | Feeds, posts, dates, authors, and publication relationships |
| Web | Configured pages and bookmarks with titles, text, metadata, and URLs |

## Extensible by design

A connector is a Python package that translates a service into local entities, people, edges, observations, and searchable content. Connectors can be built around:

- an API, export, or webhook;
- a local database or structured file;
- a browser-accessible resource;
- an internal system used by one team; or
- a niche public service maintained by its own community.

<div class="connector-promise"><strong>Bring any service into your agent's world.</strong> Build a private connector for internal context, publish an integration for a tool you use, or contribute a connector that provides an integration for others to use.</div>

See [Extending AgentGraph](/extending.html) for the complete interface, example connector, packaging, and registration instructions.

## Community direction

The bundled connectors prove the architecture; they are not presented as a complete integration ecosystem. The long-term direction is a community-maintained library spanning developer tools, issue trackers, notes, calendars, research libraries, media, CRMs, support systems, and the unusual services that matter to individual users.
