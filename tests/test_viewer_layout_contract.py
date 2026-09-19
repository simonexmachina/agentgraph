"""Browser-level contracts for the Cytoscape viewer layout."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Browser, Page, ViewportSize, expect, sync_playwright

pytestmark = pytest.mark.browser


class _ViewerFixtureServer(ThreadingHTTPServer):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    viewer_html: bytes
    meta_delay_seconds: float
    entity_types: list[str]


class _ViewerFixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        server = cast(_ViewerFixtureServer, self.server)
        path = urlsplit(self.path).path
        if path == "/viewer":
            self._send(200, "text/html; charset=utf-8", server.viewer_html)
        elif path == "/api/meta":
            time.sleep(server.meta_delay_seconds)
            self._send(
                200,
                "application/json",
                {"entity_types": server.entity_types, "platforms": []},
            )
        elif path.startswith("/api/entities/") and path.endswith("/edges"):
            entity_id = path.removeprefix("/api/entities/").removesuffix("/edges")
            entity = next((node for node in server.nodes if node["id"] == entity_id), None)
            if entity is None:
                self._send(404, "application/json", {"detail": "not found"})
            else:
                self._send(200, "application/json", {"entity": entity, "edges": server.edges})
        elif path.startswith("/api/entities/"):
            entity_id = path.removeprefix("/api/entities/")
            entity = next((node for node in server.nodes if node["id"] == entity_id), None)
            if entity:
                self._send(200, "application/json", entity)
            else:
                self._send(404, "application/json", {"detail": "not found"})
        elif path == "/api/graph/nodes":
            self._send(200, "application/json", {"data": server.nodes, "has_more": False})
        elif path == "/api/graph/edges":
            self._send(200, "application/json", {"edges": server.edges})
        else:
            self._send(404, "application/json", {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        server = cast(_ViewerFixtureServer, self.server)
        request = urlsplit(self.path)
        if request.path != "/api/persons/unify":
            self._send(404, "application/json", {"detail": "not found"})
            return

        params = parse_qs(request.query)
        primary_id = params.get("canonical_id", [""])[0]
        duplicate_ids = params.get("duplicate_ids", [])
        primary = next((node for node in server.nodes if node["id"] == primary_id), None)
        duplicates = [node for node in server.nodes if node["id"] in duplicate_ids]
        if primary is None or len(duplicates) != len(duplicate_ids):
            self._send(400, "application/json", {"detail": "Person not found"})
            return

        metadata = dict(primary.get("metadata", {}))
        metadata["merged_people"] = [
            {
                "id": duplicate["id"],
                "title": duplicate.get("title", ""),
                "platform_entity_id": duplicate.get("platform_entity_id", ""),
            }
            for duplicate in duplicates
        ]
        primary["metadata"] = metadata
        server.nodes[:] = [node for node in server.nodes if node["id"] not in duplicate_ids]
        self._send(200, "application/json", {"primary": primary, "merged_count": len(duplicates)})

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(
        self,
        status: int,
        content_type: str,
        body: bytes | dict[str, Any] | list[dict[str, Any]],
    ) -> None:
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextmanager
def _serve_viewer(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    meta_delay_seconds: float = 0,
    entity_types: list[str] | None = None,
) -> Iterator[str]:
    server = _ViewerFixtureServer(("127.0.0.1", 0), _ViewerFixtureHandler)
    server.nodes = nodes
    server.edges = edges
    server.meta_delay_seconds = meta_delay_seconds
    server.entity_types = entity_types or ["Document"]
    server.viewer_html = Path("agentgraph/server/static/viewer.html").read_bytes()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/viewer"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.fixture
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    yield page
    page.close()


def _node(index: int, label: str) -> dict[str, Any]:
    return {
        "id": f"node-{index}",
        "entity_type": "Document",
        "platform": "test",
        "platform_entity_id": str(index),
        "viewer_label": label,
        "content": "",
    }


def _person(index: int, name: str) -> dict[str, Any]:
    return {
        "id": f"person-{index}",
        "entity_type": "Person",
        "platform": "test",
        "platform_entity_id": f"person-ref-{index}",
        "title": name,
        "content": "",
        "metadata": {},
    }


def _wait_for_graph(page: Page, url: str, node_count: int) -> None:
    page.goto(url)
    page.wait_for_function(
        "count => window.__agentGraphViewer.cy.nodes().length === count",
        arg=node_count,
    )
    page.wait_for_timeout(250)


def _layout_metrics(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const cy = window.__agentGraphViewer.cy;
          const nodes = cy.nodes().toArray();
          const boxes = nodes.map(node => ({ id: node.id(), box: node.renderedBoundingBox({ includeLabels: true, includeOverlays: false }) }));
          let overlaps = 0;
          let minimumGap = Infinity;
          let totalNodeArea = 0;
          let x1 = Infinity;
          let y1 = Infinity;
          let x2 = -Infinity;
          let y2 = -Infinity;
          for (const { box } of boxes) {
            totalNodeArea += box.w * box.h;
            x1 = Math.min(x1, box.x1);
            y1 = Math.min(y1, box.y1);
            x2 = Math.max(x2, box.x2);
            y2 = Math.max(y2, box.y2);
          }
          for (let i = 0; i < boxes.length; i += 1) {
            for (let j = i + 1; j < boxes.length; j += 1) {
              const a = boxes[i].box;
              const b = boxes[j].box;
              if (a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1) overlaps += 1;
              const horizontalGap = Math.max(a.x1 - b.x2, b.x1 - a.x2, 0);
              const verticalGap = Math.max(a.y1 - b.y2, b.y1 - a.y2, 0);
              minimumGap = Math.min(minimumGap, Math.hypot(horizontalGap, verticalGap));
            }
          }
          const graphArea = Math.max(x2 - x1, 1) * Math.max(y2 - y1, 1);
          return {
            overlaps,
            minimumGap,
            utilization: totalNodeArea / graphArea,
            insideViewport: boxes.every(({ box }) => (
              box.x1 >= 0 && box.y1 >= 0 && box.x2 <= cy.width() && box.y2 <= cy.height()
            )),
            boxes,
          };
        }"""
    )


