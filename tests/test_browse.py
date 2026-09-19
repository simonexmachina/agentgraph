"""Tests for the graph browse endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from agentgraph.core.context import set_backend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entity(
    *,
    entity_type: str = "Document",
    platform: str = "gdocs",
    title: str = "Test",
) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "entity_type": entity_type,
        "platform": platform,
        "platform_entity_id": "pe-" + str(uuid4())[:8],
        "title": title,
        "content": "",
        "metadata": {},
        "created_at": None,
        "updated_at": "2025-01-01T00:00:00",
    }


def _edge(src: str, tgt: str, edge_type: str = "authored") -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "edge_type": edge_type,
        "platform": "gdocs",
        "properties": {},
        "source_entity_id": src,
        "target_entity_id": tgt,
        "source_ref": None,
        "target_ref": None,
    }


def _mock_backend(**overrides: Any) -> Any:
    backend = MagicMock()
    defaults: dict[str, Any] = {
        "search_entities": AsyncMock(return_value=[]),
        "get_entity_by_id": AsyncMock(return_value=None),
        "get_entities_by_id_prefix": AsyncMock(return_value=[]),
        "get_entity_by_platform": AsyncMock(return_value=None),
        "get_entities_by_ids": AsyncMock(return_value=[]),
        "get_edges_for_entities": AsyncMock(return_value=[]),
        "traverse_graph": AsyncMock(return_value={"nodes": [], "edges": []}),
        "list_entities": AsyncMock(return_value=[]),
    }
    for name, value in {**defaults, **overrides}.items():
        setattr(backend, name, value)
    return backend


def test_server_exposes_only_viewer_extension_and_sync_routes() -> None:
    from starlette.routing import Mount, Route

    from agentgraph.server.app import app

    paths = {route.path for route in app.routes if isinstance(route, Route | Mount)}
    assert {
        "/api/meta",
        "/api/capabilities",
        "/api/entities/search",
        "/api/entities/delete",
        "/api/entities/{ref:path}",
        "/api/entities/{ref:path}/edges",
        "/api/entities/{ref:path}/bookmark",
        "/api/entities/{ref:path}/fetch",
        "/api/entities/{ref:path}/download",
        "/api/fetches",
        "/api/persons/unify",
        "/api/graph/nodes",
        "/api/graph/edges",
        "/api/graph/traverse/{ref:path}",
        "/api/sync/poll",
        "/api/sync/ingest",
        "/report-observation",
        "/report-dwell",
        "/api/extension/fetch",
        "/api/extension/page",
        "/api/extension/bookmark",
        "/",
        "/viewer",
        "/health",
    } <= paths
    # Endpoints must not name the client that calls them. /api/cli and /api/query
    # were retired for the resource paths above; neither had an independently
    # installed consumer, so there are no compatibility aliases to keep.
    assert not {path for path in paths if path.startswith(("/api/cli", "/api/query"))}
    assert {
        "/api/entities/entity-by-url",
        "/api/admin/relink",
        "/static",
    }.isdisjoint(paths)


def test_viewer_uses_bookmark_symbol_without_status_row() -> None:
    """The web viewer presents bookmark state only through the symbol control."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert 'id="detail-bookmark"' in viewer_html
    assert 'id="detail-focus"' in viewer_html
    assert 'id="detail-delete"' in viewer_html
    assert 'class="bookmark-glyph"' in viewer_html
    assert 'class="focus-glyph"' in viewer_html
    assert 'class="delete-glyph"' in viewer_html
    assert "Bookmark status" not in viewer_html
    assert "aria-pressed" in viewer_html
    assert "applyZoomStyles();" in viewer_html
    assert "cy.style().update();" in viewer_html
    assert "/api/entities/${encodeRef(entity.id)}`" in viewer_html
    assert "detailFocus.onclick = () => {" in viewer_html
    assert "if (readUrlState().node_id !== entity.id) {" in viewer_html
    assert "lookupInput.value = '';" in viewer_html
    assert "depthInput.value = 1;" in viewer_html
    assert "syncDepthEnabled();" in viewer_html


def test_viewer_supports_zero_depth_for_a_node_only_view() -> None:
    """The viewer must retain zero instead of treating it as an absent depth."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert 'id="depth-input" min="0" max="4"' in viewer_html
    assert "params.depth !== undefined && params.depth !== 1" in viewer_html
    assert "depthInput.value === '' ? undefined : Number(depthInput.value)" in viewer_html


def test_viewer_unifies_people_and_refreshes_the_active_view() -> None:
    """Merging refetches the active collection and canonical Person detail."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "function renderPersonUnificationControls(entity)" in viewer_html
    assert "Merge duplicate people" in viewer_html
    assert "/api/persons/unify" in viewer_html
    assert "const primary = result.primary;" in viewer_html
    assert "await forceRefreshGraph();" in viewer_html
    assert "await showEntityDetail(primary.id);" in viewer_html


def test_viewer_shows_merged_people_on_canonical_person() -> None:
    """Merged person identities are visible from the canonical Person detail panel."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "function renderMergedPeople(metadata)" in viewer_html
    assert "metadata.merged_people" in viewer_html
    assert "Merged people (${people.length})" in viewer_html
    assert "detailBody.appendChild(mergedPeople);" in viewer_html


def test_viewer_heading_links_to_empty_viewer_state() -> None:
    """The top-level product link clears all URL-backed viewer state."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert '<h1><a href="/viewer">AgentGraph</a></h1>' in viewer_html


