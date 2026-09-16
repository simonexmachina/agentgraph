+++
title = "download_entity_tool"
description = "MCP reference for download_entity_tool."
nav_title = "download_entity_tool"
section = "MCP"
order = 22
summary = "Use `download_entity_tool` when the agent needs the source bytes behind a file-backed entity rather than only the indexed graph content."
output = "mcp/download-entity.html"
source_path = "docs-src/mcp/download-entity.md"
+++

## Signature

```text
download_entity_tool(entity_id, output_path=None) -> structured MCP result
```

## Returns

- local file path
- byte count
- filename
- platform
- MIME type

## Notes

- Gmail attachments are represented as Gmail `Document` stubs referenced by the owning `Email`
- Fetch or re-fetch the thread first, traverse one hop to discover the attachment document, then call this tool with that document ID
- Platform refs such as `gmail/document/attachment/<message-id>/<attachment-id>` work after the stub exists in the graph