def test_edge_free_graph_uses_a_non_overlapping_grid(page: Page) -> None:
    nodes = [_node(index, f"Isolated document {index}") for index in range(50)]
    with _serve_viewer(nodes, []) as url:
        _wait_for_graph(page, url, len(nodes))
        metrics = _layout_metrics(page)

    assert metrics["overlaps"] == 0
    assert metrics["minimumGap"] >= 16


def test_graph_load_does_not_wait_indefinitely_for_metadata(page: Page) -> None:
    nodes = [_node(1, "Available before metadata")]
    with _serve_viewer(nodes, [], meta_delay_seconds=2) as url:
        started = time.monotonic()
        _wait_for_graph(page, url, len(nodes))
        elapsed = time.monotonic() - started

    assert elapsed < 1.5


def test_viewer_adds_connector_entity_type_filter_with_stable_color(page: Page) -> None:
    with _serve_viewer([], [], entity_types=["Document", "Project"]) as url:
        page.goto(url)
        project_filter = page.locator("label", has_text="Project")
        expect(project_filter).to_be_visible()
        color = project_filter.locator("span").evaluate(
            "element => getComputedStyle(element).color"
        )

    assert color != "rgb(148, 163, 184)"


def test_viewer_uses_all_time_twenty_node_default_state(page: Page) -> None:
    with _serve_viewer([], []) as url:
        page.goto(url)

        expect(page).to_have_url(f"{url}?limit=20")
        expect(page.locator("#since-filter")).to_have_value("")
        expect(page.locator("#limit-slider")).to_have_value("20")
        expect(page.locator("#limit-display")).to_have_text("20")


def test_viewer_preserves_explicit_all_time_state(page: Page) -> None:
    with _serve_viewer([], []) as url:
        page.goto(f"{url}?since=&limit=50")

        expect(page).to_have_url(f"{url}?limit=50")
        expect(page.locator("#since-filter")).to_have_value("")
        expect(page.locator("#limit-slider")).to_have_value("50")