def test_viewer_renders_standard_web_url_links() -> None:
    """Graph entities expose source links through the standard web_url metadata field."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "function getPlatformUrl(entity)" in viewer_html
    assert "metadata.web_url" in viewer_html
    assert "isHttpUrl(metadata.web_url) ? metadata.web_url : null" in viewer_html
    assert "metadata.link" not in viewer_html
    assert "metadata.feed_url" not in viewer_html
    assert "detailBody.appendChild(row('Link', linkHtml))" in viewer_html


def test_viewer_renders_recorded_duration_time_in_entity_detail() -> None:
    """Recorded observation duration is visible in the entity detail panel."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "Number(entity.cumulative_observation_duration_ms || 0) > 0" in viewer_html
    assert "detailBody.appendChild(row('Observation time', formatObservationDuration(entity.cumulative_observation_duration_ms)))" in viewer_html


def test_viewer_renders_available_entity_timestamps_in_detail() -> None:
    """Entity lifecycle and browser observation times are visible in the detail panel."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "row('Created', formatDate(entity.created_at))" in viewer_html
    assert "row('Updated', formatDate(entity.updated_at))" in viewer_html
    assert "row('Source created', formatDate(entity.source_created_at))" in viewer_html
    assert "row('Source updated', formatDate(entity.source_updated_at))" in viewer_html
    assert "row('Observed', formatDate(entity.observed_at))" in viewer_html


def test_viewer_renders_html_content_through_a_safe_reader() -> None:
    """Stored HTML uses the reader pipeline and has an escaped source fallback."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "function safeHtmlPreview(source)" in viewer_html
    assert "new window.Readability(documentNode).parse()" in viewer_html
    assert "window.DOMPurify.sanitize(candidate" in viewer_html
    assert "FORBID_TAGS: ['form', 'iframe', 'input', 'object', 'script', 'style', 'svg', 'video']" in viewer_html
    assert "sourceView.textContent = source" in viewer_html
    assert "detailBody.appendChild(renderContent(entity))" in viewer_html
    assert "metadata.content_type" in viewer_html


def test_viewer_disables_unsupported_content_tabs_and_can_collapse_content() -> None:
    """Non-HTML content has inert tabs and every content block can be collapsed."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "if (!isHtml)" in viewer_html
    assert "previewTab.disabled = true;" in viewer_html
    assert "sourceTab.disabled = true;" in viewer_html
    assert "format.setAttribute('role', 'button');" in viewer_html
    assert "format.setAttribute('aria-expanded', 'true');" in viewer_html
    assert "format.addEventListener('keydown', (event) =>" in viewer_html
    assert "tabs.hidden = isExpanded;" in viewer_html
    assert "format.title = isExpanded ? 'Show content' : 'Hide content';" in viewer_html


def test_viewer_initial_layout_contains_all_nodes() -> None:
    """The graph's initial layout fits every node and its label in the viewport."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "const DEFAULT_LAYOUT_PADDING = 60;" in viewer_html
    assert "arrowScale: 1.2" in viewer_html
    assert "const READABLE_GRID_CELL_WIDTH = 205;" in viewer_html
    assert "const READABLE_GRID_CELL_HEIGHT = 104;" in viewer_html
    assert "function fitGraphToViewport()" in viewer_html
    assert "cy.fit(cy.nodes(), DEFAULT_LAYOUT_PADDING);" in viewer_html
    assert "function readableGridPositionFor(index, count)" in viewer_html
    assert "function readableGridPositions()" in viewer_html
    assert "const GRID_GUTTER = 24;" in viewer_html
    assert "const gutter = GRID_GUTTER / zoom;" in viewer_html
    assert "const cellWidth = Math.max(READABLE_GRID_CELL_WIDTH, bounds.w + gutter);" in viewer_html
    assert "const cellHeight = Math.max(READABLE_GRID_CELL_HEIGHT, bounds.h + gutter);" in viewer_html
    assert "const gridLayout = () => cy.layout({" in viewer_html
    assert "window.__agentGraphViewer = { cy };" in viewer_html
    assert "name: 'preset'" in viewer_html
    assert "positions: readableGridPositions()" in viewer_html
    assert "nodeDimensionsIncludeLabels: true" in viewer_html
    assert "const refinedLayout = cy.layout(coseOptions);" in viewer_html
    assert "function packGraphComponents()" in viewer_html
    assert "packGraphComponents();" in viewer_html
    assert "function resolveNodeOverlaps()" in viewer_html
    assert "resolveNodeOverlaps();" in viewer_html
    assert "ensureReadableDefaultZoom" not in viewer_html


