"""Unit tests for the RSS connector."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
# pyright: reportOptionalMemberAccess=false
# pyright: reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownMemberType=false
import logging
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import feedparser  # type: ignore[import-untyped]
import pytest
from agentgraph_connector_rss import (
    FeedAuthor,
    RssConnector,
    _feed_id,
    _fetch_feed,
    _hash_ref,
    _parse_authors,
    _parse_feed,
    derive_observation_url_patterns,
    normalise_article_url,
)
from agentgraph_connector_rss.auth import (
    OpmlFeed,
    RssConfig,
    _checkbox_select_opml_feeds,
    add_feed_urls,
    import_opml_feeds,
    load_rss_config,
    parse_opml_feeds,
    preview_feed,
    remove_feed_urls,
    resolve_feed_source,
    save_rss_config,
    select_opml_feeds,
    verify_rss_auth,
)
from agentgraph_connector_web.http import HttpFetchResult

from agentgraph.connectors.base import EntityBatch, EntityMetadataPatch, EntityRecord
from agentgraph.core.context import set_backend


class _ParsedFeed:
    version = "rss20"
    bozo = False
    bozo_exception = None
    feed = {"title": "Example Feed"}
    entries: list[dict[str, str]] = []


class _ParsedNonFeed:
    version = ""
    bozo = False
    bozo_exception = None
    feed: dict[str, str] = {}
    entries: list[dict[str, str]] = []


def _stub_auth_feed_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.fetch_http_resource",
        AsyncMock(
            return_value=HttpFetchResult(
                url="https://example.com/feed.xml",
                status_code=200,
                headers={"content-type": "application/rss+xml"},
                content=b"feed",
            )
        ),
    )


def test_rss_can_handle_configured_feed_urls() -> None:
    connector = RssConnector()

    with patch(
        "agentgraph_connector_rss.load_rss_settings",
        return_value=RssConfig(feed_urls=["https://example.com/feed.xml"]),
    ):
        assert connector.can_handle("https://example.com/feed.xml")
        assert not connector.can_handle("http://example.com/rss")


def test_rss_can_handle_returns_false_without_config() -> None:
    connector = RssConnector()

    with patch("agentgraph_connector_rss.load_rss_settings", side_effect=RuntimeError("missing")):
        assert not connector.can_handle("https://example.com/feed.xml")
    assert not connector.can_handle("file:///tmp/feed.xml")


def test_normalise_article_url_removes_fragments_trailing_slashes_and_tracking() -> None:
    assert (
        normalise_article_url(
            "HTTPS://Example.COM/posts/one/?z=last&utm_source=feed&keep=value#section"
        )
        == "https://example.com/posts/one?keep=value&z=last"
    )


def test_derive_observation_patterns_remove_subsumed_specific_paths() -> None:
    patterns = derive_observation_url_patterns(
        {
            "https://example.com/feed.xml": [
                "https://example.com/articles/2026/first",
                "https://example.com/articles/2026/second",
                "https://example.com/articles/2025/third",
                "https://example.com/about",
            ]
        }
    )

    assert patterns == ["https://example.com/articles/*"]
    assert "https://example.com/*" not in patterns


@pytest.mark.asyncio
async def test_rss_observation_patterns_use_a_bounded_recent_entry_set() -> None:
    feed_url = "https://example.com/feed.xml"
    feed_entity_id = f"feed/{_feed_id(feed_url)}"
    backend = MagicMock()
    backend.get_entity_by_platform = AsyncMock(
        return_value={
            "content": """<?xml version="1.0"?>
            <rss version="2.0"><channel>
              <item><link>https://example.com/articles/first</link></item>
              <item><link>https://example.com/articles/second</link></item>
            </channel></rss>"""
        }
    )
    set_backend(backend)

    with patch(
        "agentgraph_connector_rss.load_rss_settings",
        return_value=RssConfig(feed_urls=[feed_url]),
    ):
        patterns = await RssConnector().observation_url_patterns()

    assert patterns == ["https://example.com/articles/*"]
    backend.get_entity_by_platform.assert_awaited_once_with("rss", feed_entity_id)


@pytest.mark.asyncio
async def test_rss_observation_patterns_read_current_feed_content_on_every_request() -> None:
    feed_url = "https://example.com/feed.xml"
    feed_entity_id = f"feed/{_feed_id(feed_url)}"
    backend = MagicMock()
    backend.get_entity_by_platform = AsyncMock(
        side_effect=[
            {"content": "<rss><channel><item><link>https://example.com/first</link></item></channel></rss>"},
            {"content": "<rss><channel><item><link>https://other.example/second</link></item></channel></rss>"},
        ]
    )
    set_backend(backend)

    with patch(
        "agentgraph_connector_rss.load_rss_settings",
        return_value=RssConfig(feed_urls=[feed_url]),
    ):
        connector = RssConnector()
        assert await connector.observation_url_patterns() == ["https://example.com/*"]
        assert await connector.observation_url_patterns() == ["https://other.example/*"]

    assert backend.get_entity_by_platform.await_count == 2
    backend.get_entity_by_platform.assert_any_await("rss", feed_entity_id)


@pytest.mark.asyncio
async def test_rss_observation_resolution_requires_an_exact_known_entry() -> None:
    known_entry = {
        "platform_entity_id": "entry/known",
        "metadata": {
            "feed_url": "https://example.com/feed.xml",
            "link": "https://example.com/articles/known",
            "web_url": "https://example.com/articles/known",
            "http_etag": '"cached"',
        },
    }
    backend = MagicMock()
    backend.query_by_filter = AsyncMock(return_value=[known_entry])
    set_backend(backend)

    ref = await RssConnector().resolve_observation_url(
        "https://example.com/articles/known/?utm_source=reader#top"
    )

    assert ref is not None
    assert ref.resource_id == "entry/known"
    assert ref.fetch_meta == known_entry["metadata"]
    backend.query_by_filter.assert_awaited_once_with(
        "Document",
        {"platform": "rss", "web_url": "https://example.com/articles/known"},
        1,
        "updated_at",
        None,
        None,
    )


@pytest.mark.asyncio
async def test_rss_observation_resolution_ignores_unknown_matching_prefix_url() -> None:
    backend = MagicMock()
    backend.query_by_filter = AsyncMock(return_value=[])
    set_backend(backend)

    ref = await RssConnector().resolve_observation_url("https://example.com/articles/not-indexed")

    assert ref is None


@pytest.mark.asyncio
async def test_rss_observation_resolution_ignores_configured_feed_url() -> None:
    feed_url = "https://example.com/feed.xml"
    backend = MagicMock()
    backend.query_by_filter = AsyncMock(return_value=[])
    set_backend(backend)

    with patch(
        "agentgraph_connector_rss.load_rss_settings",
        return_value=RssConfig(feed_urls=[feed_url]),
    ):
        ref = await RssConnector().resolve_observation_url(feed_url)

    assert ref is None
    backend.query_by_filter.assert_awaited_once_with(
        "Document",
        {"platform": "rss", "web_url": feed_url},
        1,
        "updated_at",
        None,
        None,
    )


def test_rss_config_roundtrip_uses_config_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setattr("agentgraph.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("agentgraph.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("agentgraph.config.CONFIG_YAML_FILE", tmp_path / "config.yaml")

    save_rss_config(
        RssConfig(
            feed_urls=["https://example.com/feed.xml"],
        )
    )

    assert load_rss_config() == {"feed_urls": ["https://example.com/feed.xml"]}
    rendered = config_file.read_text(encoding="utf-8")
    assert "[connectors.rss]" in rendered
    assert not rendered.startswith("\n")
    assert "[[connectors.rss.accounts]]" not in rendered
    assert 'feed_urls = [\n  "https://example.com/feed.xml",\n]' in rendered


def test_save_rss_config_replaces_unmanaged_toml_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """[connectors.rss]
default_account_id = "rss"
feed_urls = ["https://example.com/old.xml"]