def test_sidebar_form_submits_and_clears_search_and_focus(page: Page) -> None:
    nodes = [_node(1, "Focus target")]
    with _serve_viewer(nodes, []) as url:
        page.goto(url)
        expect(page).to_have_url(f"{url}?limit=20")
        expect(page.locator("#search-clear-btn")).not_to_be_visible()
        expect(page.locator("#focus-clear-btn")).not_to_be_visible()

        search_input = page.locator("#search-input")
        search_input.fill("roadmap")
        expect(page.locator("#search-clear-btn")).to_be_visible()
        search_input.press("Enter")
        expect(page).to_have_url(f"{url}?search=roadmap&limit=20")

        search_input.fill("planning")
        page.locator("#search-go-btn").click()
        expect(page).to_have_url(f"{url}?search=planning&limit=20")

        page.locator("#search-clear-btn").click()
        expect(page).to_have_url(f"{url}?limit=20")

        page.locator("#lookup-input").fill("node-1")
        expect(page.locator("#focus-clear-btn")).to_be_visible()
        page.locator("#lookup-btn").click()
        expect(page).to_have_url(f"{url}?limit=20&node_id=node-1&selected_id=node-1")

        page.locator("#focus-clear-btn").click()
        expect(page.locator("#lookup-input")).to_have_value("")
        expect(page.locator("#focus-clear-btn")).not_to_be_visible()
        expect(page.locator("#depth-input")).to_have_value("1")
        expect(page).to_have_url(f"{url}?limit=20&selected_id=node-1")


def test_list_highlights_selected_entity_and_updates_on_row_click(page: Page) -> None:
    nodes = [_node(1, "First document"), _node(2, "Second document")]
    with _serve_viewer(nodes, []) as url:
        page.goto(f"{url}?view=list&selected_id=node-1")
        rows = page.locator("#node-list-body tr")
        expect(rows).to_have_count(2)

        first_row = rows.nth(0)
        second_row = rows.nth(1)
        expect(first_row).to_have_attribute("aria-selected", "true")
        expect(second_row).to_have_attribute("aria-selected", "false")
        assert first_row.evaluate("row => getComputedStyle(row).backgroundColor") == "rgba(96, 165, 250, 0.18)"

        second_row.click()
        expect(first_row).to_have_attribute("aria-selected", "false")
        expect(second_row).to_have_attribute("aria-selected", "true")


def test_list_displays_and_sorts_by_source_timestamps_without_an_id_column(page: Page) -> None:
    node = _node(1, "Source timestamped document")
    node.update(
        {
            "source_created_at": "2026-05-30T03:00:00Z",
            "source_updated_at": "2026-05-31T04:00:00Z",
        }
    )
    with _serve_viewer([node], []) as url:
        page.goto(f"{url}?view=list")

        headers = page.locator("#node-list thead th")
        expect(headers).to_contain_text(["Source created", "Source updated"])
        expect(headers.filter(has_text="ID")).to_have_count(0)

        row = page.locator("#node-list-body tr")
        expect(row).to_have_count(1)
        expect(row).to_contain_text("5/30/2026")
        expect(row).to_contain_text("5/31/2026")

        page.locator('#viewer-order-select').select_option("source_created_at")
        expect(page).to_have_url(f"{url}?limit=20&view=list&sort=source_created_at")


def test_first_click_on_date_list_header_sorts_descending(page: Page) -> None:
    nodes = [_node(1, "Timestamped document")]
    with _serve_viewer(nodes, []) as url:
        page.goto(f"{url}?view=list")

        page.locator('[data-sort="created_at"]').click()
        expect(page).to_have_url(f"{url}?limit=20&view=list&sort=created_at")

        page.locator('[data-sort="created_at"]').click()
        expect(page).to_have_url(
            f"{url}?limit=20&view=list&sort=created_at&sort_dir=asc"
        )


def test_connected_long_label_graph_keeps_nodes_separate(page: Page) -> None:
    nodes = [
        _node(index, f"Long RSS article title {index}: " + "A detailed discussion of agent systems " * 3)
        for index in range(22)
    ]
    edges = [
        {
            "id": f"edge-{index}",
            "source_entity_id": "node-0",
            "target_entity_id": f"node-{index}",
            "edge_type": "contains",
        }
        for index in range(1, len(nodes))
    ]
    with _serve_viewer(nodes, edges) as url:
        _wait_for_graph(page, url, len(nodes))
        metrics = _layout_metrics(page)

    assert metrics["overlaps"] == 0
    assert metrics["minimumGap"] >= 16