def test_viewer_renders_text_inside_rounded_rectangle_nodes() -> None:
    """Graph nodes are rounded boxes sized to contain their labels."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "'shape': 'round-rectangle'" in viewer_html
    assert "'background-color': '#ffffff'" in viewer_html
    assert "'color': '#1f2937'" in viewer_html
    assert "'border-opacity': 1" in viewer_html
    assert "'text-valign': 'center'" in viewer_html
    assert "'text-halign': 'center'" in viewer_html
    assert "function formatNodeLabel(label)" in viewer_html
    assert "const maxCharsPerLine = 18;" in viewer_html
    assert "const maxLines = 3;" in viewer_html
    assert "const lbl = formatNodeLabel(ele.data('label'));" in viewer_html
    assert "const NODE_MAX_WIDTH = 168;" in viewer_html
    assert "const NODE_MAX_HEIGHT = 64;" in viewer_html
    assert "const NODE_PADDING = 8;" in viewer_html
    assert "'width': NODE_MAX_WIDTH - (NODE_PADDING * 2)" in viewer_html
    assert "'height': NODE_MAX_HEIGHT - (NODE_PADDING * 2)" in viewer_html
    assert "'padding': NODE_PADDING" in viewer_html
    assert "return `${marker}${lbl}`;" in viewer_html
    assert "return `${marker}[${type}]\\n${lbl}`;" not in viewer_html
    assert "'border-color': (ele) => nodeColor(ele.data('entity_type'))" in viewer_html
    assert "const FALLBACK_NODE_COLORS = [" in viewer_html
    assert "hash = ((hash * 31) + char.codePointAt(0)) >>> 0;" in viewer_html
    assert "span.style.color = nodeColor(t);" in viewer_html
    assert "// ── Zoom-aware styling" in viewer_html
    assert "'width':          (NODE_MAX_WIDTH - (NODE_PADDING * 2)) / zc" in viewer_html
    assert "'height':         (NODE_MAX_HEIGHT - (NODE_PADDING * 2)) / zc" in viewer_html
    assert "'padding':        NODE_PADDING / zc" in viewer_html
    assert "'font-size':      Z.nodeFont / zc" in viewer_html
    assert "'text-max-width': Z.textMaxWidth / zc" in viewer_html


def test_viewer_has_native_remote_list_mode() -> None:
    """The native list uses the node and edge endpoints independently of graph mode."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert 'id="graph-tab"' in viewer_html
    assert 'id="list-tab"' in viewer_html
    assert "tabulator" not in viewer_html.lower()
    assert '<table id="node-list" aria-label="Entities">' in viewer_html
    assert 'id="node-list-body"' in viewer_html
    assert "/api/graph/nodes" in viewer_html
    assert "/api/graph/edges" in viewer_html
    assert "function loadList(params)" in viewer_html
    assert "function renderList(rawNodes, params)" in viewer_html
    assert "page: 1, size: params.limit" in viewer_html
    assert "row.addEventListener('dblclick', () => focusNode(entity.id));" in viewer_html
    assert "function renderCachedGraph(params)" in viewer_html
    assert "function renderCachedList(params)" in viewer_html
    assert "renderCachedList(params)" in viewer_html
    assert "cy.resize();" in viewer_html


def test_viewer_requests_unordered_graph_nodes() -> None:
    """Graph mode omits list ordering while retaining ordered list requests."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "function graphRequestParams(params)" in viewer_html
    assert "sort: undefined, sort_dir: undefined, ordered: false" in viewer_html
    assert "const requestParams = graphRequestParams(params);" in viewer_html
    assert "key: viewerScopeKey(requestParams)," in viewer_html


def test_viewer_persists_list_sort_in_url_state() -> None:
    """List ordering is restored from and written to the browser query string."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "sort:        p.get('sort') || DEFAULT_LIST_SORT" in viewer_html
    assert "sort_dir:    p.get('sort_dir') === 'asc' ? 'asc' : DEFAULT_LIST_SORT_DIR" in viewer_html
    assert "if (params.sort && params.sort !== DEFAULT_LIST_SORT) q.set('sort', params.sort);" in viewer_html
    assert "q.set('sort_dir', params.sort_dir);" in viewer_html
    assert "function updateListSortHeaders(params)" in viewer_html
    assert "button.dataset.direction = direction;" in viewer_html
    assert "const DATE_LIST_SORTS = new Set([" in viewer_html
    assert "DATE_LIST_SORTS.has(sort) ? 'desc' : 'asc'" in viewer_html


def test_viewer_has_shared_order_controls() -> None:
    """The viewer exposes the list sort fields and direction at the top right."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert 'id="viewer-order"' in viewer_html
    assert 'id="viewer-order-select"' in viewer_html
    assert 'value="display_name">Name</option>' in viewer_html
    assert 'value="observed_at">Observed</option>' in viewer_html
    assert 'data-sort="observed_at">Observed</button>' in viewer_html
    assert 'value="source_created_at">Source created</option>' in viewer_html
    assert 'value="source_updated_at">Source updated</option>' in viewer_html
    assert 'data-sort="source_created_at">Source created</button>' in viewer_html
    assert 'data-sort="source_updated_at">Source updated</button>' in viewer_html
    assert '<th scope="col">ID</th>' not in viewer_html
    assert 'id="viewer-order-direction"' in viewer_html
    assert '>↓</button>' in viewer_html
    assert "function updateViewerOrderControls(params)" in viewer_html
    assert "viewerOrderDirection.textContent = params.sort_dir === 'asc' ? '↑' : '↓';" in viewer_html
    assert "viewerOrderSelect.addEventListener('change'" in viewer_html
    assert "const sortDir = current.sort_dir === 'asc' ? 'desc' : 'asc';" in viewer_html


def test_viewer_persists_selected_entity_detail_in_url_state() -> None:
    """Opening a detail panel is shareable and is restored from the URL."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "selected_id: p.get('selected_id') || null" in viewer_html
    assert "if (params.selected_id) q.set('selected_id', params.selected_id);" in viewer_html
    assert "selected_id: current.selected_id || undefined" in viewer_html
    assert "function closeDetail(syncUrl = true)" in viewer_html
    assert "detailRequestId += 1;" in viewer_html
    assert "collectParams({ selected_id: null });" in viewer_html
    assert "async function showEntityDetail(entityId, syncUrl = true)" in viewer_html
    assert "collectParams({ selected_id: entityId });" in viewer_html
    assert "if (requestId !== detailRequestId) return;" in viewer_html
    assert "if (_initial.selected_id) showEntityDetail(_initial.selected_id, false);" in viewer_html
    assert "if (state.selected_id) {" in viewer_html
    assert "closeDetail(false);" in viewer_html


