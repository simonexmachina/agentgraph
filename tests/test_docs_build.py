from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from click import Group

import scripts.build_docs as build_docs
import scripts.serve_docs as serve_docs


def test_render_markdown_keeps_fenced_code_blocks_separate() -> None:
    markdown = """## First

```bash
echo hello
```

## Second
"""

    rendered, headings = build_docs.render_markdown(markdown)

    assert 'class="codehilite"' in rendered
    assert 'class="language-bash"' in rendered
    assert "tok-" in rendered
    assert (
        '<h2 id="second"><a class="anchor" href="#second" aria-label="Anchor link">#</a>Second</h2>'
        in rendered
    )
    assert [heading.text for heading in headings] == ["First", "Second"]


def test_render_inline_preserves_code_symbols() -> None:
    rendered = build_docs.render_inline(
        "Use `run_auth_flow(cls) -> None` with `agentgraph auth <source>`."
    )

    assert "<code>run_auth_flow(cls) -&gt; None</code>" in rendered
    assert "<code>agentgraph auth &lt;source&gt;</code>" in rendered
    assert "-&amp;gt;" not in rendered
    assert "&amp;lt;source&amp;gt;" not in rendered


def test_render_inline_preserves_link_query_separator() -> None:
    rendered = build_docs.render_inline("[Extension](https://example.com/detail?id=1&hl=en-AU)")

    assert 'href="https://example.com/detail?id=1&amp;hl=en-AU"' in rendered
    assert "&amp;amp;" not in rendered


def test_on_page_nav_renders_inline_code_without_backticks() -> None:
    page = build_docs.Page(
        meta=build_docs.PageMeta(
            source_path=Path("configuration.md"),
            output_path=Path("configuration.html"),
            title="Configuration",
            description="Configuration settings.",
            nav_title="Configuration",
            nav_hidden=False,
            section="Configuration",
            order=10,
            summary="",
            aliases=(),
        ),
        body="",
        headings=(build_docs.Heading(3, "`AGENTGRAPH_CONFIG_DIR`", "agentgraph-config-dir"),),
    )

    rendered = build_docs.build_on_page_nav(page)

    assert rendered == (
        '<a class="toc-l3" href="#agentgraph-config-dir"><code>AGENTGRAPH_CONFIG_DIR</code></a>'
    )
    assert "`" not in rendered


def test_render_markdown_supports_tables() -> None:
    rendered, _ = build_docs.render_markdown(
        """| Connector | Context paths |
| --- | --- |
| Web | Observe, fetch |
| Gmail | Observe, fetch, poll |
"""
    )

    assert "<table><thead><tr><th>Connector</th><th>Context paths</th></tr></thead>" in rendered
    assert "<tbody><tr><td>Web</td><td>Observe, fetch</td></tr>" in rendered


def test_build_generates_site_and_assets_with_valid_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "docs"
    monkeypatch.setattr(build_docs, "DOCS_OUT", output_dir)

    build_docs.build()

    html_files = list(output_dir.rglob("*.html"))
    assert html_files
    assert (output_dir / "docs.css").is_file()
    assert (output_dir / "assets" / "og-image.png").is_file()
    assert (
        output_dir / "assets" / "diagrams" / "architecture-overview-dark.svg"
    ).is_file()
    configuration_page = (output_dir / "configuration.html").read_text(encoding="utf-8")
    assert 'class="doc-grid no-toc"' in configuration_page
    assert 'aria-label="On this page"' not in configuration_page

    broken_links: list[str] = []
    for html_path in output_dir.rglob("*.html"):
        page = html_path.read_text(encoding="utf-8")
        for match in re.finditer(r'(href|src)="([^"]+)"', page):
            attribute = match.group(1)
            url = match.group(2)
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            link_path = Path(unquote(parsed.path.lstrip("/")))
            target = (
                output_dir / link_path
                if parsed.path.startswith("/")
                else html_path.parent / link_path
            )
            if parsed.path.endswith("/"):
                target /= "index.html"
            if not target.resolve().exists():
                broken_links.append(f"{html_path.relative_to(output_dir)} {attribute} -> {url}")
    assert broken_links == []