@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 720}, {"width": 800, "height": 900}])
def test_mixed_graph_packs_components_without_wasting_space(
    page: Page,
    viewport: ViewportSize,
) -> None:
    page.set_viewport_size(viewport)
    nodes = [_node(index, f"Mixed component document {index}") for index in range(10)]
    edges = [
        {
            "id": "edge-0-1",
            "source_entity_id": "node-0",
            "target_entity_id": "node-1",
            "edge_type": "contains",
        },
        {
            "id": "edge-1-2",
            "source_entity_id": "node-1",
            "target_entity_id": "node-2",
            "edge_type": "contains",
        },
        {
            "id": "edge-3-4",
            "source_entity_id": "node-3",
            "target_entity_id": "node-4",
            "edge_type": "contains",
        },
    ]
    with _serve_viewer(nodes, edges) as url:
        _wait_for_graph(page, url, len(nodes))
        metrics = _layout_metrics(page)

    assert metrics["overlaps"] == 0
    assert metrics["minimumGap"] >= 16
    assert metrics["insideViewport"] is True
    # Utilisation is platform-sensitive: node width follows the rendered label, so a
    # wider default sans (CI's headless Linux Chromium) tiles fewer nodes per row into
    # the narrow canvas and inflates the bounding box faster than the node area. Measured
    # 0.217/0.262 (macOS, 1280x720 and 800x900) against 0.184/0.186 (CI, 800x900), so the
    # floor stays well under the lowest observed value. A layout that stopped packing
    # components lands below 0.10 and is still caught.
    assert metrics["utilization"] >= 0.15


def test_page_title_includes_search_and_focused_node(page: Page) -> None:
    nodes = [_node(1, "Quarterly planning")]
    with _serve_viewer(nodes, []) as url:
        _wait_for_graph(page, f"{url}?search=roadmap&node_id=node-1", len(nodes))
        expect(page).to_have_title("AgentGraph Viewer | Search: roadmap | Focus: Quarterly planning")


def test_detail_panel_focus_button_focuses_the_displayed_entity(page: Page) -> None:
    nodes = [_node(1, "Focus target"), _node(2, "Neighbour")]
    with _serve_viewer(nodes, []) as url:
        _wait_for_graph(page, url, len(nodes))
        page.evaluate("() => window.__agentGraphViewer.cy.getElementById('node-1').emit('tap')")
        focus_button = page.locator("#detail-focus")
        expect(focus_button).to_be_visible()
        expect(focus_button).to_have_attribute("aria-label", "Focus on entity")
        expect(focus_button).to_have_attribute("aria-pressed", "false")

        focus_button.click()

        expect(page.locator("#lookup-input")).to_have_value("node-1")
        expect(page).to_have_url(
            f"{url}?limit=20&node_id=node-1&selected_id=node-1"
        )
        expect(page).to_have_title("AgentGraph Viewer | Focus: Focus target")
        expect(focus_button).to_have_attribute("aria-pressed", "true")


def test_detail_panel_shows_available_entity_timestamps(page: Page) -> None:
    timestamped = _node(1, "Timestamped document")
    timestamped.update(
        {
            "created_at": "2026-06-01T01:00:00Z",
            "updated_at": "2026-06-02T02:00:00Z",
            "source_created_at": "2026-05-30T03:00:00Z",
            "source_updated_at": "2026-05-31T04:00:00Z",
            "observed_at": "2026-06-04T05:00:00Z",
        }
    )
    with _serve_viewer([timestamped], []) as url:
        _wait_for_graph(page, url, 1)
        page.evaluate("() => window.__agentGraphViewer.cy.getElementById('node-1').emit('tap')")

        labels = page.locator("#detail-body .detail-label")
        expect(labels).to_contain_text(
            ["Observed", "Created", "Updated", "Source created", "Source updated"]
        )