# BEGIN AgentGraph managed web config
[connectors.web]
observation_urls = ["https://example.com/*"]
# END AgentGraph managed web config
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("agentgraph.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("agentgraph.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("agentgraph.config.CONFIG_YAML_FILE", tmp_path / "config.yaml")

    save_rss_config(RssConfig(feed_urls=["https://example.com/new.xml"]))

    rendered = config_file.read_text(encoding="utf-8")
    parsed = tomllib.loads(rendered)
    assert parsed["connectors"]["rss"] == {"feed_urls": ["https://example.com/new.xml"]}
    assert parsed["connectors"]["web"] == {"observation_urls": ["https://example.com/*"]}
    assert rendered.count("[connectors.rss]") == 1
    assert "# BEGIN AgentGraph managed web config" in rendered
    assert "# END AgentGraph managed web config" in rendered


def test_load_rss_config_recovers_duplicate_toml_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """[connectors.rss]
feed_urls = ["https://example.com/old.xml"]

[connectors.web]
observation_urls = ["https://example.com/*"]

[connectors.rss]
feed_urls = ["https://example.com/new.xml"]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("agentgraph.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("agentgraph.config.CONFIG_YAML_FILE", tmp_path / "config.yaml")

    assert load_rss_config() == {
        "feed_urls": ["https://example.com/old.xml", "https://example.com/new.xml"]
    }


def test_rss_config_roundtrip_uses_config_yaml_when_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "config.toml"
    config_yaml_file = tmp_path / "config.yaml"
    config_yaml_file.write_text("server:\n  host: 127.0.0.1\n", encoding="utf-8")
    monkeypatch.setattr("agentgraph.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("agentgraph.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("agentgraph.config.CONFIG_YAML_FILE", config_yaml_file)

    save_rss_config(
        RssConfig(
            feed_urls=["https://example.com/feed.xml"],
        )
    )

    assert load_rss_config() == {"feed_urls": ["https://example.com/feed.xml"]}
    rendered = config_yaml_file.read_text(encoding="utf-8")
    assert "connectors:" in rendered
    assert "rss:" in rendered
    assert "accounts:" not in rendered
    assert not config_file.exists()


def test_rss_config_prefers_yaml_over_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "config.toml"
    config_yaml_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
[connectors.rss]
feed_urls = ["https://example.com/toml.xml"]
""",
        encoding="utf-8",
    )
    config_yaml_file.write_text(
        """
connectors:
  rss:
    feed_urls:
      - https://example.com/yaml.xml
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("agentgraph.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("agentgraph.config.CONFIG_YAML_FILE", config_yaml_file)

    assert load_rss_config() == {"feed_urls": ["https://example.com/yaml.xml"]}


def test_add_feed_urls_creates_rss_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: dict[str, object] = {}

    monkeypatch.setattr(feedparser, "parse", lambda source: _ParsedFeed())
    _stub_auth_feed_fetch(monkeypatch)
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.load_rss_settings",
        lambda account_id=None: (_ for _ in ()).throw(RuntimeError("missing")),
    )
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.save_rss_config",
        lambda data: saved.update({"data": data.model_dump(mode="json")}),
    )

    config = add_feed_urls(["https://example.com/feed.xml"])

    assert config.feed_urls == ["https://example.com/feed.xml"]
    assert saved["data"] == {
        "feed_urls": ["https://example.com/feed.xml"],
    }


def test_add_feed_urls_rejects_invalid_feed_before_saving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: dict[str, object] = {}

    monkeypatch.setattr(feedparser, "parse", lambda source: _ParsedNonFeed())
    _stub_auth_feed_fetch(monkeypatch)
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.load_rss_settings",
        lambda account_id=None: (_ for _ in ()).throw(RuntimeError("missing")),
    )
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.save_rss_config",
        lambda data: saved.update({"data": data.model_dump(mode="json")}),
    )

    with pytest.raises(ValueError, match="Not a valid RSS/Atom feed"):
        add_feed_urls(["https://example.com/not-a-feed"])

    assert saved == {}


def test_remove_feed_urls_updates_existing_rss_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: dict[str, object] = {}
    fetch = AsyncMock()

    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.load_rss_settings",
        lambda account_id=None: RssConfig(
            feed_urls=[
                "https://example.com/one.xml",
                "https://example.com/two.xml",
            ],
        ),
    )
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.save_rss_config",
        lambda data: saved.update({"data": data.model_dump(mode="json")}),
    )
    monkeypatch.setattr("agentgraph_connector_rss.auth.fetch_http_resource", fetch)

    config, removed = remove_feed_urls(["https://example.com/two.xml"])

    assert removed == ["https://example.com/two.xml"]
    assert config.feed_urls == ["https://example.com/one.xml"]
    assert saved == {
        "data": {
            "feed_urls": ["https://example.com/one.xml"],
        },
    }
    fetch.assert_not_awaited()


def test_remove_feed_urls_rejects_unconfigured_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.load_rss_settings",
        lambda account_id=None: RssConfig(
            feed_urls=["https://example.com/one.xml"],
        ),
    )
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.resolve_feed_source",
        lambda source: source,
    )

    with pytest.raises(ValueError, match="No matching RSS feed URLs"):
        remove_feed_urls(["https://example.com/missing.xml"])


def test_remove_feed_urls_discovers_configured_feed_from_html_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: dict[str, object] = {}
    fetch = AsyncMock(
        side_effect=[
            HttpFetchResult(
                url="https://example.com/blog/",
                status_code=200,
                headers={"content-type": "text/html"},
                content=b"<link rel=\"alternate\" type=\"application/rss+xml\" href=\"/feed.xml\">",
            ),
            HttpFetchResult(
                url="https://example.com/feed.xml",
                status_code=200,
                headers={"content-type": "application/rss+xml"},
                content=b"<rss version=\"2.0\"><channel><title>Example</title></channel></rss>",
            ),
        ]
    )
    monkeypatch.setattr("agentgraph_connector_rss.auth.fetch_http_resource", fetch)
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.load_rss_settings",
        lambda account_id=None: RssConfig(feed_urls=["https://example.com/feed.xml"]),
    )
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.save_rss_config",
        lambda data: saved.update({"data": data.model_dump(mode="json")}),
    )

    config, removed = remove_feed_urls(["https://example.com/blog"])

    assert removed == ["https://example.com/feed.xml"]
    assert config.feed_urls == []
    assert saved == {"data": {"feed_urls": []}}


def test_resolve_feed_source_discovers_rss_alternate_link_from_html_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html_path = tmp_path / "index.html"
    html_path.write_text(
        """<!doctype html>
<html>
  <head>
    <link rel="alternate" type="application/rss+xml" href="https://example.com/feed.xml">
  </head>
</html>
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.fetch_http_resource",
        AsyncMock(
            return_value=HttpFetchResult(
                url="https://example.com/feed.xml",
                status_code=200,
                headers={"content-type": "application/rss+xml"},
                content=b"<rss version=\"2.0\"><channel><title>Example</title></channel></rss>",
            )
        ),
    )

    assert resolve_feed_source(str(html_path)) == "https://example.com/feed.xml"


