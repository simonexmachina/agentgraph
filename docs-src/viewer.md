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

The graph connects entities and people with labeled edges. The captures below follow their original timestamp order.

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/depth.png"><img src="/assets/viewer/depth.png" alt="Depth 2 graph focused on the Decision message, showing a wider Atlas neighborhood with people, messages, and documents."></a>
  <figcaption>At depth 2, the selected Decision message reveals a wider Atlas neighborhood.</figcaption>
</figure>

## Narrow to direct relationships

Reducing the depth to 1 shows only the selected message's immediate relationships.

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/focused.png"><img src="/assets/viewer/focused.png" alt="Depth 1 graph focused on the Decision message, showing its directly connected people, channel, and related messages."></a>
  <figcaption>Depth 1 narrows the view to the Decision message and its immediate relationships.</figcaption>
</figure>

## Select a node to view its details

Return to the full graph and select the Decision message to open its info pane. The pane shows its type, content, source link, dates, and connected edges. You can bookmark an entity to protect it from [automatic expiry](/retention.html).

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/selected.png"><img src="/assets/viewer/selected.png" alt="The full Atlas graph with the Decision message selected and its info pane visible."></a>
  <figcaption>The Decision message stays selected while the full graph is visible.</figcaption>
</figure>

## View the full collection

Close the info pane to return to the complete graph without a selected entity.

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/collection.png"><img src="/assets/viewer/collection.png" alt="Full Atlas graph showing twelve entities and their relationships, with no info pane open."></a>
  <figcaption>The complete Atlas graph, with no entity selected.</figcaption>
</figure>

Search across entities, filter by type, platform, or time range, and sort results to find relevant context. List view shows entities by name, type, platform, and dates.
