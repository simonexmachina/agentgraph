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

The graph connects entities and people with labeled edges.

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/collection.png"><img src="/assets/viewer/collection.png" alt="Full Atlas graph showing twelve entities and their relationships, with no info pane open."></a>
  <figcaption>The full Atlas graph, with no entity selected.</figcaption>
</figure>

## Select a node to view its details

Select a node to open its info pane while keeping the collection in view. The pane shows its type, source link, dates, and connected edges. You can bookmark an entity to protect it from [automatic expiry](/retention.html).

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/selected.png"><img src="/assets/viewer/selected.png" alt="The full Atlas graph with the Decision message selected and its info pane visible."></a>
  <figcaption>Selecting the Decision message opens its details beside the full graph.</figcaption>
</figure>

## Focus on the selected entity

Focus on the selected message to narrow the graph to its immediate neighborhood.

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/focused.png"><img src="/assets/viewer/focused.png" alt="Depth 1 graph focused on the Decision message, showing its directly connected people, channel, and related messages."></a>
  <figcaption>Depth 1 shows the Decision message and its immediate relationships.</figcaption>
</figure>

## Increase the depth

Increase the depth to reveal entities connected through the selected message's immediate relationships.

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/depth.png"><img src="/assets/viewer/depth.png" alt="Depth 2 graph focused on the Decision message, showing a wider Atlas neighborhood with people, messages, and documents."></a>
  <figcaption>Depth 2 expands the neighborhood to show more related people, messages, and documents.</figcaption>
</figure>

Search across entities, filter by type, platform, or time range, and sort results to find relevant context. List view shows entities by name, type, platform, and dates.