def test_resolve_feed_source_discovers_first_atom_or_rss_alternate_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        HttpFetchResult(
            url="https://www.example.com/blog/",
            status_code=200,
            headers={"content-type": "text/html"},
            content=b"""<html><head>
<link rel="alternate" type="application/atom+xml" href="feeds/first.atom">
<link rel="alternate" type="application/rss+xml" href="/feeds/second.xml">
</head></html>""",
        ),
        HttpFetchResult(
            url="https://www.example.com/blog/feeds/first.atom",
            status_code=200,
            headers={"content-type": "application/atom+xml"},
            content=b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<title>Example</title><entry><title>One</title><id>one</id></entry></feed>""",
        ),
    ]
    fetch = AsyncMock(side_effect=responses)
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.fetch_http_resource",
        fetch,
    )

    assert resolve_feed_source("https://example.com/redirected-blog") == (
        "https://www.example.com/blog/feeds/first.atom"
    )
    assert fetch.await_count == 2


def test_rss_connector_add_saves_discovered_feed_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: dict[str, object] = {}
    fetch = AsyncMock(
        side_effect=[
            HttpFetchResult(
                url="https://example.com/",
                status_code=200,
                headers={"content-type": "text/html"},
                content=b"""<link rel="alternate" type="application/rss+xml" href="feed.xml">""",
            ),
            HttpFetchResult(
                url="https://example.com/feed.xml",
                status_code=200,
                headers={"content-type": "application/rss+xml"},
                content=b"<rss version=\"2.0\"><channel><title>Example</title></channel></rss>",
            ),
        ]
    )
    monkeypatch.setattr("agentgraph_connector_rss.auth.fetch_http_resource", fetch)
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.load_rss_settings",
        lambda account_id=None: (_ for _ in ()).throw(RuntimeError("missing")),
    )
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.save_rss_config",
        lambda data: saved.update({"data": data.model_dump(mode="json")}),
    )

    result = RssConnector.run_cli_command(["add", "https://example.com/home"])

    assert result["added"] == ["https://example.com/feed.xml"]
    assert saved == {"data": {"feed_urls": ["https://example.com/feed.xml"]}}


def test_rss_connector_add_rejects_html_without_valid_alternate_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: dict[str, object] = {}
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.fetch_http_resource",
        AsyncMock(
            return_value=HttpFetchResult(
                url="https://example.com/",
                status_code=200,
                headers={"content-type": "text/html"},
                content=b"<html><head><title>No feed</title></head></html>",
            )
        ),
    )
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.load_rss_settings",
        lambda account_id=None: (_ for _ in ()).throw(RuntimeError("missing")),
    )
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.save_rss_config",
        lambda data: saved.update({"data": data.model_dump(mode="json")}),
    )

    with pytest.raises(ValueError, match="Not a valid RSS/Atom feed"):
        RssConnector.run_cli_command(["add", "https://example.com/home"])

    assert saved == {}


@pytest.mark.parametrize("command", ["add", "import-opml"])
def test_rss_connector_requests_poll_after_commands_that_add_feeds(command: str) -> None:
    effects = RssConnector.command_effects([command], {"status": "ok"})

    assert effects.poll is True


def test_rss_connector_does_not_request_poll_after_remove() -> None:
    effects = RssConnector.command_effects(["remove"], {"status": "ok"})

    assert effects.poll is False


def test_rss_connector_formats_poll_status() -> None:
    rendered = RssConnector.format_cli_result(
        {
            "status": "ok",
            "feed_urls": ["https://example.com/feed.xml"],
            "added": ["https://example.com/feed.xml"],
            "poll": {"source": "rss", "status": "already_running", "reason": None},
        }
    )

    assert "Poll: already running." in rendered


def test_rss_connector_remove_reports_removed_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: dict[str, object] = {}

    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.load_rss_settings",
        lambda account_id=None: RssConfig(
            feed_urls=[
                "https://example.com/one.xml",
                "https://example.com/two.xml",
            ],
        ),
    )
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.save_rss_config",
        lambda data: saved.update({"data": data.model_dump(mode="json")}),
    )

    result = RssConnector.run_cli_command(["remove", "https://example.com/two.xml"])

    assert result["removed"] == ["https://example.com/two.xml"]
    assert result["feed_urls"] == ["https://example.com/one.xml"]
    assert saved["data"] == {"feed_urls": ["https://example.com/one.xml"]}


def test_rss_connector_remove_requests_feed_folder_deletion() -> None:
    feed_url = "https://example.com/two.xml"

    effects = RssConnector.command_effects(
        ["remove", feed_url],
        {"status": "ok", "removed": [feed_url]},
    )

    assert [(item.platform, item.platform_entity_id) for item in effects.delete_entities] == [
        ("rss", f"feed/{_feed_id(feed_url)}")
    ]


def test_parse_opml_feeds_deduplicates_feed_urls(tmp_path: Path) -> None:
    opml_path = tmp_path / "feeds.opml"
    opml_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <body>
    <outline text="Tech">
      <outline text="Example One" title="Example One" type="rss" xmlUrl="https://example.com/feed.xml" htmlUrl="https://example.com/" />
      <outline text="Example Two" type="rss" xmlUrl="https://example.com/two.xml" />
      <outline text="Duplicate" type="rss" xmlUrl="https://example.com/feed.xml" />
    </outline>
  </body>
</opml>
""",
        encoding="utf-8",
    )

    feeds = parse_opml_feeds(opml_path)

    assert [feed.title for feed in feeds] == ["Example One", "Example Two"]
    assert [feed.feed_url for feed in feeds] == [
        "https://example.com/feed.xml",
        "https://example.com/two.xml",
    ]
    assert feeds[0].html_url == "https://example.com/"


def test_parse_opml_feeds_accepts_file_uri(tmp_path: Path) -> None:
    opml_path = tmp_path / "feeds with spaces.opml"
    opml_path.write_text(
        """<opml version="2.0"><body>
  <outline text="Example" xmlUrl="https://example.com/feed.xml" />
</body></opml>""",
        encoding="utf-8",
    )

    feeds = parse_opml_feeds(opml_path.as_uri())

    assert [feed.feed_url for feed in feeds] == ["https://example.com/feed.xml"]


