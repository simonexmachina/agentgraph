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

Start the local server with `agentgraph serve`, then open the viewer at [`http://127.0.0.1:8765/viewer`](http://127.0.0.1:8765/viewer).

The graph connects entities and people with labeled edges.

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/depth.png"><img src="/assets/viewer/depth.png" alt="A graph of nodes and edges"></a>
</figure>

## Select a node to view its details

The right pane shows its type, content, source link, dates, and connected edges. You can bookmark an entity to protect it from [automatic expiry](/retention.html).

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/focused.png"><img src="/assets/viewer/focused.png" alt="Message focused with the info pane open."></a>
</figure>

## Focus a node to show only its connected nodes

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/selected.png"><img src="/assets/viewer/selected.png" alt="Message focused showing only its connections."></a>
</figure>

## Increase depth to traverse to related nodes

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/collection.png"><img src="/assets/viewer/collection.png" alt="The full Atlas graph of twelve entities and labeled edges, with no node selected and the info pane closed."></a>
</figure>

## Browse entities in list view

Switch to List to browse entities in a tabular view.

<figure class="architecture-figure architecture-figure-fit">
  <a href="/assets/viewer/list-view.png"><img src="/assets/viewer/list-view.png" alt="List view showing Atlas entities in rows with name, type, platform, and observed date columns; the Decision message is selected and its details pane is open."></a>
</figure>
