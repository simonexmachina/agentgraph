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

Start the local server with `agentgraph serve`, then open [the viewer](http://127.0.0.1:8765/viewer). The screenshots below show the fictional Atlas data from the [demo](/demo.html).

The graph connects entities and people with labeled edges. Select a node to inspect it, focus it to see nearby relationships, then increase the depth to expand the neighborhood.

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/collection.png"><img src="/assets/viewer/collection.png" alt="Graph view showing seven Atlas entities and their labeled relationships, with no node selected."></a>
  <figcaption>A collection of seven Atlas entities, connected by labeled edges.</figcaption>
</figure>

## Select a node to view its details

Select a node to open its info pane while keeping the collection in view. The pane shows its type, source link, dates, and connected edges. You can bookmark an entity to protect it from [automatic expiry](/retention.html).

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/selected.png"><img src="/assets/viewer/selected.png" alt="The same Atlas graph collection with the project-atlas channel selected and its info pane visible."></a>
  <figcaption>Selecting #project-atlas opens its details beside the graph.</figcaption>
</figure>

## Focus on the selected entity

Focus on a node to narrow the graph to its immediate neighborhood. Here, #project-atlas is connected to three Slack messages.

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/focused.png"><img src="/assets/viewer/focused.png" alt="Graph focused on the project-atlas channel, showing three directly related Slack messages and their edges."></a>
  <figcaption>Depth 1 shows the three messages directly related to #project-atlas.</figcaption>
</figure>

## Increase the depth

Increase the depth to reveal entities connected through those messages, including people and documents from other sources.

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/depth.png"><img src="/assets/viewer/depth.png" alt="Graph focused on project-atlas at depth 2, showing eight related entities including messages, people, and documents."></a>
  <figcaption>Depth 2 adds more of the neighborhood, including people and documents.</figcaption>
</figure>

Search across entities, filter by type, platform, or time range, and sort results to find relevant context. List view shows entities by name, type, platform, and dates.