def test_select_opml_feeds_supports_indexes_and_ranges(tmp_path: Path) -> None:
    opml_path = tmp_path / "feeds.opml"
    opml_path.write_text(
        """<opml version="2.0"><body>
  <outline text="One" xmlUrl="https://example.com/one.xml" />
  <outline text="Two" xmlUrl="https://example.com/two.xml" />
  <outline text="Three" xmlUrl="https://example.com/three.xml" />
</body></opml>""",
        encoding="utf-8",
    )
    feeds = parse_opml_feeds(opml_path)

    selected = select_opml_feeds(feeds, selection="1,3")
    ranged = select_opml_feeds(feeds, selection="2-3")

    assert [feed.feed_url for feed in selected] == [
        "https://example.com/one.xml",
        "https://example.com/three.xml",
    ]
    assert [feed.feed_url for feed in ranged] == [
        "https://example.com/two.xml",
        "https://example.com/three.xml",
    ]


def test_checkbox_select_opml_feeds_uses_questionary(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeChoice:
        def __init__(self, *, title: str, value: str, checked: bool) -> None:
            self.title = title
            self.value = value
            self.checked = checked

    class _FakeQuestion:
        def ask(self) -> list[str]:
            return ["https://example.com/two.xml"]

    def fake_checkbox(prompt: str, *, choices: list[_FakeChoice]) -> _FakeQuestion:
        assert "2 found" in prompt
        assert [choice.checked for choice in choices] == [False, True]
        return _FakeQuestion()

    questionary = ModuleType("questionary")
    questionary.__dict__["Choice"] = _FakeChoice
    questionary.__dict__["checkbox"] = fake_checkbox
    monkeypatch.setitem(sys.modules, "questionary", questionary)

    selected = _checkbox_select_opml_feeds(
        [
            OpmlFeed(title="One", feed_url="https://example.com/one.xml"),
            OpmlFeed(title="Two", feed_url="https://example.com/two.xml"),
        ],
        configured_feed_urls=["https://example.com/two.xml"],
    )

    assert selected == [OpmlFeed(title="Two", feed_url="https://example.com/two.xml")]


def test_import_opml_prompt_uses_existing_rss_feed_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opml_path = tmp_path / "feeds.opml"
    opml_path.write_text(
        """<opml version="2.0"><body>
  <outline text="One" xmlUrl="https://example.com/one.xml" />
  <outline text="Two" xmlUrl="https://example.com/two.xml" />
</body></opml>""",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(feedparser, "parse", lambda source: _ParsedFeed())
    _stub_auth_feed_fetch(monkeypatch)

    def fake_select(
        feeds: list[OpmlFeed],
        *,
        include_all: bool = False,
        selection: str | None = None,
        configured_feed_urls: list[str] | None = None,
    ) -> list[OpmlFeed]:
        captured["configured_feed_urls"] = configured_feed_urls
        return [feeds[0]]

    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.load_rss_settings",
        lambda account_id=None: RssConfig(
            feed_urls=["https://example.com/two.xml"],
        ),
    )
    monkeypatch.setattr("agentgraph_connector_rss.auth.select_opml_feeds", fake_select)
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.save_rss_config",
        lambda data: None,
    )

    import_opml_feeds(opml_path)

    assert captured["configured_feed_urls"] == ["https://example.com/two.xml"]


def test_rss_connector_import_opml_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opml_path = tmp_path / "feeds.opml"
    opml_path.write_text(
        """<opml version="2.0"><body>
  <outline text="One" xmlUrl="https://example.com/one.xml" />
  <outline text="Two" xmlUrl="https://example.com/two.xml" />
</body></opml>""",
        encoding="utf-8",
    )
    saved: dict[str, object] = {}

    monkeypatch.setattr(feedparser, "parse", lambda source: _ParsedFeed())
    _stub_auth_feed_fetch(monkeypatch)
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.load_rss_settings",
        lambda account_id=None: (_ for _ in ()).throw(RuntimeError("missing")),
    )
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.save_rss_config",
        lambda data: saved.update({"data": data.model_dump(mode="json")}),
    )

    result = RssConnector.run_cli_command(["import-opml", str(opml_path), "--all"])

    assert result["status"] == "ok"
    assert result["imported_feed_count"] == 2
    assert result["selected_feed_count"] == 2
    assert result["added"] == [
        "https://example.com/one.xml",
        "https://example.com/two.xml",
    ]
    assert saved["data"] == {
        "feed_urls": ["https://example.com/one.xml", "https://example.com/two.xml"],
    }


def test_rss_connector_import_opml_select(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opml_path = tmp_path / "feeds.opml"
    opml_path.write_text(
        """<opml version="2.0"><body>
  <outline text="One" xmlUrl="https://example.com/one.xml" />
  <outline text="Two" xmlUrl="https://example.com/two.xml" />
</body></opml>""",
        encoding="utf-8",
    )
    saved: dict[str, object] = {}

    monkeypatch.setattr(feedparser, "parse", lambda source: _ParsedFeed())
    _stub_auth_feed_fetch(monkeypatch)
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.load_rss_settings",
        lambda account_id=None: (_ for _ in ()).throw(RuntimeError("missing")),
    )
    monkeypatch.setattr(
        "agentgraph_connector_rss.auth.save_rss_config",
        lambda data: saved.update({"data": data.model_dump(mode="json")}),
    )

    result = RssConnector.run_cli_command(["import-opml", str(opml_path), "--select", "2"])

    assert result["selected_feed_count"] == 1
    assert result["added"] == ["https://example.com/two.xml"]
    assert saved["data"] == {
        "feed_urls": ["https://example.com/two.xml"],
    }


@pytest.mark.asyncio
async def test_verify_rss_auth_missing() -> None:
    with patch(
        "agentgraph_connector_rss.auth.load_rss_settings", side_effect=RuntimeError("missing")
    ):
        assert await verify_rss_auth() == ("missing", None)


@pytest.mark.asyncio
async def test_verify_rss_auth_requires_feed_urls() -> None:
    with patch(
        "agentgraph_connector_rss.auth.load_rss_settings",
        return_value=RssConfig(feed_urls=[]),
    ):
        assert await verify_rss_auth() == ("invalid", "No RSS feed URLs configured")


@pytest.mark.asyncio
async def test_verify_rss_auth_uses_feed_preview() -> None:
    with (
        patch(
            "agentgraph_connector_rss.auth.load_rss_settings",
            return_value=RssConfig(feed_urls=["https://example.com/feed.xml"]),
        ),
        patch(
            "agentgraph_connector_rss.auth.preview_feed",
            return_value={"entries": [{"title": "One"}], "bozo": False},
        ) as preview,
    ):
        status = await verify_rss_auth("rss-main")

    assert status == ("ok", "1 feed(s), sample returned 1 article(s)")
    preview.assert_awaited_once_with("https://example.com/feed.xml", count=1)


@pytest.mark.asyncio
async def test_fetch_feed_maps_entries_to_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Parsed:
        bozo = False
        feed = {"title": "Example Feed"}
        entries = [
            {
                "id": "post-1",
                "title": "First Post",
                "link": "https://example.com/first",
                "summary": "A short summary",
                "published": "Fri, 05 Jun 2026 10:00:00 GMT",
                "author": "Author Name",
            }
        ]

    async def fake_parse_feed(feed_url: str) -> _Parsed:
        assert feed_url == "https://example.com/feed.xml"
        return _Parsed()

    monkeypatch.setattr("agentgraph_connector_rss._parse_feed", fake_parse_feed)

    batch = await _fetch_feed("https://example.com/feed.xml")

    assert len(batch.entities) == 2
    feed = batch.entities[0]
    entry = batch.entities[1]
    assert feed.entity_type == "Folder"
    assert feed.title == "Example Feed"
    assert feed.metadata["web_url"] == "https://example.com/feed.xml"
    assert entry.entity_type == "Document"
    assert entry.platform == "rss"
    assert entry.title == "First Post"
    assert entry.content and "A short summary" in entry.content
    assert entry.metadata["link"] == "https://example.com/first"
    assert entry.metadata["web_url"] == "https://example.com/first"
    assert entry.metadata["author"] == "Author Name"
    assert batch.edges[0].edge_type == "posted_in"