def test_detail_panel_keeps_lifecycle_timestamp_rows_when_observed_is_missing(page: Page) -> None:
    node = _node(1, "Unobserved document")
    with _serve_viewer([node], []) as url:
        _wait_for_graph(page, url, 1)
        page.evaluate("() => window.__agentGraphViewer.cy.getElementById('node-1').emit('tap')")

        labels = page.locator("#detail-body .detail-label")
        expect(labels).to_contain_text(["Observed", "Created", "Updated"])
        expect(page.locator("#detail-body .detail-value").filter(has_text="—")).to_have_count(3)


@pytest.mark.parametrize("view", ["graph", "list"])
def test_person_merge_refetches_active_view_and_canonical_detail(page: Page, view: str) -> None:
    nodes = [_person(1, "Canonical person"), _person(2, "Duplicate person")]
    with _serve_viewer(nodes, []) as url:
        view_url = f"{url}?view=list" if view == "list" else url
        _wait_for_graph(page, view_url, 0 if view == "list" else len(nodes))
        if view == "list":
            page.locator('tr[data-entity-id="person-1"]').click()
        else:
            page.evaluate("() => window.__agentGraphViewer.cy.getElementById('person-1').emit('tap')")

        page.get_by_label("Duplicate person IDs or platform references").fill("person-2")
        page.on("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Merge into this person").click()

        expect(page.locator(".merged-people-list")).to_contain_text("Duplicate person")
        if view == "list":
            expect(page.locator("#node-list-body tr")).to_have_count(1)
            expect(page.locator('tr[data-entity-id="person-2"]')).to_have_count(0)
        else:
            page.wait_for_function("() => window.__agentGraphViewer.cy.nodes().length === 1")
            assert page.evaluate(
                "() => window.__agentGraphViewer.cy.getElementById('person-2').length"
            ) == 0


def test_slash_focuses_search_without_intercepting_text_input(page: Page) -> None:
    nodes = [_node(1, "Keyboard shortcuts")]
    with _serve_viewer(nodes, []) as url:
        _wait_for_graph(page, url, len(nodes))

        page.locator("#canvas-wrap").click(position={"x": 20, "y": 20})
        page.keyboard.press("/")
        expect(page.locator("#search-input")).to_be_focused()
        expect(page.locator("#search-input")).to_have_value("")

        lookup_input = page.locator("#lookup-input")
        lookup_input.focus()
        page.keyboard.type("/")
        expect(lookup_input).to_be_focused()
        expect(lookup_input).to_have_value("/")


def test_j_and_k_move_through_the_entity_list(page: Page) -> None:
    nodes = [_node(1, "First entity"), _node(2, "Second entity"), _node(3, "Third entity")]
    with _serve_viewer(nodes, []) as url:
        _wait_for_graph(page, f"{url}?view=list", 0)
        expect(page.locator("#node-list-body tr")).to_have_count(3)

        page.locator('tr[data-entity-id="node-1"]').click()
        page.keyboard.press("j")
        expect(page.locator('tr[data-entity-id="node-2"]')).to_have_attribute("aria-selected", "true")
        expect(page.locator("#detail-title")).to_have_text("node-2")

        page.keyboard.press("k")
        expect(page.locator('tr[data-entity-id="node-1"]')).to_have_attribute("aria-selected", "true")
        expect(page.locator("#detail-title")).to_have_text("node-1")


def test_shortcut_help_button_toggles_the_right_pane(page: Page) -> None:
    nodes = [_node(1, "Shortcut entity")]
    with _serve_viewer(nodes, []) as url:
        _wait_for_graph(page, url, len(nodes))
        help_button = page.get_by_role("button", name="Keyboard shortcuts", exact=True)

        help_button.click()
        expect(help_button).to_have_attribute("aria-pressed", "true")
        expect(page.locator("#detail")).to_have_class("open")
        expect(page.locator("#detail-title")).to_have_text("Keyboard shortcuts")
        expect(page.locator("#shortcut-body")).to_be_visible()

        help_button.click()
        expect(help_button).to_have_attribute("aria-pressed", "false")
        expect(page.locator("#detail")).not_to_have_class("open")
        expect(page.locator("#shortcut-body")).to_be_hidden()


