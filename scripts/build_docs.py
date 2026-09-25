from __future__ import annotations

# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false
import html
import posixpath
import re
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

ROOT = Path(__file__).resolve().parent.parent
DOCS_SRC = ROOT / "docs-src"
DOCS_OUT = ROOT / "docs"
GITHUB_ROOT = "https://github.com/simonexmachina/agentgraph/blob/main"
SITE_ROOT = "https://agentgraph.simonwa.de"
SOCIAL_IMAGE_URL = f"{SITE_ROOT}/assets/og-image.png?v=3"
SECTION_ORDER = {"Start": 10, "Configuration": 15, "Reference": 20, "MCP": 30}

_FORMATTER = HtmlFormatter(nowrap=True, classprefix="tok-")


def ensure_syntax_highlighter() -> None:
    if not callable(highlight):
        raise RuntimeError(
            "Docs build requires Pygments. Run it from the project environment "
            "(for example `.venv/bin/python scripts/build_docs.py`)."
        )


@dataclass(frozen=True)
class PageMeta:
    source_path: Path
    output_path: Path
    title: str
    description: str
    nav_title: str
    nav_hidden: bool
    section: str
    order: int
    summary: str
    aliases: tuple[str, ...]
    toc_enabled: bool = True


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    slug: str


@dataclass(frozen=True)
class Page:
    meta: PageMeta
    body: str
    headings: tuple[Heading, ...]


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned or "section"


def parse_frontmatter(text: str) -> tuple[PageMeta, str]:
    if not text.startswith("+++\n"):
        raise ValueError("Missing TOML frontmatter")
    _, frontmatter, body = text.split("+++\n", 2)
    data = tomllib.loads(frontmatter)

    source_path = Path(str(data["source_path"]))
    aliases = tuple(str(alias) for alias in data.get("aliases", []))
    return (
        PageMeta(
            source_path=source_path,
            output_path=Path(str(data["output"])),
            title=str(data["title"]),
            description=str(data["description"]),
            nav_title=str(data["nav_title"]),
            nav_hidden=bool(data.get("nav_hidden", False)),
            section=str(data["section"]),
            order=int(data["order"]),
            summary=str(data["summary"]),
            aliases=aliases,
            toc_enabled=bool(data.get("toc", True)),
        ),
        body.strip() + "\n",
    )


def render_inline(text: str) -> str:
    code_placeholders: dict[str, str] = {}

    def replace_code(match: re.Match[str]) -> str:
        key = f"@@CODE{len(code_placeholders)}@@"
        code_placeholders[key] = f"<code>{html.escape(match.group(1))}</code>"
        return key

    text = re.sub(r"`([^`]+)`", replace_code, text)
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", lambda m: f"<code>{html.escape(m.group(1))}</code>", escaped)
    escaped = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: (
            f'<a href="{html.escape(html.unescape(m.group(2)), quote=True)}">{m.group(1)}</a>'
        ),
        escaped,
    )
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    for key, value in code_placeholders.items():
        escaped = escaped.replace(html.escape(key), value)
    return escaped


def render_code_block(code: str, language: str) -> str:
    code_class = f' class="language-{html.escape(language, quote=True)}"' if language else ""

    try:
        lexer = (
            get_lexer_by_name(language, stripall=False) if language else TextLexer(stripall=False)
        )
    except ClassNotFound:
        lexer = TextLexer(stripall=False)

    highlighted = highlight(code, lexer, _FORMATTER)
    return f'<div class="codehilite"><pre><code{code_class}>{highlighted}</code></pre></div>'