def test_viewer_offers_more_results_when_limit_is_reached() -> None:
    """The viewer can increase its active result limit from the result area."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert 'id="more-btn"' in viewer_html
    assert "setMoreAvailable(viewerCache.hasMore);" in viewer_html
    assert "hasMore: Boolean(data.has_more)" in viewer_html
    assert "Math.min(currentLimit * 2, Number(limitSlider.max))" in viewer_html
    assert "refreshGraph();" in viewer_html


def test_viewer_list_end_does_not_activate_more_results() -> None:
    """Only the explicit More results action may increase the active result limit."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "function activateMoreResults()" in viewer_html
    assert "getComputedStyle(moreBtn).display === 'none'" in viewer_html
    assert "function listIsAtEnd()" not in viewer_html
    assert "listView.addEventListener('wheel'" not in viewer_html


def test_viewer_list_rows_match_graph_click_behaviour() -> None:
    """List row clicks open details and double clicks focus the selected node."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "row.dataset.entityId = entity.id;" in viewer_html
    assert "row.addEventListener('click', () => showEntityDetail(entity.id));" in viewer_html
    assert "row.addEventListener('dblclick', () => focusNode(entity.id));" in viewer_html
    assert "function focusNode(entityId)" in viewer_html
    assert "refreshGraph({ node_id: entityId });" in viewer_html
    assert "focusNode(node.data('id'));" in viewer_html


def test_viewer_supports_list_navigation_and_shortcut_help() -> None:
    """The viewer exposes j/k list navigation and a right-pane shortcut guide."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert 'id="shortcut-help-btn"' in viewer_html
    assert '<div id="sidebar-footer">' in viewer_html
    assert 'aria-label="Keyboard shortcuts"' in viewer_html
    assert 'id="shortcut-body" hidden' in viewer_html
    assert '<kbd>j</kbd>' in viewer_html
    assert '<kbd>k</kbd>' in viewer_html
    assert "function moveListSelection(delta)" in viewer_html
    assert "showEntityDetail(nextId);" in viewer_html
    assert "key === 'j' || key === 'k'" in viewer_html
    assert "function toggleShortcutHelp()" in viewer_html
    assert "setShortcutHelpMode(true);" in viewer_html
    assert "setShortcutHelpMode(false);" in viewer_html


def test_viewer_truncates_list_names_at_100_characters() -> None:
    """List names stay within a fixed, readable character limit."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "function truncateListName(value)" in viewer_html
    assert "const maxLength = 100;" in viewer_html
    assert "value.slice(0, maxLength - 3)" in viewer_html
    assert "addListCell(row, truncateListName(name));" in viewer_html


def test_viewer_spinner_does_not_block_list_double_clicks() -> None:
    """The detail-loading indicator must not intercept a row's second click."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "#spinner {" in viewer_html
    assert "pointer-events: none;" in viewer_html


def test_viewer_omits_redundant_all_type_filter() -> None:
    """The default all-types view should use the database's global sort index."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()
    assert "function activeEntityTypes()" in viewer_html
    assert "return allTypesSelected ? undefined : selected;" in viewer_html
    assert "entity_type: activeEntityTypes()," in viewer_html


def test_viewer_option_clicking_sole_entity_type_restores_all_types() -> None:
    """Option-clicking the only selected type returns to the all-types view."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "const wasSoleSelected = !cb.checked && typeFilters().length === 0;" in viewer_html
    assert "c.checked = wasSoleSelected || c === cb;" in viewer_html


def test_viewer_has_sidebar_form_sections_and_submit_controls() -> None:
    """The sidebar groups its controls and exposes form submission actions."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert '<form id="sidebar-form">' in viewer_html
    assert viewer_html.index('id="search-heading"') < viewer_html.index('id="focus-heading"')
    assert viewer_html.index('id="focus-heading"') < viewer_html.index('id="filter-heading"')
    assert 'id="search-clear-btn" type="button"' in viewer_html
    assert 'aria-label="Clear search"' in viewer_html
    assert 'id="search-go-btn" type="submit"' in viewer_html
    assert 'id="focus-clear-btn" type="button"' in viewer_html
    assert 'aria-label="Clear focus node"' in viewer_html
    assert 'id="lookup-btn" type="submit"' in viewer_html
    assert viewer_html.index('id="lookup-input"') < viewer_html.index('id="depth-input"')
    assert 'class="input-shell"' in viewer_html
    assert 'id="reset-btn"' not in viewer_html
    assert 'id="refresh-btn"' not in viewer_html


def test_viewer_focus_node_placeholder_describes_supported_identifiers() -> None:
    """The Focus Node hint describes each connector-neutral lookup format."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert 'placeholder="Entity UUID, prefix, or platform reference"' in viewer_html


def test_viewer_sidebar_uses_native_form_submission() -> None:
    """Enter and action controls share the sidebar form submission path."""
    viewer_html = Path("agentgraph/server/static/viewer.html").read_text()

    assert "sidebarForm.addEventListener('submit'" in viewer_html
    assert "event.preventDefault();" in viewer_html
    assert "sidebarForm.requestSubmit();" in viewer_html
    assert "function wireTextInputSubmission(input, submit)" not in viewer_html
    assert "searchInput.addEventListener('input'" not in viewer_html
    assert "lookupInput.addEventListener('keydown'" not in viewer_html


# ---------------------------------------------------------------------------
# Rule: focal node always shown regardless of filters
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_focal_node_shown_when_type_filter_excludes_it() -> None:
    """node_id entity is always in results even if entity_type filter would exclude it."""
    from agentgraph.server.browse_api import cli_browse

    focal = _entity(entity_type="Person", platform="slack")
    neighbour = _entity(entity_type="Document")
    edge = _edge(focal["id"], neighbour["id"])

    backend = _mock_backend(
        get_entity_by_id=AsyncMock(return_value=focal),
        traverse_graph=AsyncMock(return_value={"nodes": [focal, neighbour], "edges": [edge]}),
        list_entities=AsyncMock(return_value=[]),
    )
    set_backend(backend)

    with patch("agentgraph.server.browse_api.get_entity", AsyncMock(return_value=focal)), \
         patch("agentgraph.server.browse_api.traverse_graph", AsyncMock(return_value={"nodes": [focal, neighbour], "edges": [edge]})), \
         patch("agentgraph.server.browse_api.search_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.list_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_edges_for_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_entities_by_ids", AsyncMock(return_value=[])):
        result = await cli_browse(
            node_id=focal["id"],
            entity_type=["Document"],  # excludes Person
            search=None,
            platform=None,
            since=None,
            depth=2,
            limit=50,
        )

    node_ids = {n["id"] for n in result["nodes"]}
    assert focal["id"] in node_ids, "Focal node must always be present"


