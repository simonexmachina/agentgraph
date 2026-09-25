+++
title = "Entity retention"
description = "How AgentGraph observations, timestamps, bookmarks, and expiration determine entity retention."
nav_title = "Retention"
section = "Reference"
order = 5
summary = "Understand which entities expire by observation time, which follow a parent, and which remain only while connected to the graph."
output = "retention.html"
source_path = "docs-src/retention.md"
+++

## Retention policies

Entities use one of four policies:

| Policy | Entity types | Collection rule |
| --- | --- | --- |
| Observed | `Channel`, `Document`, `Email`, `Folder`, `Spreadsheet`, `Task`, `Video` | Delete when `observed_at` (or `created_at` if never observed) is outside the retention window. |
| Owned | `Message` and Gmail attachment `Document` entities | Delete with the parent Channel or Email. |
| Connected | `Person` | Delete when the Person has no incoming or outgoing edges. |
| Persistent | Configured RSS feed `Folder` entities | Never delete automatically. |

RSS entry `Document` entities follow the observed policy. See [RSS browser observations](/rss.html#browser-observations) for how article visits update them.

A stub is not a separate retention category. It follows the policy of the entity it represents. An observable stub begins with `observed_at = NULL`, so its local insertion time controls retention until its own URL is observed.

Google Drive Folder contents are not owned children: a file can remain useful independently or belong to multiple folders. Removing a Folder therefore removes `contains` edges but does not delete its files.

## Bookmarking

Bookmarking an entity protects it from being removed when it expires. A bookmarked Message or Gmail attachment can remain after its parent is deleted.

## Expiration

The server runs expiration daily and applies the following rules:

1. Bookmarked entities are never deleted
1. Delete observed-policy entities whose effective retention timestamp is outside the window.
2. Cascade deletion to their owned children.
3. Detach bookmarked owned children before deleting an expired parent.
4. Delete owned entities left without a parent, including detached children after they are unbookmarked.
5. Delete connected-policy Persons that have no edges.

The retention period is controlled using `AGENTGRAPH_RETENTION_DAYS`, which defaults to 90 days.