def test_shortcut_help_button_is_positioned_inside_the_sidebar(page: Page) -> None:
    nodes = [_node(1, "Sidebar entity")]
    with _serve_viewer(nodes, []) as url:
        _wait_for_graph(page, url, len(nodes))
        bounds = page.evaluate(
            """() => {
              const sidebar = document.querySelector('#sidebar').getBoundingClientRect();
              const footer = document.querySelector('#sidebar-footer').getBoundingClientRect();
              const button = document.querySelector('#shortcut-help-btn').getBoundingClientRect();
              return {
                sidebar: { left: sidebar.left, right: sidebar.right, bottom: sidebar.bottom },
                footer: { left: footer.left, right: footer.right, bottom: footer.bottom },
                button: { left: button.left, right: button.right, bottom: button.bottom },
              };
            }"""
        )

        assert bounds["button"]["left"] >= bounds["sidebar"]["left"]
        assert bounds["button"]["right"] <= bounds["sidebar"]["right"]
        assert bounds["button"]["right"] == bounds["footer"]["right"]
        assert bounds["button"]["bottom"] == bounds["footer"]["bottom"]


def test_i_toggles_selected_node_detail_without_intercepting_text_input(page: Page) -> None:
    nodes = [_node(1, "Keyboard detail"), _node(2, "Other node")]
    with _serve_viewer(nodes, []) as url:
        _wait_for_graph(page, url, len(nodes))
        canvas = page.locator("#cy").bounding_box()
        position = page.evaluate("() => window.__agentGraphViewer.cy.getElementById('node-1').renderedPosition()")
        assert canvas is not None
        page.mouse.click(canvas["x"] + position["x"], canvas["y"] + position["y"])
        assert page.evaluate("() => window.__agentGraphViewer.cy.$('node:selected').id()") == "node-1"
        expect(page.locator("#detail")).to_have_class("open")

        page.locator("#detail-title").click()
        page.keyboard.press("i")
        expect(page.locator("#detail")).not_to_have_class("open")

        page.keyboard.press("I")
        expect(page.locator("#detail")).to_have_class("open")

        lookup_input = page.locator("#lookup-input")
        lookup_input.focus()
        page.keyboard.type("i")
        expect(lookup_input).to_have_value("i")
        expect(page.locator("#detail")).to_have_class("open")


def test_i_toggles_focused_url_node_detail(page: Page) -> None:
    nodes = [_node(1, "Focused keyboard detail")]
    with _serve_viewer(nodes, []) as url:
        _wait_for_graph(page, f"{url}?node_id=node-1", len(nodes))

        assert page.evaluate("() => window.__agentGraphViewer.cy.$('node:selected').id()") == "node-1"

        page.keyboard.press("i")
        expect(page.locator("#detail")).to_have_class("open")

        page.keyboard.press("i")
        expect(page.locator("#detail")).not_to_have_class("open")

        page.keyboard.press("i")
        expect(page.locator("#detail")).to_have_class("open")


def test_i_hides_and_shows_url_selected_detail_in_list_mode(page: Page) -> None:
    nodes = [_node(1, "List keyboard detail")]
    with _serve_viewer(nodes, []) as url:
        page.goto(f"{url}?limit=100&node_id=node-1&selected_id=node-1&depth=0&view=list")
        expect(page.locator("#detail")).to_have_class("open")
        selected_row = page.locator('tr[data-entity-id="node-1"]')
        expect(selected_row).to_have_attribute("aria-selected", "true")

        page.locator("#detail-title").click()
        page.keyboard.press("i")
        expect(page.locator("#detail")).not_to_have_class("open")
        expect(page).to_have_url(
            f"{url}?limit=100&node_id=node-1&selected_id=node-1&depth=0&view=list"
        )
        expect(selected_row).to_have_attribute("aria-selected", "true")

        page.keyboard.press("i")
        expect(page.locator("#detail")).to_have_class("open")