@pytest.mark.asyncio
async def test_parse_feed_uses_shared_web_http_fetcher(monkeypatch: pytest.MonkeyPatch) -> None:
    response = HttpFetchResult(
        url="https://example.com/feed.xml",
        status_code=200,
        headers={"content-type": "application/rss+xml"},
        content=b"""<?xml version=\"1.0\"?>
<rss version=\"2.0\"><channel><title>Example</title><item><title>One</title></item></channel></rss>""",
    )
    fetch = AsyncMock(return_value=response)
    monkeypatch.setattr("agentgraph_connector_rss.fetch_http_resource", fetch)

    parsed = await _parse_feed("https://example.com/feed.xml")

    assert parsed.feed.title == "Example"
    assert "<rss version=\"2.0\">" in parsed.raw_content
    fetch.assert_awaited_once()
    assert fetch.await_args.kwargs["max_bytes"] == 5_000_000


@pytest.mark.asyncio
async def test_fetch_feed_persists_raw_feed_payload_without_indexing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel><title>Example</title>
    <item><title>One</title><link>https://example.com/one</link></item>
    </channel></rss>"""
    monkeypatch.setattr(
        "agentgraph_connector_rss.fetch_http_resource",
        AsyncMock(
            return_value=HttpFetchResult(
                url="https://example.com/feed.xml",
                status_code=200,
                headers={"content-type": "application/rss+xml"},
                content=content,
            )
        ),
    )

    batch = await _fetch_feed(
        "https://example.com/feed.xml",
        persist_feed_payload_only=True,
    )

    feed = batch.entities[0]
    assert len(batch.entities) == 1
    assert feed.entity_type == "Folder"
    assert feed.content == content.decode("utf-8")
    assert feed.content_searchable is False


def test_resolve_feed_source_uses_shared_web_http_fetcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = HttpFetchResult(
        url="https://example.com/feed.xml",
        status_code=200,
        headers={"content-type": "application/rss+xml"},
        content=b"""<?xml version=\"1.0\"?>
<rss version=\"2.0\"><channel><title>Example</title></channel></rss>""",
    )
    fetch = AsyncMock(return_value=response)
    monkeypatch.setattr("agentgraph_connector_rss.auth.fetch_http_resource", fetch)

    assert resolve_feed_source("https://example.com/feed.xml") == "https://example.com/feed.xml"
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_preview_feed_uses_shared_web_http_fetcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = HttpFetchResult(
        url="https://example.com/feed.xml",
        status_code=200,
        headers={"content-type": "application/rss+xml"},
        content=b"""<?xml version=\"1.0\"?>
<rss version=\"2.0\"><channel><title>Example</title><item><title>One</title>
<link>https://example.com/one</link></item></channel></rss>""",
    )
    fetch = AsyncMock(return_value=response)
    monkeypatch.setattr("agentgraph_connector_rss.auth.fetch_http_resource", fetch)

    preview = await preview_feed("https://example.com/feed.xml")

    assert preview["title"] == "Example"
    assert preview["entries"] == [{"title": "One", "link": "https://example.com/one"}]
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_rss_fetch_feed_hydrates_linked_articles() -> None:
    batch = EntityBatch()

    with patch(
        "agentgraph_connector_rss._fetch_feed",
        new=AsyncMock(return_value=batch),
    ) as fetch_feed:
        result = await RssConnector().fetch(
            "folder",
            "https://example.com/feed.xml",
        )

    assert result == batch
    fetch_feed.assert_awaited_once_with(
        "https://example.com/feed.xml",
        hydrate_documents=True,
        new_documents_only=True,
    )


@pytest.mark.asyncio
async def test_rss_fetch_folder_uses_stored_feed_url() -> None:
    batch = EntityBatch()

    with patch(
        "agentgraph_connector_rss._fetch_feed",
        new=AsyncMock(return_value=batch),
    ) as fetch_feed:
        result = await RssConnector().fetch(
            "folder",
            "feed/73c63ac29e5d1dc80f240d99",
            meta={"feed_url": "http://stratechery.com/feed/"},
        )

    assert result is batch
    fetch_feed.assert_awaited_once_with(
        "http://stratechery.com/feed/",
        hydrate_documents=True,
        new_documents_only=True,
    )


def test_parse_authors_reads_atom_name_and_email() -> None:
    authors = _parse_authors({"authors": [{"name": "Ada Lovelace", "email": "Ada@Example.com"}]})

    assert authors == [
        _author("ada@example.com", display_name="Ada Lovelace", email="ada@example.com")
    ]


def test_parse_authors_reads_bare_rss_email_author() -> None:
    authors = _parse_authors({"authors": [{"email": "solo@example.com"}]})

    assert authors == [_author("solo@example.com", display_name=None, email="solo@example.com")]


def test_parse_authors_treats_email_only_name_as_an_email() -> None:
    authors = _parse_authors({"author_detail": {"name": "solo@example.com"}})

    assert authors == [_author("solo@example.com", display_name=None, email="solo@example.com")]


def test_parse_authors_keys_name_only_authors_by_name() -> None:
    authors = _parse_authors({"author": "  Matt Ridley  "})

    assert authors == [_author("Matt Ridley", display_name="Matt Ridley", email=None)]


def test_parse_authors_deduplicates_and_skips_empty_entries() -> None:
    authors = _parse_authors(
        {
            "authors": [
                {"name": "Matt Ridley"},
                {"name": ""},
                {"name": "Matt Ridley"},
                {"name": "Stephen McBride"},
            ]
        }
    )

    assert [author.user_id for author in authors] == ["Matt Ridley", "Stephen McBride"]


def test_parse_authors_returns_empty_without_author_metadata() -> None:
    assert _parse_authors({"title": "No author here"}) == []


