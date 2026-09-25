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
  <a href="/assets/viewer/depth.png"><img src="/assets/viewer/depth.png" alt="Decision message focused at depth 2, with the info pane open and connected people, messages, and documents in the graph."></a>
  <figcaption>Depth 2 shows the focused message’s wider neighborhood.</figcaption>
</figure>

## Narrow to direct relationships

Reducing the depth to 1 shows only the selected message's immediate relationships.

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/focused.png"><img src="/assets/viewer/focused.png" alt="Decision message focused at depth 1, with the info pane open and its immediate connections to people, the channel, and related messages."></a>
  <figcaption>Depth 1 shows the message’s immediate connections.</figcaption>
</figure>

## Select a node to view its details

Return to the full graph and select the Decision message to open its info pane. The pane shows its type, content, source link, dates, and connected edges. You can bookmark an entity to protect it from [automatic expiry](/retention.html).

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/selected.png"><img src="/assets/viewer/selected.png" alt="Full Atlas graph with the Decision message selected and its info pane open; all twelve entities remain visible."></a>
  <figcaption>Selecting a message opens its details without narrowing the graph.</figcaption>
</figure>

## View the full collection

Close the info pane to return to the complete graph without a selected entity.

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/collection.png"><img src="/assets/viewer/collection.png" alt="The full Atlas graph of twelve entities and labeled edges, with no node selected and the info pane closed."></a>
  <figcaption>The unselected view shows the complete graph.</figcaption>
</figure>

Search across entities, filter by type, platform, or time range, and sort results to find relevant context. List view shows entities by name, type, platform, and dates.