def test_build_requires_pygments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output_dir = tmp_path / "docs"
    monkeypatch.setattr(build_docs, "DOCS_OUT", output_dir)
    monkeypatch.setattr(build_docs, "highlight", None)

    with pytest.raises(RuntimeError, match="Docs build requires Pygments"):
        build_docs.build()


def test_watch_snapshot_tracks_docs_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs_src = tmp_path / "docs-src"
    scripts_dir = tmp_path / "scripts"
    docs_src.mkdir()
    scripts_dir.mkdir()
    (docs_src / "index.md").write_text("hello", encoding="utf-8")
    (scripts_dir / "build_docs.py").write_text("print('x')", encoding="utf-8")
    (scripts_dir / "serve_docs.py").write_text("print('y')", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    snapshot = serve_docs.snapshot_mtimes(serve_docs.watch_paths())

    assert Path("docs-src/index.md") in snapshot
    assert Path("scripts/build_docs.py") in snapshot
    assert Path("scripts/serve_docs.py") in snapshot


def test_load_pages_ignores_node_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    docs_src = tmp_path / "docs-src"
    dependency = docs_src / "node_modules" / "example"
    dependency.mkdir(parents=True)
    (dependency / "README.md").write_text("not a docs page", encoding="utf-8")
    (docs_src / "index.md").write_text(
        """+++
title = "Home"
description = "Home"
nav_title = "Home"
section = "Start"
order = 1
summary = ""
output = "index.html"
source_path = "docs-src/index.md"
+++

Hello.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(build_docs, "DOCS_SRC", docs_src)

    pages = build_docs.load_pages()

    assert [page.meta.title for page in pages] == ["Home"]


@pytest.mark.asyncio
async def test_command_and_mcp_reference_pages_match_runtime_interfaces() -> None:
    from typer.main import get_command

    from agentgraph.cli import app
    from agentgraph.mcp.server import mcp

    pages = build_docs.load_pages()
    documented_commands = {
        page.meta.title for page in pages if page.meta.output_path.parent == Path("commands")
    }
    documented_commands.remove("Commands")
    root_command = get_command(app)
    assert isinstance(root_command, Group)
    runtime_commands: set[str] = set(root_command.commands)

    documented_tools = {
        page.meta.title for page in pages if page.meta.output_path.parent == Path("mcp")
    }
    documented_tools.remove("MCP tools")
    runtime_tools = {tool.name for tool in await mcp.list_tools()}

    assert documented_commands == runtime_commands
    assert documented_tools == runtime_tools

    pages_by_output = {page.meta.output_path: page for page in pages}

    def source_for(output_path: str) -> str:
        return pages_by_output[Path(output_path)].meta.source_path.read_text(encoding="utf-8")

    assert "agentgraph auth [status] [--verify] [--json]" in source_for("commands/auth.html")
    assert "agentgraph list-connectors [--verify] [--json]" in source_for(
        "commands/list-connectors.html"
    )
    assert "Web is a required dependency of `agentgraph-server`" in source_for(
        "commands/list-connectors.html"
    )
    assert "agentgraph serve" in source_for("install.html")
    assert "agentgraph server" not in source_for("install.html")
    demo_source = source_for("demo.html")
    assert 'mkdir -p "$HOME/agentgraph-tmp"' in demo_source
    assert "AGENTGRAPH_CONFIG_DIR=%s" in demo_source
    assert "get_entity_tool(entity_id, resolve=false) -> structured MCP result" in source_for(
        "mcp/get-entity.html"
    )
    assert "list_connectors_tool(verify=false) -> structured MCP result" in source_for(
        "mcp/list-connectors.html"
    )