@pytest.mark.asyncio
async def test_fetch_feed_creates_persons_and_authored_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Parsed:
        bozo = False
        feed = {"title": "Rational Optimist Society"}
        entries = [
            {
                "id": "post-1",
                "title": "Wildfires",
                "link": "https://example.com/wildfires",
                "authors": [{"name": "Matt Ridley"}],
            },
            {
                "id": "post-2",
                "title": "Jet fuel",
                "link": "https://example.com/jet-fuel",
                "authors": [{"name": "Matt Ridley"}, {"name": "Stephen McBride"}],
            },
        ]

    async def fake_parse_feed(feed_url: str) -> _Parsed:
        _ = feed_url
        return _Parsed()

    monkeypatch.setattr("agentgraph_connector_rss._parse_feed", fake_parse_feed)

    batch = await _fetch_feed("https://example.com/feed.xml")

    assert [
        (person.platform, person.platform_user_id, person.display_name) for person in batch.persons
    ] == [
        ("rss", "Matt Ridley", "Matt Ridley"),
        ("rss", "Stephen McBride", "Stephen McBride"),
    ]
    assert all(person.canonical_email is None for person in batch.persons)

    first, second = batch.entities[1], batch.entities[2]
    assert first.metadata["author"] == "Matt Ridley"
    assert second.metadata["authors"] == "Matt Ridley, Stephen McBride"

    authored = [edge for edge in batch.edges if edge.edge_type == "authored"]
    assert [
        (edge.source_platform_user_id, edge.target_platform_entity_id) for edge in authored
    ] == [
        ("Matt Ridley", first.platform_entity_id),
        ("Matt Ridley", second.platform_entity_id),
        ("Stephen McBride", second.platform_entity_id),
        ("Matt Ridley", batch.entities[0].platform_entity_id),
        ("Stephen McBride", batch.entities[0].platform_entity_id),
    ]
    assert all(edge.platform == "rss" for edge in authored)


@pytest.mark.asyncio
async def test_fetch_feed_falls_back_to_feed_level_authors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Parsed:
        bozo = False
        feed = {
            "title": "Sam Altman",
            "authors": [{"name": "Sam Altman", "email": "sam@example.com"}],
        }
        entries = [
            {"id": "post-1", "title": "Untitled", "link": "https://example.com/one"},
            {
                "id": "post-2",
                "title": "Guest post",
                "link": "https://example.com/two",
                "author": "Guest Writer",
            },
        ]

    async def fake_parse_feed(feed_url: str) -> _Parsed:
        _ = feed_url
        return _Parsed()

    monkeypatch.setattr("agentgraph_connector_rss._parse_feed", fake_parse_feed)

    batch = await _fetch_feed("https://example.com/feed.xml")

    assert [person.platform_user_id for person in batch.persons] == [
        "sam@example.com",
        "Guest Writer",
    ]
    assert batch.persons[0].canonical_email == "sam@example.com"
    assert batch.entities[1].metadata["author"] == "Sam Altman"
    assert batch.entities[2].metadata["author"] == "Guest Writer"
    folder_id = batch.entities[0].platform_entity_id
    assert {
        edge.source_platform_user_id
        for edge in batch.edges
        if edge.edge_type == "authored" and edge.target_platform_entity_id == folder_id
    } == {"sam@example.com", "Guest Writer"}


@pytest.mark.asyncio
async def test_fetch_feed_without_authors_creates_no_persons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Parsed:
        bozo = False
        feed = {"title": "Example Feed"}
        entries = [{"id": "post-1", "title": "One", "link": "https://example.com/one"}]

    async def fake_parse_feed(feed_url: str) -> _Parsed:
        _ = feed_url
        return _Parsed()

    monkeypatch.setattr("agentgraph_connector_rss._parse_feed", fake_parse_feed)

    batch = await _fetch_feed("https://example.com/feed.xml")

    assert batch.persons == []
    assert not [edge for edge in batch.edges if edge.edge_type == "authored"]
    assert batch.entities[1].metadata["author"] is None
    assert batch.entities[1].metadata["authors"] is None


def _author(user_id: str, *, display_name: str | None, email: str | None) -> FeedAuthor:
    return FeedAuthor(user_id=user_id, display_name=display_name, email=email)


@pytest.mark.asyncio
async def test_fetch_feed_hydrates_entry_documents_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Parsed:
        bozo = False
        feed = {"title": "Example Feed"}
        entries = [
            {
                "id": "post-1",
                "title": "First Post",
                "link": "https://example.com/first",
                "summary": "A short summary",
                "published": "Fri, 05 Jun 2026 10:00:00 GMT",
            }
        ]

    monkeypatch.setattr("agentgraph_connector_rss._parse_feed", AsyncMock(return_value=_Parsed()))
    backend = MagicMock()
    backend.get_entity_by_platform = AsyncMock(return_value=None)
    set_backend(backend)

    fetched = EntityRecord(
        entity_type="Document",
        platform="web",
        platform_entity_id="https://example.com/first",
        title="Full First Post",
        content="Full article body",
        metadata={
            "web_url": "https://example.com/first",
            "http_etag": '"fresh"',
            "status_code": 200,
        },
    )

    with patch(
        "agentgraph_connector_rss._fetch_http_document", new=AsyncMock(return_value=fetched)
    ) as fetch:
        batch = await _fetch_feed("https://example.com/feed.xml", hydrate_documents=True)

    fetch.assert_awaited_once()
    assert fetch.await_args.args == ("https://example.com/first",)
    assert fetch.await_args.kwargs["existing_entity"] is None
    entry = batch.entities[1]
    assert entry.platform == "rss"
    assert entry.title == "Full First Post"
    assert entry.content == "Full article body"
    assert entry.source_created_at is not None
    assert entry.source_updated_at is None
    assert entry.metadata["feed_url"] == "https://example.com/feed.xml"
    assert entry.metadata["web_url"] == "https://example.com/first"
    assert entry.metadata["http_etag"] == '"fresh"'


@pytest.mark.asyncio
async def test_fetch_feed_new_documents_only_skips_existing_article_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Parsed:
        bozo = False
        feed = {"title": "Example Feed"}
        entries = [
            {
                "id": "post-1",
                "title": "First Post",
                "link": "https://example.com/first",
                "author": "Author One",
            }
        ]

    monkeypatch.setattr("agentgraph_connector_rss._parse_feed", AsyncMock(return_value=_Parsed()))
    backend = MagicMock()
    backend.get_entity_by_platform = AsyncMock(return_value={"id": "existing-entry"})
    set_backend(backend)

    with patch(
        "agentgraph_connector_rss._hydrate_entry_document",
        new=AsyncMock(),
    ) as hydrate:
        batch = await _fetch_feed(
            "https://example.com/feed.xml",
            hydrate_documents=True,
            new_documents_only=True,
        )

    hydrate.assert_not_awaited()
    assert [entity.entity_type for entity in batch.entities] == ["Folder"]
    assert [person.platform_user_id for person in batch.persons] == ["Author One"]
    authored_targets = {
        edge.target_platform_entity_id
        for edge in batch.edges
        if edge.edge_type == "authored"
    }
    assert batch.entities[0].platform_entity_id in authored_targets
    assert len(authored_targets) == 2
    assert any(target and target.startswith("entry/") for target in authored_targets)


@pytest.mark.asyncio
async def test_fetch_feed_skip_existing_urls_omits_duplicate_article_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Parsed:
        bozo = False
        feed = {"title": "Example Feed"}
        entries = [
            {
                "id": "new-entry-id",
                "title": "First Post",
                "link": "https://example.com/first?utm_source=rss",
            }
        ]

    monkeypatch.setattr("agentgraph_connector_rss._parse_feed", AsyncMock(return_value=_Parsed()))
    backend = MagicMock()
    backend.query_by_filter = AsyncMock(return_value=[{"id": "existing-entry"}])
    set_backend(backend)

    with patch(
        "agentgraph_connector_rss._hydrate_entry_document",
        new=AsyncMock(),
    ) as hydrate:
        batch = await _fetch_feed(
            "https://example.com/feed.xml",
            hydrate_documents=True,
            skip_existing_urls=True,
        )

    hydrate.assert_not_awaited()
    backend.query_by_filter.assert_awaited_once_with(
        "Document",
        {"platform": "rss", "link": "https://example.com/first"},
        1,
        "updated_at",
        None,
        None,
    )
    assert [entity.entity_type for entity in batch.entities] == ["Folder"]
    assert batch.edges == []


