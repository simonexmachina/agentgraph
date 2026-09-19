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


def test_build_writes_docs_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output_dir = tmp_path / "docs"
    monkeypatch.setattr(build_docs, "DOCS_OUT", output_dir)

    build_docs.build()

    docs_css = (output_dir / "docs.css").read_text(encoding="utf-8")
    social_image = output_dir / "assets" / "og-image.png"
    architecture_svg = output_dir / "assets" / "diagrams" / "architecture-overview-dark.svg"
    index_html = (output_dir / "index.html").read_text(encoding="utf-8")
    install_html = (output_dir / "install.html").read_text(encoding="utf-8")
    configuration_html = (output_dir / "configuration.html").read_text(encoding="utf-8")
    extending_html = (output_dir / "extending.html").read_text(encoding="utf-8")
    rss_html = (output_dir / "rss.html").read_text(encoding="utf-8")
    commands_html = (output_dir / "commands" / "index.html").read_text(encoding="utf-8")
    search_html = (output_dir / "commands" / "search.html").read_text(encoding="utf-8")
    mcp_html = (output_dir / "mcp" / "index.html").read_text(encoding="utf-8")
    mcp_auth_html = (output_dir / "mcp" / "authenticate-provider.html").read_text(encoding="utf-8")
    connectors_html = (output_dir / "connectors.html").read_text(encoding="utf-8")
    how_it_works_html = (output_dir / "how-it-works.html").read_text(encoding="utf-8")
    demo_html = (output_dir / "demo.html").read_text(encoding="utf-8")
    demo_article = demo_html.split('<article class="doc">', 1)[1].split("</article>", 1)[0]
    privacy_html = (output_dir / "privacy.html").read_text(encoding="utf-8")
    terms_html = (output_dir / "terms.html").read_text(encoding="utf-8")
    redirect_html = (output_dir / "commands.html").read_text(encoding="utf-8")

    assert 'class="shell"' in index_html
    assert architecture_svg.exists()
    assert architecture_svg.stat().st_size > 1_000
    assert social_image.exists()
    assert social_image.stat().st_size > 1_000
    assert "architecture-overview-dark.svg" not in index_html
    assert 'src="assets/diagrams/architecture-overview-dark.svg"' in how_it_works_html
    assert 'class="architecture-figure architecture-figure-fit"' in how_it_works_html
    assert "<title>AgentGraph - Local-first context for AI agents</title>" in index_html
    assert 'rel="canonical" href="https://agentgraph.simonwa.de/"' in index_html
    social_image_url = "https://agentgraph.simonwa.de/assets/og-image.png?v=2"
    assert f'property="og:image" content="{social_image_url}"' in index_html
    assert 'property="og:image:width" content="1200"' in index_html
    assert 'property="og:image:height" content="630"' in index_html
    assert 'name="twitter:card" content="summary_large_image"' in index_html
    assert f'name="twitter:image" content="{social_image_url}"' in index_html
    assert "local context for AI agents" in index_html
    assert "https://www.googletagmanager.com/gtag/js?id=G-36ETGXF6K5" in index_html
    assert "gtag('config', 'G-36ETGXF6K5');" in index_html
    assert 'id="doc-search"' in index_html
    assert 'class="toc"' in index_html
    assert 'class="page-summary"' not in index_html
    assert 'class="language-bash"' not in index_html
    assert 'agentgraph<span class="tok-w"> </span>mcp-config' not in index_html
    assert ".doc .codehilite .tok-n" in docs_css
    assert "var(--code-name)" in docs_css
    assert ".architecture-figure-fit img{min-width:0}" in docs_css
    assert ".doc pre .copy svg" in docs_css
    assert ">Commands</a>" in index_html
    assert (
        '<section><h2>Configuration</h2><a class="nav-link" href="configuration.html">Configuration</a><a class="nav-link" href="rss.html">RSS</a><a class="nav-link" href="extending.html">Extending</a></section>'
        in index_html
    )
    assert 'href="extending.html"' in index_html
    assert 'href="rss.html"' in index_html
    nav_html = index_html.split('<nav aria-label="Documentation">', 1)[1].split("</nav>", 1)[0]
    assert 'href="tester-extension-install.html"' not in nav_html
    assert 'href="privacy.html"' not in nav_html
    assert 'href="extension-distribution.html"' not in nav_html
    assert 'https://github.com/simonexmachina/agentgraph/blob/main/examples/custom_connector.py' in extending_html
    assert "Interface reference" not in extending_html
    assert "<table>" not in extending_html
    assert "Why extend AgentGraph" in extending_html
    assert "custom connectors" in extending_html
    assert "RSS" in extending_html
    assert "Add RSS and Atom feeds" in rss_html
    assert "feed.xml" in rss_html
    assert "platform=rss" in rss_html
    assert not (output_dir / "slack.html").exists()
    assert 'href="install.html"' in index_html
    assert 'href="quickstart.html"' not in index_html
    assert "What it lets the agent perceive" in connectors_html
    assert "Context paths" not in connectors_html
    assert "Bring any service into your agent's world" in connectors_html
    assert "Three ways context enters" in how_it_works_html
    assert "agentgraph fetch" in how_it_works_html
    assert "Before I reply to Maya" in demo_html
    assert "agentgraph-server" in demo_html
    assert "demo<span" in demo_html
    assert (
        "Open a new coding-agent session" in demo_html
        or "Open a coding-agent session" in demo_html
    )
    assert "AgentGraph CLI" in demo_html
    assert "Open the viewer" in demo_html
    assert 'agentgraph<span class="tok-w"> </span>serve' in demo_html
    assert 'href="http://127.0.0.1:8765/viewer"' in demo_html
    assert 'href="install.html"' in demo_article
    assert "mcp-config" not in demo_article
    assert "http.server" not in demo_article
    assert "Observe the first article" not in demo_article
    assert "git clone" not in demo_article
    assert "uv sync" not in demo_article
    assert "AGENTGRAPH_BACKEND_SQLITE_VECTOR_MODE" not in demo_article
    assert "Retention and deletion" in privacy_html
    assert "Open-source software" in terms_html
    assert "<h1>Commands</h1>" in commands_html
    assert "After the extension is installed, start" in install_html
    assert "agentgraph-server" in install_html
    assert "agentgraph-connector-google" not in install_html
    assert "uv sync" not in install_html
    assert "source .venv" not in install_html
    assert "Connect an MCP client" in install_html
    assert 'default_permissions = "agentgraph"' not in configuration_html
    # The supported clients are named explicitly rather than split into "desktop" and
    # "coding agent", which put Codex on the wrong side of the distinction.
    for client in ("ChatGPT Desktop Work Mode", "Codex", "Claude Desktop", "Claude Code"):
        assert client in install_html
    # Highlighting wraps the whitespace in spans, so match the start of the block.
    assert '<code class="language-bash">codex' in install_html
    assert "Connect sources" in install_html
    assert "onboard" in install_html
    assert "Authenticate connectors" not in install_html
    assert "agentgraph search" in search_html
    assert "<code>agentgraph search</code>" in search_html
    assert 'href="../docs.css"' in search_html
    assert 'href="../index.html"' in search_html
    assert 'class="page-nav-prev" href="index.html"' in search_html
    assert 'class="page-nav-next" href="get.html"' in search_html
    assert "MCP tools" in mcp_html
    assert "authenticate_provider_tool" in mcp_auth_html
    assert 'content="0; url=commands/index.html"' in redirect_html

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
    assert "get_entity_tool(entity_id, resolve=false) -> JSON string" in source_for(
        "mcp/get-entity.html"
    )
    assert "list_connectors_tool(verify=false) -> JSON string" in source_for(
        "mcp/list-connectors.html"
    )