# ---------------------------------------------------------------------------
# Rule: only matching nodes shown (except focal)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_entity_type_filter_excludes_non_focal_nodes() -> None:
    """Non-focal nodes that don't match entity_type filter are excluded."""
    from agentgraph.server.browse_api import cli_browse

    focal = _entity(entity_type="Person", platform="slack")
    doc = _entity(entity_type="Document")
    msg = _entity(entity_type="Message")
    edge_doc = _edge(focal["id"], doc["id"])
    edge_msg = _edge(focal["id"], msg["id"])

    traverse_result = {"nodes": [focal, doc, msg], "edges": [edge_doc, edge_msg]}

    with patch("agentgraph.server.browse_api.get_entity", AsyncMock(return_value=focal)), \
         patch("agentgraph.server.browse_api.traverse_graph", AsyncMock(return_value=traverse_result)), \
         patch("agentgraph.server.browse_api.search_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.list_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_edges_for_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_entities_by_ids", AsyncMock(return_value=[])):
        result = await cli_browse(
            node_id=focal["id"],
            entity_type=["Person", "Document"],  # excludes Message
            search=None,
            platform=None,
            since=None,
            depth=2,
            limit=50,
        )

    node_ids = {n["id"] for n in result["nodes"]}
    assert msg["id"] not in node_ids, "Message node should be filtered out"
    assert doc["id"] in node_ids
    assert focal["id"] in node_ids


# ---------------------------------------------------------------------------
# Rule: depth only applies when node_id provided
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_depth_ignored_without_node_id() -> None:
    """When no node_id is given, traverse_graph is never called."""
    from agentgraph.server.browse_api import cli_browse

    mock_traverse = AsyncMock(return_value={"nodes": [], "edges": []})

    with patch("agentgraph.server.browse_api.get_entity", AsyncMock(return_value=None)), \
         patch("agentgraph.server.browse_api.traverse_graph", mock_traverse), \
         patch("agentgraph.server.browse_api.search_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.list_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_edges_for_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_entities_by_ids", AsyncMock(return_value=[])):
        await cli_browse(
            node_id=None,
            entity_type=[],
            search=None,
            platform=None,
            since=None,
            depth=4,
            limit=50,
        )

    mock_traverse.assert_not_called()


@pytest.mark.asyncio
async def test_traverse_called_with_depth_when_node_id_given() -> None:
    """When node_id is given, traverse_graph is called with the specified depth."""
    from agentgraph.server.browse_api import cli_browse

    focal = _entity(entity_type="Person")
    mock_traverse = AsyncMock(return_value={"nodes": [focal], "edges": []})

    with patch("agentgraph.server.browse_api.get_entity", AsyncMock(return_value=focal)), \
         patch("agentgraph.server.browse_api.traverse_graph", mock_traverse), \
         patch("agentgraph.server.browse_api.search_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.list_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_edges_for_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_entities_by_ids", AsyncMock(return_value=[])):
        await cli_browse(
            node_id=focal["id"],
            entity_type=[],
            search=None,
            platform=None,
            since=None,
            depth=3,
            limit=50,
        )

    mock_traverse.assert_called_once_with(
        focal["id"], max_depth=3, content_limit=300
    )


@pytest.mark.asyncio
async def test_browse_allows_zero_depth_for_a_node_only_view() -> None:
    """A focused browse request passes depth zero through to traversal."""
    from agentgraph.server.browse_api import cli_browse

    focal = _entity(entity_type="Person")
    mock_traverse = AsyncMock(return_value={"nodes": [focal], "edges": []})

    with patch("agentgraph.server.browse_api.get_entity", AsyncMock(return_value=focal)), \
         patch("agentgraph.server.browse_api.traverse_graph", mock_traverse), \
         patch("agentgraph.server.browse_api.search_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.list_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_edges_for_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_entities_by_ids", AsyncMock(return_value=[])):
        result = await cli_browse(
            node_id=focal["id"],
            entity_type=[],
            search=None,
            platform=None,
            since=None,
            depth=0,
            limit=50,
        )

    assert [node["id"] for node in result["nodes"]] == [focal["id"]]
    mock_traverse.assert_called_once_with(
        focal["id"], max_depth=0, content_limit=300
    )


# ---------------------------------------------------------------------------
# Reachability prune
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reachability_prune_removes_disconnected_nodes() -> None:
    """Nodes not reachable from focal through the filtered edge set are pruned."""
    from agentgraph.server.browse_api import cli_browse

    focal = _entity(entity_type="Person")
    connected = _entity(entity_type="Message")
    orphan = _entity(entity_type="Message")
    edge = _edge(focal["id"], connected["id"])
    # orphan has no edge connecting it to focal in this filtered view

    traverse_result = {"nodes": [focal, connected, orphan], "edges": [edge]}

    with patch("agentgraph.server.browse_api.get_entity", AsyncMock(return_value=focal)), \
         patch("agentgraph.server.browse_api.traverse_graph", AsyncMock(return_value=traverse_result)), \
         patch("agentgraph.server.browse_api.search_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.list_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_edges_for_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_entities_by_ids", AsyncMock(return_value=[])):
        result = await cli_browse(
            node_id=focal["id"],
            entity_type=[],
            search=None,
            platform=None,
            since=None,
            depth=2,
            limit=50,
        )

    node_ids = {n["id"] for n in result["nodes"]}
    assert orphan["id"] not in node_ids, "Disconnected node should be pruned"
    assert connected["id"] in node_ids
    assert focal["id"] in node_ids