def test_node_bounds_and_labels_remain_capped_across_zoom(page: Page) -> None:
    nodes = [_node(1, "A deliberately long document title that needs more than three lines to display")]
    with _serve_viewer(nodes, []) as url:
        _wait_for_graph(page, url, len(nodes))
        measurements = page.evaluate(
            """() => {
              const cy = window.__agentGraphViewer.cy;
              const node = cy.nodes()[0];
              return [0.5, 1, 2].map(zoom => {
                cy.zoom({ level: zoom, renderedPosition: node.renderedPosition() });
                return {
                  body: node.renderedBoundingBox({ includeLabels: false, includeOverlays: false }),
                  all: node.renderedBoundingBox({ includeLabels: true, includeOverlays: false }),
                };
              });
            }"""
        )

    for measurement in measurements:
        body = measurement["body"]
        all_bounds = measurement["all"]
        assert all_bounds["w"] <= 176
        assert all_bounds["h"] <= 72
        assert all_bounds["x1"] >= body["x1"]
        assert all_bounds["x2"] <= body["x2"]
        assert all_bounds["y1"] >= body["y1"]
        assert all_bounds["y2"] <= body["y2"]


def test_selection_and_hover_do_not_move_graph_geometry(page: Page) -> None:
    nodes = [_node(1, "Hover target"), _node(2, "Other node")]
    with _serve_viewer(nodes, []) as url:
        _wait_for_graph(page, url, len(nodes))
        before = page.evaluate(
            """() => window.__agentGraphViewer.cy.nodes().map(node => ({
              id: node.id(), position: node.position(), box: node.renderedBoundingBox({ includeLabels: true, includeOverlays: false }),
            }))"""
        )
        page.evaluate("() => window.__agentGraphViewer.cy.nodes()[0].select()")
        after_selection = page.evaluate(
            """() => window.__agentGraphViewer.cy.nodes().map(node => ({
              id: node.id(), position: node.position(), box: node.renderedBoundingBox({ includeLabels: true, includeOverlays: false }),
            }))"""
        )
        canvas = page.locator("#cy").bounding_box()
        target = page.evaluate("() => window.__agentGraphViewer.cy.nodes()[0].renderedPosition()")
        assert canvas is not None
        page.mouse.move(canvas["x"] + target["x"], canvas["y"] + target["y"])
        expect(page.locator("#hover-card")).to_be_visible()
        expect(page.locator("#hover-card-title")).to_have_text("Hover target")
        after = page.evaluate(
            """() => window.__agentGraphViewer.cy.nodes().map(node => ({
              id: node.id(), position: node.position(), box: node.renderedBoundingBox({ includeLabels: true, includeOverlays: false }),
            }))"""
        )

    assert after_selection == before
    assert after == before


def test_hover_card_hides_when_pointer_enters_detail_panel(page: Page) -> None:
    nodes = [_node(1, "Hover target")]
    with _serve_viewer(nodes, []) as url:
        _wait_for_graph(page, url, len(nodes))
        page.locator("#detail").evaluate("panel => panel.classList.add('open')")
        page.wait_for_timeout(250)
        canvas = page.locator("#cy").bounding_box()
        target = page.evaluate("() => window.__agentGraphViewer.cy.nodes()[0].renderedPosition()")
        detail = page.locator("#detail").bounding_box()
        assert canvas is not None
        assert detail is not None

        page.mouse.move(canvas["x"] + target["x"], canvas["y"] + target["y"])
        expect(page.locator("#hover-card")).to_be_visible()
        page.mouse.move(detail["x"] + 20, detail["y"] + 20)
        expect(page.locator("#hover-card")).not_to_be_visible()


def test_open_detail_uses_its_own_desktop_column(page: Page) -> None:
    nodes = [_node(1, "Detail target")]
    with _serve_viewer(nodes, []) as url:
        _wait_for_graph(page, url, len(nodes))
        canvas_before = page.locator("#canvas-wrap").bounding_box()
        page.locator("#app").evaluate("app => app.classList.add('detail-open')")
        page.locator("#detail").evaluate("panel => panel.classList.add('open')")
        page.wait_for_timeout(250)
        canvas_after = page.locator("#canvas-wrap").bounding_box()
        detail = page.locator("#detail").bounding_box()

    assert canvas_before is not None
    assert canvas_after is not None
    assert detail is not None
    assert canvas_before["width"] - canvas_after["width"] >= 300
    assert detail["x"] >= canvas_after["x"] + canvas_after["width"]
