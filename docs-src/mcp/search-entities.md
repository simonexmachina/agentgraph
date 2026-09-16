+++
title = "search_entities_tool"
description = "MCP reference for search_entities_tool."
nav_title = "search_entities_tool"
section = "MCP"
order = 15
summary = "Use `search_entities_tool` for discovery from natural-language questions, for deterministic filtering by type and metadata, or for both at once."
output = "mcp/search-entities.html"
source_path = "docs-src/mcp/search-entities.md"
+++

## Signature

```text
search_entities_tool(query=null, entity_types=null, platform=null, filters=null,
                     since=null, authored_by_me=false, has_attachments=false,
                     limit=null, order_by=null, min_score=0.03,
                     refresh=false, observed_since=null) -> structured MCP result
```

## Notes

- `query` is optional. With it, results are ranked by hybrid full-text and vector
  relevance with every filter applied as a hard predicate. Without it there is no
  ranking: the filters select the entities and `order_by` sorts them, newest first.
- `limit` defaults to 10 with a query and 50 without; `min_score` is ignored
  without a query. `data.returned` is the number returned and `data.has_more`
  reports whether more matching results exist.
- `since` filters `updated_at`; `observed_since` filters browser observation time
  (`observed_at`), excluding never-observed entities. Both accept ISO timestamps or
  relative durations such as `30m`, `12h`, `2d` and include the cutoff itself.
  When combined, both cutoffs must match. Neither changes ordering. For example,
  `observed_since="2d", order_by="observed_at"` lists recently observed entities.
- `order_by` takes a date column (`created_at`, `updated_at`, `source_created_at`,
  `source_updated_at`, `observed_at`, `synced_at`). Combined with a query, results
  are relevance-filtered but date-sorted.
- for images and uploaded files, search `Message` entities rather than `Document`,
  with `has_attachments=true`
- Gmail email attachments are represented as Gmail `Document` stubs referenced by the owning `Email`
- the tool description returned by MCP discovery contains the complete alphabetical
  catalog of core and installed connector-defined entity types
- scope by source with `platform="gmail"`, or with an equivalent column filter such
  as `filters={"platform": "gmail"}`; other `filters` keys are matched against the
  entity's metadata
- results contain bounded content snippets and set `content_truncated` when shortened;
  use `get_entity_tool` for full stored content. Every entity includes `is_stub`
  and includes `source_url` when available.
- `refresh=true` allows connector-owned presentation metadata, such as temporary
  attachment URLs, to be refreshed before returning; it does not replace a targeted
  source fetch for stale content