# ---------------------------------------------------------------------------
# No node_id — list_entities path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_node_id_returns_list_entities() -> None:
    """Without node_id or search, list_entities is used."""
    from agentgraph.server.browse_api import cli_browse

    entities = [_entity(), _entity()]
    mock_list = AsyncMock(return_value=entities)

    with patch("agentgraph.server.browse_api.get_entity", AsyncMock(return_value=None)), \
         patch("agentgraph.server.browse_api.traverse_graph", AsyncMock(return_value={"nodes": [], "edges": []})), \
         patch("agentgraph.server.browse_api.search_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.list_entities", mock_list), \
         patch("agentgraph.server.browse_api.get_edges_for_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_entities_by_ids", AsyncMock(return_value=[])):
        result = await cli_browse(
            node_id=None,
            entity_type=[],
            search=None,
            platform=None,
            since=None,
            depth=2,
            limit=50,
        )

    assert len(result["nodes"]) == 2
    mock_list.assert_called_once()


# ---------------------------------------------------------------------------
# Search + node_id intersection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_with_node_id_intersects_neighbourhood() -> None:
    """Search results are intersected with the neighbourhood when node_id is given."""
    from agentgraph.server.browse_api import cli_browse

    focal = _entity(entity_type="Person")
    in_neighbourhood = _entity(entity_type="Document", title="Match")
    outside_neighbourhood = _entity(entity_type="Document", title="Match too")
    edge = _edge(focal["id"], in_neighbourhood["id"])

    traverse_result = {
        "nodes": [focal, in_neighbourhood],
        "edges": [edge],
    }
    search_results = [in_neighbourhood, outside_neighbourhood]

    with patch("agentgraph.server.browse_api.get_entity", AsyncMock(return_value=focal)), \
         patch("agentgraph.server.browse_api.traverse_graph", AsyncMock(return_value=traverse_result)), \
         patch("agentgraph.server.browse_api.search_entities", AsyncMock(return_value=search_results)), \
         patch("agentgraph.server.browse_api.list_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_edges_for_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_entities_by_ids", AsyncMock(return_value=[])):
        result = await cli_browse(
            node_id=focal["id"],
            entity_type=[],
            search="Match",
            platform=None,
            since=None,
            depth=2,
            limit=50,
        )

    node_ids = {n["id"] for n in result["nodes"]}
    assert outside_neighbourhood["id"] not in node_ids
    assert in_neighbourhood["id"] in node_ids
    assert focal["id"] in node_ids  # focal always present


@pytest.mark.parametrize("since", ["24h", "2025-01-02T00:00:00Z"])
@pytest.mark.asyncio
async def test_search_pushes_platform_and_since_into_the_query(since: str) -> None:
    """Search takes the same filters as entity listing, applied as SQL predicates.

    They used to be post-filtered in Python here because the ranked path could not
    express them; the merged search can, so the viewer just forwards them.
    """
    from agentgraph.server.browse_api import cli_browse

    mock_search = AsyncMock(return_value=[])
    with patch("agentgraph.server.browse_api.search_entities", mock_search), patch(
        "agentgraph.server.browse_api.get_edges_for_entities",
        AsyncMock(return_value=[]),
    ):
        await cli_browse(
            node_id=None,
            entity_type=[],
            search="match",
            platform="slack",
            since=since,
            depth=2,
            limit=50,
        )

    mock_search.assert_awaited_once_with(
        "match",
        entity_types=None,
        limit=51,
        min_score=0.0,
        platform="slack",
        since=since,
        content_limit=300,
    )