@pytest.mark.asyncio
async def test_fetch_feed_skip_existing_urls_falls_back_to_canonical_web_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Parsed:
        bozo = False
        feed = {"title": "Example Feed"}
        entries = [
            {
                "id": "new-entry-id",
                "title": "First Post",
                "link": "https://example.com/first",
            }
        ]

    monkeypatch.setattr("agentgraph_connector_rss._parse_feed", AsyncMock(return_value=_Parsed()))
    backend = MagicMock()
    backend.query_by_filter = AsyncMock(side_effect=[[], [{"id": "existing-entry"}]])
    set_backend(backend)

    with patch(
        "agentgraph_connector_rss._hydrate_entry_document",
        new=AsyncMock(),
    ) as hydrate:
        batch = await _fetch_feed(
            "https://example.com/feed.xml",
            hydrate_documents=True,
            skip_existing_urls=True,
        )

    hydrate.assert_not_awaited()
    assert backend.query_by_filter.await_count == 2
    assert [entity.entity_type for entity in batch.entities] == ["Folder"]


@pytest.mark.asyncio
async def test_fetch_feed_skip_existing_urls_hydrates_new_article_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Parsed:
        bozo = False
        feed = {"title": "Example Feed"}
        entries = [
            {
                "id": "new-entry-id",
                "title": "First Post",
                "link": "https://example.com/first",
            }
        ]

    monkeypatch.setattr("agentgraph_connector_rss._parse_feed", AsyncMock(return_value=_Parsed()))
    backend = MagicMock()
    backend.query_by_filter = AsyncMock(return_value=[])
    set_backend(backend)

    async def preserve_entity(entity: EntityRecord) -> EntityRecord:
        return entity

    with patch(
        "agentgraph_connector_rss._hydrate_entry_document",
        new=AsyncMock(side_effect=preserve_entity),
    ) as hydrate:
        batch = await _fetch_feed(
            "https://example.com/feed.xml",
            hydrate_documents=True,
            skip_existing_urls=True,
        )

    hydrate.assert_awaited_once()
    assert backend.query_by_filter.await_count == 2
    assert [entity.entity_type for entity in batch.entities] == ["Folder", "Document"]


@pytest.mark.asyncio
async def test_rss_poll_skips_existing_article_urls() -> None:
    connector = RssConnector()
    batch = EntityBatch()

    with patch.object(connector, "ingest", new=AsyncMock(return_value=batch)) as ingest:
        result, cursor = await connector.poll({}, account_id=None)

    assert result is batch
    ingest.assert_awaited_once_with(
        account_id=None,
        skip_existing_urls=True,
        defer_article_hydration_for_legacy_feeds=True,
    )
    assert "last_polled_at" in cursor


@pytest.mark.asyncio
async def test_rss_poll_defers_article_hydration_for_a_legacy_feed_folder() -> None:
    feed_url = "https://example.com/feed.xml"
    backend = MagicMock()
    backend.get_entity_by_platform = AsyncMock(
        return_value={"content": "RSS feed: Example Feed\nhttps://example.com/feed.xml"}
    )
    set_backend(backend)
    batch = EntityBatch()

    with (
        patch(
            "agentgraph_connector_rss.load_rss_settings",
            return_value=RssConfig(feed_urls=[feed_url]),
        ),
        patch("agentgraph_connector_rss._fetch_feed", new=AsyncMock(return_value=batch)) as fetch_feed,
    ):
        result, _cursor = await RssConnector().poll({}, account_id=None)

    assert result == batch
    fetch_feed.assert_awaited_once_with(
        feed_url,
        hydrate_documents=False,
        skip_existing_urls=True,
        persist_feed_payload_only=True,
    )


@pytest.mark.asyncio
async def test_rss_poll_hydrates_articles_after_feed_payload_is_stored() -> None:
    feed_url = "https://example.com/feed.xml"
    backend = MagicMock()
    backend.get_entity_by_platform = AsyncMock(return_value={"content": "<rss><channel /></rss>"})
    set_backend(backend)
    batch = EntityBatch()

    with (
        patch(
            "agentgraph_connector_rss.load_rss_settings",
            return_value=RssConfig(feed_urls=[feed_url]),
        ),
        patch("agentgraph_connector_rss._fetch_feed", new=AsyncMock(return_value=batch)) as fetch_feed,
    ):
        await RssConnector().poll({}, account_id=None)

    fetch_feed.assert_awaited_once_with(
        feed_url,
        hydrate_documents=True,
        skip_existing_urls=True,
        persist_feed_payload_only=False,
    )


@pytest.mark.asyncio
async def test_fetch_feed_hydrates_existing_entries_with_cache_validators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Parsed:
        bozo = False
        feed = {"title": "Example Feed"}
        entries = [
            {
                "id": "post-1",
                "title": "First Post",
                "link": "https://example.com/first",
                "summary": "A short summary",
            }
        ]

    monkeypatch.setattr("agentgraph_connector_rss._parse_feed", AsyncMock(return_value=_Parsed()))
    entry_id = f"entry/{_hash_ref('https://example.com/feed.xml:post-1')}"
    existing = {
        "entity_type": "Document",
        "platform": "rss",
        "platform_entity_id": entry_id,
        "title": "Cached title",
        "content": "Cached body",
        "metadata": {
            "web_url": "https://example.com/first",
            "http_etag": '"cached"',
            "http_last_modified": "Sun, 07 Jun 2026 12:00:00 GMT",
        },
    }
    backend = MagicMock()
    backend.get_entity_by_platform = AsyncMock(return_value=existing)
    set_backend(backend)

    fetched = EntityMetadataPatch(
        platform="web",
        platform_entity_id="https://example.com/first",
        metadata={
            "web_url": "https://example.com/first",
            "http_etag": '"cached"',
            "http_last_modified": "Sun, 07 Jun 2026 12:00:00 GMT",
            "status_code": 304,
        },
    )

    with patch(
        "agentgraph_connector_rss._fetch_http_document", new=AsyncMock(return_value=fetched)
    ) as fetch:
        batch = await _fetch_feed("https://example.com/feed.xml", hydrate_documents=True)

    fetch.assert_awaited_once()
    existing_arg = fetch.await_args.kwargs["existing_entity"]
    assert existing_arg["platform_entity_id"] == "https://example.com/first"
    assert existing_arg["metadata"]["http_etag"] == '"cached"'
    assert [entity.entity_type for entity in batch.entities] == ["Folder"]
    patch_result = batch.metadata_patches[0]
    assert patch_result.platform == "rss"
    assert patch_result.platform_entity_id == entry_id
    assert patch_result.metadata["status_code"] == 304
    assert patch_result.metadata["http_etag"] == '"cached"'


