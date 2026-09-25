+++
title = "Graph viewer"
description = "Explore the local AgentGraph visually through its graph, list, and entity detail views."
nav_title = "Viewer"
section = "Start"
order = 35
summary = "See the entities and relationships in your local graph, narrow what is shown, and inspect the underlying content."
output = "viewer.html"
source_path = "docs-src/viewer.md"
+++

Start the local server with `agentgraph serve`, then open [the viewer](http://127.0.0.1:8765/viewer). The screenshots below show the fictional Atlas data from the [demo](/demo.html). Select an image to open it at full size.

## Explore relationships

The graph connects entities and people with labeled edges. Focus on an entity to see its nearby relationships; change the depth to expand or narrow that view.

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/graph.png"><img src="/assets/viewer/graph.png" alt="Graph view focused on an Atlas email, with authored and participated-in connections to Maya Patel and Alex Chen."></a>
  <figcaption>A focused email and its connected people in the graph view.</figcaption>
</figure>

## Browse and filter

Switch to List to scan entities by name, type, platform, and dates. Search across entities, filter by type, platform, or time range, and sort the results to find relevant context.

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/list.png"><img src="/assets/viewer/list.png" alt="List view of fictional Atlas entities with search, type and platform filters, and sortable date columns."></a>
  <figcaption>The list view shows Atlas records from several sources in one place.</figcaption>
</figure>

## Inspect an entity

Select a graph node or list row to open its details. The panel shows available content, a source link, dates, and connected edges. You can focus the graph on the selected entity or bookmark it to protect it from [automatic expiry](/retention.html).

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/details.png"><img src="/assets/viewer/details.png" alt="Detail panel for the fictional Atlas synchronization email, showing its content, source link, dates, and related edges beside the selected list row."></a>
  <figcaption>The detail panel exposes the source content and relationships behind a selected entity.</figcaption>
</figure>