@pytest.mark.parametrize(
    ("since", "included_updated_at", "excluded_updated_at"),
    [
        (
            "24h",
            (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            (datetime.now(UTC) - timedelta(hours=25)).isoformat(),
        ),
        (
            "2025-01-02T00:00:00Z",
            "2025-01-02T00:00:01Z",
            "2025-01-01T23:59:59Z",
        ),
    ],
)
@pytest.mark.asyncio
async def test_neighbourhood_filters_since_using_parsed_timestamps(
    since: str,
    included_updated_at: str,
    excluded_updated_at: str,
) -> None:
    """The neighbourhood path has no predicate hook, so it still filters in Python."""
    from agentgraph.server.browse_api import cli_browse

    focal = _entity(title="Focal")
    included = _entity(title="Recent match")
    included["updated_at"] = included_updated_at
    excluded = _entity(title="Old match")
    excluded["updated_at"] = excluded_updated_at

    traverse_result = {
        "nodes": [focal, included, excluded],
        "edges": [_edge(focal["id"], included["id"]), _edge(focal["id"], excluded["id"])],
    }

    with patch(
        "agentgraph.server.browse_api.get_entity", AsyncMock(return_value=focal)
    ), patch(
        "agentgraph.server.browse_api.traverse_graph",
        AsyncMock(return_value=traverse_result),
    ), patch(
        "agentgraph.server.browse_api.get_edges_for_entities",
        AsyncMock(return_value=[]),
    ), patch(
        "agentgraph.server.browse_api.get_entities_by_ids", AsyncMock(return_value=[])
    ):
        result = await cli_browse(
            node_id=focal["id"],
            entity_type=[],
            search=None,
            platform=None,
            since=since,
            depth=2,
            limit=50,
        )

    node_ids = {node["id"] for node in result["nodes"]}
    assert included["id"] in node_ids
    assert excluded["id"] not in node_ids


@pytest.mark.asyncio
async def test_browse_nodes_include_display_name_from_title() -> None:
    """Viewer nodes include a human label derived from title."""
    from agentgraph.server.browse_api import cli_browse

    thread = _entity(
        entity_type="Email",
        platform="gmail",
        title="Quarterly planning sync with vendor and finance",
    )

    with patch("agentgraph.server.browse_api.get_entity", AsyncMock(return_value=None)), \
         patch("agentgraph.server.browse_api.traverse_graph", AsyncMock(return_value={"nodes": [], "edges": []})), \
         patch("agentgraph.server.browse_api.search_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.list_entities", AsyncMock(return_value=[thread])), \
         patch("agentgraph.server.browse_api.get_edges_for_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_entities_by_ids", AsyncMock(return_value=[])):
        result = await cli_browse(
            node_id=None,
            entity_type=[],
            search=None,
            platform=None,
            since=None,
            depth=2,
            limit=50,
        )

    assert result["nodes"][0]["display_name"] == thread["title"]


@pytest.mark.asyncio
async def test_browse_nodes_fall_back_to_content_for_display_name() -> None:
    """Viewer nodes fall back to normalised content when title is missing."""
    from agentgraph.server.browse_api import cli_browse

    message = _entity(entity_type="Message", title="")
    message["content"] = "  First line\nwith extra   spacing  "

    with patch("agentgraph.server.browse_api.get_entity", AsyncMock(return_value=None)), \
         patch("agentgraph.server.browse_api.traverse_graph", AsyncMock(return_value={"nodes": [], "edges": []})), \
         patch("agentgraph.server.browse_api.search_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.list_entities", AsyncMock(return_value=[message])), \
         patch("agentgraph.server.browse_api.get_edges_for_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_entities_by_ids", AsyncMock(return_value=[])):
        result = await cli_browse(
            node_id=None,
            entity_type=[],
            search=None,
            platform=None,
            since=None,
            depth=2,
            limit=50,
        )

    assert result["nodes"][0]["display_name"] == "First line with extra spacing"
    assert result["nodes"][0]["viewer_label"] == "First line with extra spacing"


@pytest.mark.asyncio
async def test_browse_message_nodes_include_truncated_viewer_label() -> None:
    """Message nodes expose a shortened label for graph rendering."""
    from agentgraph.server.browse_api import cli_browse

    message = _entity(entity_type="Message", title="")
    message["content"] = (
        "This is a long message body that should be truncated in the viewer label "
        "while keeping the full display name available in the detail view."
    )

    with patch("agentgraph.server.browse_api.get_entity", AsyncMock(return_value=None)), \
         patch("agentgraph.server.browse_api.traverse_graph", AsyncMock(return_value={"nodes": [], "edges": []})), \
         patch("agentgraph.server.browse_api.search_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.list_entities", AsyncMock(return_value=[message])), \
         patch("agentgraph.server.browse_api.get_edges_for_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_entities_by_ids", AsyncMock(return_value=[])):
        result = await cli_browse(
            node_id=None,
            entity_type=[],
            search=None,
            platform=None,
            since=None,
            depth=2,
            limit=50,
        )

    node = result["nodes"][0]
    assert node["display_name"].endswith("detail view.")
    assert node["viewer_label"].endswith("…")
    assert len(node["viewer_label"]) <= 80


@pytest.mark.asyncio
async def test_browse_nodes_preserve_bookmarked_flag() -> None:
    """Viewer nodes expose bookmark state for graph styling and detail actions."""
    from agentgraph.server.browse_api import cli_browse

    document = _entity(entity_type="Document", title="Pinned Doc")
    document["bookmarked"] = True

    with patch("agentgraph.server.browse_api.get_entity", AsyncMock(return_value=None)), \
         patch("agentgraph.server.browse_api.traverse_graph", AsyncMock(return_value={"nodes": [], "edges": []})), \
         patch("agentgraph.server.browse_api.search_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.list_entities", AsyncMock(return_value=[document])), \
         patch("agentgraph.server.browse_api.get_edges_for_entities", AsyncMock(return_value=[])), \
         patch("agentgraph.server.browse_api.get_entities_by_ids", AsyncMock(return_value=[])):
        result = await cli_browse(
            node_id=None,
            entity_type=[],
            search=None,
            platform=None,
            since=None,
            depth=2,
            limit=50,
        )

    assert result["nodes"][0]["bookmarked"] is True


# ---------------------------------------------------------------------------
# 404 when node_id not found
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_browse_404_when_node_id_not_found() -> None:
    """Returns 404 when the specified node_id does not exist."""
    from fastapi import HTTPException

    from agentgraph.server.browse_api import cli_browse

    with patch("agentgraph.server.browse_api.get_entity", AsyncMock(return_value=None)), \
         pytest.raises(HTTPException) as exc_info:
        await cli_browse(
            node_id="nonexistent-id",
            entity_type=[],
            search=None,
            platform=None,
            since=None,
            depth=2,
            limit=50,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_fetch_entity_returns_bad_request_for_connector_runtime_error() -> None:
    """A re-fetch by UUID must expose connector setup errors without a 500.

    Connectors signal missing credentials with RuntimeError, so this goes through the
    HTTP layer: the mapping lives in GraphAPIRoute, not in the handler function.
    """
    import httpx

    from agentgraph.server.app import app

    message = "Google credentials not configured. Run: agentgraph auth google"
    entity_id = "01139ce4-550b-4086-8c4c-8f5bae045281"
    with patch(
        "agentgraph.graph.fetch.fetch_entity_by_id",
        new=AsyncMock(side_effect=RuntimeError(message)),
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
            response = await client.post(f"/api/entities/{entity_id}/fetch")

    assert response.status_code == 400
    # The class name travels with the message so clients can re-raise the same type.
    assert response.json()["detail"] == {"message": message, "error_type": "RuntimeError"}


@pytest.mark.asyncio
async def test_browse_nodes_returns_paginated_node_page() -> None:
    """The list endpoint exposes Tabulator-compatible pagination metadata."""
    from agentgraph.server.browse_api import browse_nodes

    entities = [_entity(title="First"), _entity(title="Second")]
    mock_page = AsyncMock(return_value=(entities, 7))

    with patch("agentgraph.server.browse_api.list_entities_page", mock_page):
        result = await browse_nodes(
            search=None,
            entity_type=[],
            platform=None,
            since=None,
            node_id=None,
            depth=2,
            limit=100,
            page=2,
            size=2,
            sort="updated_at",
            sort_dir="asc",
        )

    assert [node["display_name"] for node in result["data"]] == ["First", "Second"]
    assert result["total"] == 7
    assert result["last_page"] == 4
    assert result["has_more"] is False
    mock_page.assert_awaited_once_with(
        entity_types=None,
        platform=None,
        since=None,
        limit=2,
        offset=2,
        order_by="updated_at",
        order_dir="asc",
        content_limit=300,
    )


@pytest.mark.asyncio
async def test_browse_nodes_can_skip_ordering_for_graph_view() -> None:
    """Graph node pages tell storage to omit ordering without changing list defaults."""
    from agentgraph.server.browse_api import browse_nodes

    mock_page = AsyncMock(return_value=([_entity(title="Node")], 1))
    with patch("agentgraph.server.browse_api.list_entities_page", mock_page):
        await browse_nodes(
            search=None,
            entity_type=["Message", "Email"],
            platform=None,
            since=None,
            node_id=None,
            depth=2,
            limit=50,
            page=1,
            size=50,
            sort="observed_at",
            sort_dir="desc",
            ordered=False,
        )

    mock_page.assert_awaited_once_with(
        entity_types=["Message", "Email"],
        platform=None,
        since=None,
        limit=50,
        offset=0,
        order_by=None,
        order_dir="desc",
        content_limit=300,
    )


@pytest.mark.asyncio
async def test_browse_nodes_reports_results_beyond_limit() -> None:
    """The list endpoint distinguishes the active cap from the full result count."""
    from agentgraph.server.browse_api import browse_nodes

    entities = [_entity(title="First"), _entity(title="Second")]

    with patch(
        "agentgraph.server.browse_api.list_entities_page",
        AsyncMock(return_value=(entities, 7)),
    ):
        result = await browse_nodes(
            search=None,
            entity_type=[],
            platform=None,
            since=None,
            node_id=None,
            depth=2,
            limit=2,
            page=1,
            size=2,
            sort="updated_at",
            sort_dir="asc",
        )

    assert result["total"] == 2
    assert result["last_page"] == 1
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_browse_nodes_checks_one_extra_search_result() -> None:
    """Search overflow is detected without returning more than the active limit."""
    from agentgraph.server.browse_api import browse_nodes

    entities = [_entity(title=f"Result {index}") for index in range(3)]
    mock_search = AsyncMock(return_value=entities)

    with patch("agentgraph.server.browse_api.search_entities", mock_search), patch(
        "agentgraph.server.browse_api.get_edges_for_entities", AsyncMock(return_value=[])
    ):
        result = await browse_nodes(
            search="result",
            entity_type=[],
            platform=None,
            since=None,
            node_id=None,
            depth=2,
            limit=2,
            page=1,
            size=2,
            sort="updated_at",
            sort_dir="asc",
        )

    assert len(result["data"]) == 2
    assert result["total"] == 2
    assert result["has_more"] is True
    mock_search.assert_awaited_once_with(
        "result",
        entity_types=None,
        limit=3,
        min_score=0.0,
        platform=None,
        since=None,
        content_limit=300,
    )


@pytest.mark.asyncio
async def test_browse_nodes_sorts_search_results_by_display_name() -> None:
    """Sorting a list header also orders search-backed result sets."""
    from agentgraph.server.browse_api import browse_nodes

    zulu = _entity(title="Zulu")
    alpha = _entity(title="Alpha")
    with patch("agentgraph.server.browse_api.search_entities", AsyncMock(return_value=[zulu, alpha])), patch(
        "agentgraph.server.browse_api.get_edges_for_entities", AsyncMock(return_value=[])
    ):
        result = await browse_nodes(
            search="letter",
            entity_type=[],
            platform=None,
            since=None,
            node_id=None,
            depth=2,
            limit=50,
            page=1,
            size=50,
            sort="display_name",
            sort_dir="asc",
        )

    assert [node["display_name"] for node in result["data"]] == ["Alpha", "Zulu"]


@pytest.mark.asyncio
async def test_browse_edges_accepts_comma_separated_node_ids() -> None:
    """Edge lookup deduplicates node IDs and omits edges outside the visible set."""
    from agentgraph.server.browse_api import browse_edges

    first = _entity()
    second = _entity()
    outside = _entity()
    visible_edge = _edge(first["id"], second["id"])
    hidden_edge = _edge(first["id"], outside["id"])
    mock_edges = AsyncMock(return_value=[visible_edge, hidden_edge])

    with patch("agentgraph.server.browse_api.get_edges_for_entities", mock_edges):
        result = await browse_edges(f" {first['id']}, {second['id']}, {first['id']} ")

    assert result == {"edges": [visible_edge]}
    mock_edges.assert_awaited_once_with([first["id"], second["id"]])