@pytest.mark.asyncio
async def test_fetch_feed_keeps_entry_when_hydration_fails_without_error_log(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Parsed:
        bozo = False
        feed = {"title": "Example Feed"}
        entries = [
            {
                "id": "post-1",
                "title": "First Post",
                "link": "https://missing.example/first",
                "summary": "A short summary",
            }
        ]

    monkeypatch.setattr("agentgraph_connector_rss._parse_feed", AsyncMock(return_value=_Parsed()))
    backend = MagicMock()
    backend.get_entity_by_platform = AsyncMock(return_value=None)
    set_backend(backend)
    caplog.set_level(logging.WARNING, logger="agentgraph_connector_rss")

    with patch(
        "agentgraph_connector_rss._fetch_http_document",
        new=AsyncMock(side_effect=RuntimeError("DNS lookup failed")),
    ):
        batch = await _fetch_feed("https://example.com/feed.xml", hydrate_documents=True)

    entry = batch.entities[1]
    assert entry.platform == "rss"
    assert entry.title == "First Post"
    assert entry.content and "A short summary" in entry.content
    assert entry.metadata["web_url"] == "https://missing.example/first"
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert "Skipping RSS article hydration for" in caplog.text
    assert "RuntimeError: DNS lookup failed" in caplog.text


@pytest.mark.asyncio
async def test_rss_ingest_skips_failed_feed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    entity = EntityRecord(
        entity_type="Folder",
        platform="rss",
        platform_entity_id="feed/ok",
        title="OK feed",
        content="RSS feed: OK feed",
    )
    fetch_feed = AsyncMock(
        side_effect=[
            RuntimeError("timeout"),
            EntityBatch(entities=[entity]),
        ]
    )
    caplog.set_level(logging.INFO, logger="agentgraph_connector_rss")

    with (
        patch(
            "agentgraph_connector_rss.load_rss_settings",
            return_value=RssConfig(
                feed_urls=["https://example.com/bad.xml", "https://example.com/ok.xml"]
            ),
        ),
        patch("agentgraph_connector_rss._fetch_feed", new=fetch_feed),
    ):
        batch = await RssConnector().ingest()

    assert batch.entities == [entity]
    assert fetch_feed.await_count == 2
    assert "Fetching RSS feed https://example.com/bad.xml" in caplog.text
    assert "Fetching RSS feed https://example.com/ok.xml" in caplog.text
    assert "Skipping RSS feed https://example.com/bad.xml" in caplog.text


@pytest.mark.asyncio
async def test_rss_ingest_does_not_cache_observation_patterns() -> None:
    connector = RssConnector()
    batch = EntityBatch(
        entities=[
            EntityRecord(
                entity_type="Folder",
                platform="rss",
                platform_entity_id="feed/example",
                title="Example feed",
                content="RSS feed: Example feed",
            )
        ]
    )

    with (
        patch(
            "agentgraph_connector_rss.load_rss_settings",
            return_value=RssConfig(feed_urls=["https://example.com/feed.xml"]),
        ),
        patch("agentgraph_connector_rss._fetch_feed", new=AsyncMock(return_value=batch)),
    ):
        await connector.ingest(skip_existing_urls=True)

    assert not hasattr(connector, "_observation_patterns")


@pytest.mark.asyncio
async def test_rss_ingest_aggregates_metadata_patches() -> None:
    metadata_patch = EntityMetadataPatch(
        platform="rss",
        platform_entity_id="entry/cached",
        metadata={"http_etag": '"fresh"'},
    )
    with (
        patch(
            "agentgraph_connector_rss.load_rss_settings",
            return_value=RssConfig(feed_urls=["https://example.com/feed.xml"]),
        ),
        patch(
            "agentgraph_connector_rss._fetch_feed",
            new=AsyncMock(return_value=EntityBatch(metadata_patches=[metadata_patch])),
        ),
    ):
        batch = await RssConnector().ingest()

    assert batch.metadata_patches == [metadata_patch]


@pytest.mark.asyncio
async def test_rss_fetch_entry_document_uses_http_document_cache() -> None:
    existing = {
        "entity_type": "Document",
        "platform": "rss",
        "platform_entity_id": "entry/cached",
        "title": "Cached",
        "content": "Cached content",
        "metadata": {
            "feed_url": "https://example.com/feed.xml",
            "feed_entity_id": "feed/abc",
            "link": "https://example.com/post",
            "web_url": "https://example.com/post",
            "http_etag": '"cached"',
        },
    }
    backend = MagicMock()
    backend.get_entity_by_platform = AsyncMock(return_value=existing)
    set_backend(backend)

    fetched = EntityRecord(
        entity_type="Document",
        platform="web",
        platform_entity_id="https://example.com/post",
        title="Fresh article",
        content="Fresh article body",
        metadata={
            "web_url": "https://example.com/post",
            "http_etag": '"fresh"',
            "http_last_modified": "Mon, 08 Jun 2026 01:23:45 GMT",
            "status_code": 200,
        },
    )

    with patch(
        "agentgraph_connector_rss._fetch_http_document", new=AsyncMock(return_value=fetched)
    ) as fetch:
        batch = await RssConnector().fetch(
            "document",
            "entry/cached",
            meta={
                "feed_url": "https://example.com/feed.xml",
                "feed_entity_id": "feed/abc",
                "link": "https://example.com/post",
                "web_url": "https://example.com/post",
                "http_etag": '"cached"',
            },
        )

    fetch.assert_awaited_once()
    existing_arg = fetch.await_args.kwargs["existing_entity"]
    assert existing_arg["platform_entity_id"] == "https://example.com/post"
    entity = batch.entities[0]
    assert entity.platform == "rss"
    assert entity.platform_entity_id == "entry/cached"
    assert entity.title == "Fresh article"
    assert entity.content == "Fresh article body"
    assert entity.metadata["feed_url"] == "https://example.com/feed.xml"
    assert entity.metadata["web_url"] == "https://example.com/post"
    assert entity.metadata["http_etag"] == '"fresh"'


@pytest.mark.asyncio
async def test_rss_fetch_entry_document_translates_web_metadata_patch() -> None:
    existing = {
        "entity_type": "Document",
        "platform": "rss",
        "platform_entity_id": "entry/cached",
        "title": "Cached",
        "content": "Cached content",
        "metadata": {
            "feed_url": "https://example.com/feed.xml",
            "web_url": "https://example.com/post",
            "http_etag": '"cached"',
        },
    }
    backend = MagicMock()
    backend.get_entity_by_platform = AsyncMock(return_value=existing)
    set_backend(backend)
    fetched = EntityMetadataPatch(
        platform="web",
        platform_entity_id="https://example.com/post",
        metadata={
            "http_etag": '"fresh"',
            "status_code": 304,
        },
    )

    with patch(
        "agentgraph_connector_rss._fetch_http_document",
        new=AsyncMock(return_value=fetched),
    ):
        batch = await RssConnector().fetch(
            "document",
            "entry/cached",
            meta={"web_url": "https://example.com/post"},
        )

    assert batch.entities == []
    assert batch.metadata_patches == [
        EntityMetadataPatch(
            platform="rss",
            platform_entity_id="entry/cached",
            metadata={"http_etag": '"fresh"', "status_code": 304},
        )
    ]