def split_table_row(line: str) -> list[str]:
    """Split one simple Markdown table row into cells."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_table_separator(line: str) -> bool:
    cells = split_table_row(line)
    return len(cells) > 0 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def render_markdown(body: str) -> tuple[str, tuple[Heading, ...]]:
    lines = body.splitlines()
    parts: list[str] = []
    headings: list[Heading] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("```"):
            fence = "```"
            language = stripped[3:].strip()
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip() != fence:
                code_lines.append(lines[i])
                i += 1
            parts.append(render_code_block("\n".join(code_lines), language))
            i += 1
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            slug = slugify(text)
            headings.append(Heading(level=level, text=text, slug=slug))
            anchor = f'<a class="anchor" href="#{slug}" aria-label="Anchor link">#</a>'
            parts.append(f'<h{level} id="{slug}">{anchor}{render_inline(text)}</h{level}>')
            i += 1
            continue

        if "|" in stripped and i + 1 < len(lines) and is_table_separator(lines[i + 1].strip()):
            headers = split_table_row(stripped)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = split_table_row(lines[i])
                if len(cells) != len(headers):
                    break
                rows.append(cells)
                i += 1
            head_html = "".join(f"<th>{render_inline(cell)}</th>" for cell in headers)
            body_html = "".join(
                "<tr>" + "".join(f"<td>{render_inline(cell)}</td>" for cell in row) + "</tr>"
                for row in rows
            )
            parts.append(
                f"<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>"
            )
            continue

        if stripped.startswith("<"):
            block_lines = [line]
            i += 1
            while i < len(lines):
                next_line = lines[i]
                next_stripped = next_line.strip()
                if not next_stripped:
                    block_lines.append(next_line)
                    i += 1
                    continue
                if (
                    next_stripped.startswith("<")
                    or next_stripped.startswith(">")
                    or next_stripped.startswith("- ")
                    or re.match(r"^\d+\.\s+", next_stripped)
                ):
                    block_lines.append(next_line)
                    i += 1
                    continue
                break
            parts.append("\n".join(block_lines))
            continue

        if stripped.startswith("> "):
            quote_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("> "):
                quote_lines.append(lines[i].strip()[2:])
                i += 1
            parts.append("<blockquote>" + render_inline(" ".join(quote_lines)) + "</blockquote>")
            continue

        if stripped.startswith("- "):
            items: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(lines[i].strip()[2:])
                i += 1
            parts.append(
                "<ul>" + "".join(f"<li>{render_inline(item)}</li>" for item in items) + "</ul>"
            )
            continue

        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < len(lines):
                match = re.match(r"^\d+\.\s+(.*)$", lines[i].strip())
                if not match:
                    break
                items.append(match.group(1))
                i += 1
            parts.append(
                "<ol>" + "".join(f"<li>{render_inline(item)}</li>" for item in items) + "</ol>"
            )
            continue

        paragraph_lines = [stripped]
        i += 1
        while i < len(lines):
            next_line = lines[i].strip()
            if not next_line:
                break
            if next_line.startswith(("```", "#", "<", "> ", "- ")) or re.match(
                r"^\d+\.\s+", next_line
            ):
                break
            paragraph_lines.append(next_line)
            i += 1
        parts.append(f"<p>{render_inline(' '.join(paragraph_lines))}</p>")

    return "\n\n".join(parts), tuple(headings)


def load_pages() -> list[Page]:
    pages: list[Page] = []
    for source_path in sorted(DOCS_SRC.rglob("*.md")):
        if "node_modules" in source_path.parts:
            continue
        text = source_path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        rendered_body, headings = render_markdown(body)
        pages.append(Page(meta=meta, body=rendered_body, headings=headings))
    return sorted(
        pages,
        key=lambda page: (
            SECTION_ORDER.get(page.meta.section, 999),
            page.meta.order,
            str(page.meta.output_path),
        ),
    )


def build_global_nav(pages: list[Page], current_page: Page) -> str:
    grouped: dict[str, list[Page]] = {}
    section_order: list[str] = []
    for page in pages:
        if page.meta.section not in grouped:
            grouped[page.meta.section] = []
            section_order.append(page.meta.section)
        grouped[page.meta.section].append(page)

    section_blocks: list[str] = []
    for section in section_order:
        links = ""
        for page in grouped[section]:
            if page.meta.nav_hidden:
                continue
            current_class = (
                "nav-link active"
                if page.meta.output_path == current_page.meta.output_path
                else "nav-link"
            )
            links += f'<a class="{current_class}" href="{page_permalink(page.meta)}">{page.meta.nav_title}</a>'
        if links:
            section_blocks.append(f"<section><h2>{html.escape(section)}</h2>{links}</section>")
    return "\n".join(section_blocks)


def build_on_page_nav(page: Page) -> str:
    items = [heading for heading in page.headings if heading.level in (2, 3)]
    if not items:
        return ""
    return "".join(
        f'<a class="toc-l{heading.level}" href="#{heading.slug}">{render_inline(heading.text)}</a>'
        for heading in items
    )


def page_permalink(meta: PageMeta) -> str:
    output = meta.output_path.as_posix()
    if output == "index.html":
        return "/"
    if output.endswith("/index.html"):
        return "/" + output[: -len("index.html")]
    return "/" + output


def output_relpath(path: Path) -> Path:
    return path.relative_to(DOCS_OUT) if path.is_absolute() else path


def relative_url(current_output: Path, target: str) -> str:
    if target.startswith(("http://", "https://", "mailto:", "#")):
        return target
    if not target.startswith("/"):
        return target

    current_dir = output_relpath(current_output).parent.as_posix() or "."
    if target == "/":
        target_path = "index.html"
    else:
        trimmed = target.lstrip("/")
        target_path = f"{trimmed}index.html" if target.endswith("/") else trimmed
    return posixpath.relpath(target_path, start=current_dir)


def normalize_internal_urls(fragment: str, current_output: Path) -> str:
    return re.sub(
        r'(?P<attribute>href|src)="(?P<url>/[^"]*)"',
        lambda m: (
            f'{m.group("attribute")}="'
            f'{html.escape(relative_url(current_output, m.group("url")), quote=True)}"'
        ),
        fragment,
    )


def build_prev_next(pages: list[Page], index: int) -> str:
    links: list[str] = []
    if index > 0:
        previous = pages[index - 1]
        links.append(
            f'<a class="page-nav-prev" href="{page_permalink(previous.meta)}"><small>Previous</small><span>{html.escape(previous.meta.nav_title)}</span></a>'
        )
    if index + 1 < len(pages):
        next_page = pages[index + 1]
        links.append(
            f'<a class="page-nav-next" href="{page_permalink(next_page.meta)}"><small>Next</small><span>{html.escape(next_page.meta.nav_title)}</span></a>'
        )
    return '<nav class="page-nav" aria-label="Pager">' + "".join(links) + "</nav>"


def build_page(page: Page, pages: list[Page], index: int, nav_html: str) -> str:
    source_href = f"{GITHUB_ROOT}/{page.meta.source_path.as_posix()}"
    title = html.escape(page.meta.title)
    description = html.escape(page.meta.description, quote=True)
    body_class = "home" if page.meta.output_path == Path("index.html") else ""
    document_title = (
        "AgentGraph - The perception layer for coding agents"
        if body_class == "home"
        else f"{title} - AgentGraph"
    )
    canonical_path = page_permalink(page.meta)
    canonical_url = f"{SITE_ROOT}{canonical_path}"
    toc_html = build_on_page_nav(page) if page.meta.toc_enabled else ""
    article_title = "" if body_class == "home" else f"          <h1>{title}</h1>\n"
    stylesheet_href = relative_url(page.meta.output_path, "/docs.css")
    home_href = relative_url(page.meta.output_path, "/")
    body_html = normalize_internal_urls(page.body, page.meta.output_path)
    nav_html = normalize_internal_urls(nav_html, page.meta.output_path)
    toc_html = normalize_internal_urls(toc_html, page.meta.output_path)
    pager_html = normalize_internal_urls(build_prev_next(pages, index), page.meta.output_path)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="theme-color" content="#07080a" />
  <meta name="color-scheme" content="light dark" />
  <title>{document_title}</title>
  <meta name="description" content="{description}" />
  <link rel="canonical" href="{canonical_url}" />
  <meta property="og:type" content="website" />
  <meta property="og:site_name" content="AgentGraph" />
  <meta property="og:title" content="{document_title}" />
  <meta property="og:description" content="{description}" />
  <meta property="og:url" content="{canonical_url}" />
  <meta property="og:image" content="{SOCIAL_IMAGE_URL}" />
  <meta property="og:image:width" content="1200" />
  <meta property="og:image:height" content="630" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="{document_title}" />
  <meta name="twitter:description" content="{description}" />
  <meta name="twitter:image" content="{SOCIAL_IMAGE_URL}" />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,300..800&family=Recursive:wght@300..800&family=JetBrains+Mono:wght@400..700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="{stylesheet_href}" />
  <!-- Google tag (gtag.js) -->
  <script async src="https://www.googletagmanager.com/gtag/js?id=G-36ETGXF6K5"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){{dataLayer.push(arguments);}}
    gtag('js', new Date());

    gtag('config', 'G-36ETGXF6K5');
  </script>
</head>
<body class="{body_class}">
  <button class="nav-toggle" type="button" aria-label="Toggle navigation" aria-expanded="false">
    <span aria-hidden="true"></span><span aria-hidden="true"></span><span aria-hidden="true"></span>
  </button>
  <div class="shell">
    <aside class="sidebar">
      <div class="sidebar-head">
        <a class="brand" href="{home_href}" aria-label="AgentGraph home">
          <span><strong>AgentGraph</strong><small>local context for AI agents</small></span>
        </a>
      </div>
      <label class="search"><span>Search</span><input id="doc-search" type="search" placeholder="slack, mcp, drive, poll"></label>
      <nav aria-label="Documentation">
{nav_html}
      </nav>
    </aside>
    <main class="page-shell">
      <header class="hero">
        <div class="hero-text">
          <p class="eyebrow">{html.escape(page.meta.section)}</p>
          <h1>{title}</h1>
        </div>
        <div class="hero-meta">
          <a class="repo" href="{home_href}">Home</a>
          <a class="repo" href="https://github.com/simonexmachina/agentgraph" rel="noopener">GitHub</a>
          <a class="edit" href="{source_href}">Edit page</a>
        </div>
      </header>
      <div class="doc-grid{' no-toc' if not toc_html else ''}">
        <article class="doc">
{article_title}\
{f'          <p class="page-summary">{render_inline(page.meta.summary)}</p>\n' if page.meta.summary.strip() else ""}\
{body_html}
{pager_html}
        </article>
        {f'<nav class="toc" aria-label="On this page"><h2>On this page</h2>{toc_html}</nav>' if toc_html else ''}
      </div>
    </main>
  </div>
  <script>
const sidebar=document.querySelector('.sidebar');
const toggle=document.querySelector('.nav-toggle');
const mobileNav=window.matchMedia('(max-width: 900px)');
const sidebarFocusable='a[href],button,input,select,textarea,[tabindex]';
function setSidebarFocusable(enabled){{sidebar?.querySelectorAll(sidebarFocusable).forEach((el)=>{{if(enabled){{if(el.dataset.sidebarTabindex!==undefined){{if(el.dataset.sidebarTabindex)el.setAttribute('tabindex',el.dataset.sidebarTabindex);else el.removeAttribute('tabindex');delete el.dataset.sidebarTabindex;}}}}else if(el.dataset.sidebarTabindex===undefined){{el.dataset.sidebarTabindex=el.getAttribute('tabindex')??'';el.setAttribute('tabindex','-1');}}}});}}
function setSidebarOpen(open){{if(!sidebar||!toggle)return;sidebar.classList.toggle('open',open);toggle.setAttribute('aria-expanded',open?'true':'false');if(mobileNav.matches){{sidebar.inert=!open;if(open)sidebar.removeAttribute('aria-hidden');else sidebar.setAttribute('aria-hidden','true');setSidebarFocusable(open);}}else{{sidebar.inert=false;sidebar.removeAttribute('aria-hidden');setSidebarFocusable(true);}}}}
setSidebarOpen(false);
toggle?.addEventListener('click',()=>setSidebarOpen(!sidebar?.classList.contains('open')));
document.addEventListener('click',(e)=>{{if(!sidebar?.classList.contains('open'))return;if(sidebar.contains(e.target)||toggle?.contains(e.target))return;setSidebarOpen(false);}});
document.addEventListener('keydown',(e)=>{{if(e.key==='Escape')setSidebarOpen(false);}});
const syncSidebarForViewport=()=>setSidebarOpen(sidebar?.classList.contains('open')??false);
if(mobileNav.addEventListener)mobileNav.addEventListener('change',syncSidebarForViewport);else mobileNav.addListener?.(syncSidebarForViewport);
const input=document.getElementById('doc-search');
input?.addEventListener('input',()=>{{const q=input.value.trim().toLowerCase();document.querySelectorAll('nav section').forEach(sec=>{{let any=false;sec.querySelectorAll('.nav-link').forEach(a=>{{const m=!q||a.textContent.toLowerCase().includes(q);a.style.display=m?'block':'none';if(m)any=true;}});sec.style.display=any?'block':'none';}});}});
function attachCopy(target,getText){{const copyIcon='<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5.5 2.5A1.5 1.5 0 0 1 7 1h5.5A1.5 1.5 0 0 1 14 2.5V10a1.5 1.5 0 0 1-1.5 1.5H11V13A1.5 1.5 0 0 1 9.5 14.5H4A1.5 1.5 0 0 1 2.5 13V5.5A1.5 1.5 0 0 1 4 4h1.5zm1.5 0V4h2.5A1.5 1.5 0 0 1 11 5.5V10h1.5a.5.5 0 0 0 .5-.5V2.5a.5.5 0 0 0-.5-.5H7.5a.5.5 0 0 0-.5.5zm3 8V5.5a.5.5 0 0 0-.5-.5H4a.5.5 0 0 0-.5.5V13a.5.5 0 0 0 .5.5h5.5A.5.5 0 0 0 10 13z"/></svg>';const doneIcon='<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13.78 4.22a.75.75 0 0 1 0 1.06l-6.25 6.25a.75.75 0 0 1-1.06 0L2.22 7.28a.75.75 0 0 1 1.06-1.06L7 9.94l5.72-5.72a.75.75 0 0 1 1.06 0z"/></svg>';const btn=document.createElement('button');btn.type='button';btn.className='copy';btn.innerHTML=copyIcon;btn.setAttribute('aria-label','Copy code to clipboard');btn.setAttribute('title','Copy code to clipboard');btn.addEventListener('click',async()=>{{try{{await navigator.clipboard.writeText(getText());btn.innerHTML=doneIcon;btn.classList.add('copied');btn.setAttribute('title','Copied');setTimeout(()=>{{btn.innerHTML=copyIcon;btn.classList.remove('copied');btn.setAttribute('title','Copy code to clipboard');}},1400);}}catch{{btn.classList.add('failed');btn.setAttribute('title','Copy failed');setTimeout(()=>{{btn.classList.remove('failed');btn.setAttribute('title','Copy code to clipboard');}},1400);}}}});target.appendChild(btn);}}
document.querySelectorAll('.doc pre').forEach(pre=>attachCopy(pre,()=>pre.querySelector('code')?.textContent??''));
const tocLinks=document.querySelectorAll('.toc a');
if(tocLinks.length){{const map=new Map();tocLinks.forEach(a=>{{const id=a.getAttribute('href')?.slice(1);const el=id?document.getElementById(id):null;if(el)map.set(el,a);}});const setActive=l=>{{tocLinks.forEach(x=>x.classList.remove('active'));l.classList.add('active');}};const obs=new IntersectionObserver(entries=>{{const visible=entries.filter(e=>e.isIntersecting).sort((a,b)=>a.boundingClientRect.top-b.boundingClientRect.top);if(visible.length){{const link=map.get(visible[0].target);if(link)setActive(link);}}}},{{rootMargin:'-15% 0px -65% 0px',threshold:0}});map.forEach((_,el)=>obs.observe(el));}}
  </script>
</body>
</html>
"""


def write_redirect(alias: Path, target: str) -> None:
    target_href = relative_url(alias, target)
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.write_text(
        f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="refresh" content="0; url={html.escape(target_href, quote=True)}" />
  <link rel="canonical" href="{html.escape(target_href, quote=True)}" />
</head>
<body></body>
</html>
""",
        encoding="utf-8",
    )


def copy_static_assets() -> None:
    if DOCS_OUT.exists():
        shutil.rmtree(DOCS_OUT)
    DOCS_OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "docs-src" / "docs.css", DOCS_OUT / "docs.css")
    assets_src = ROOT / "docs-src" / "assets"
    if assets_src.exists():
        shutil.copytree(assets_src, DOCS_OUT / "assets")


def build() -> None:
    ensure_syntax_highlighter()
    pages = load_pages()
    copy_static_assets()

    for index, page in enumerate(pages):
        nav_html = build_global_nav(pages, page)
        output_path = DOCS_OUT / page.meta.output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(build_page(page, pages, index, nav_html), encoding="utf-8")
        for alias in page.meta.aliases:
            write_redirect(DOCS_OUT / alias, page_permalink(page.meta))


if __name__ == "__main__":
    build()
