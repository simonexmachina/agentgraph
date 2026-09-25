"""Tests for CLI structure."""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agentgraph_connector_google.provider import (
    GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET,
    GoogleCredentials,
)
from agentgraph_connector_slack import SlackConnector
from click import unstyle
from typer.testing import CliRunner

from agentgraph.auth.credentials import (
    load_platform,
    load_platform_account,
    load_platform_accounts,
    save_platform,
    upsert_platform_account,
)
from agentgraph.cli import app
from agentgraph.connectors.base import (
    ConnectorAccount,
    ConnectorCommandEffects,
    EntityReference,
    EntityTypeDefinition,
    SourceReference,
)
from agentgraph.core.storage import EntityResult

runner = CliRunner()


@pytest.fixture
def tmp_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    creds_file = tmp_path / "credentials.json"
    monkeypatch.setattr("agentgraph.auth.credentials.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("agentgraph.auth.credentials.CREDENTIALS_FILE", creds_file)
    return creds_file


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "serve" in result.output
    assert "search" in result.output
    assert "auth" in result.output
    assert "download" in result.output
    assert "bookmark" in result.output
    assert "delete" in result.output
    assert "unify-persons" in result.output
    assert "demo" in result.output
    assert "ingest" not in result.output


def test_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output == "agentgraph 0.9.1\n"


def _serve_settings(uds_path: Path | None) -> SimpleNamespace:
    return SimpleNamespace(
        log_level="INFO",
        log_file=Path("/tmp/agentgraph-test/agentgraph.log"),
        server_host="127.0.0.1",
        server_port=8765,
        server_uds_path=uds_path,
    )


def test_serve_outputs_log_file_path() -> None:
    """With the socket disabled, serve keeps the plain TCP uvicorn.run path."""
    settings = _serve_settings(None)
    with (
        patch("agentgraph.config.get_settings", return_value=settings),
        patch("agentgraph.logging.configure_logging"),
        patch("uvicorn.run") as run,
    ):
        result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0
    assert f"AgentGraph log file: {settings.log_file}" in result.output
    run.assert_called_once_with(
        "agentgraph.server.app:app",
        host="127.0.0.1",
        port=8765,
        reload=False,
    )


def test_serve_listens_on_both_tcp_and_the_socket() -> None:
    """Uvicorn takes one config, so both listeners are pre-bound and passed in."""
    uds_path = Path("/tmp/agentgraph-test/ag.sock")
    tcp_socket = object()
    uds_socket = MagicMock()
    settings = _serve_settings(uds_path)

    with (
        patch("agentgraph.config.get_settings", return_value=settings),
        patch("agentgraph.logging.configure_logging"),
        patch("agentgraph.server.uds.bind_socket", return_value=uds_socket) as bind,
        patch("agentgraph.server.uds.set_owned_socket") as set_owned,
        patch("agentgraph.server.uds.release_owned_socket") as release,
        patch("uvicorn.Config") as config_cls,
        patch("uvicorn.Server") as server_cls,
    ):
        config_cls.return_value.bind_socket.return_value = tcp_socket
        config_cls.return_value.backlog = 2048
        result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0
    assert f"AgentGraph socket: {uds_path}" in result.output
    bind.assert_called_once_with(uds_path, backlog=2048)
    server_cls.return_value.run.assert_called_once_with(sockets=[tcp_socket, uds_socket])
    # The socket file must not outlive the server that bound it. Ownership is claimed
    # before serving so the app's shutdown hook can remove it on SIGTERM.
    set_owned.assert_called_once_with(uds_path)
    uds_socket.close.assert_called_once()
    release.assert_called_once()


def test_serve_reports_a_socket_already_in_use() -> None:
    settings = _serve_settings(Path("/tmp/agentgraph-test/ag.sock"))

    with (
        patch("agentgraph.config.get_settings", return_value=settings),
        patch("agentgraph.logging.configure_logging"),
        patch(
            "agentgraph.server.uds.bind_socket",
            side_effect=OSError("Another AgentGraph server is already listening"),
        ),
        patch("uvicorn.Config"),
        patch("uvicorn.Server") as server_cls,
    ):
        result = runner.invoke(app, ["serve"])

    assert result.exit_code == 1
    assert "already listening" in result.output
    server_cls.return_value.run.assert_not_called()


def test_serve_with_reload_skips_the_socket() -> None:
    """Reload runs its own supervisor, which cannot inherit pre-bound sockets."""
    uds_path = Path("/tmp/agentgraph-test/ag.sock")
    settings = _serve_settings(uds_path)

    with (
        patch("agentgraph.config.get_settings", return_value=settings),
        patch("agentgraph.logging.configure_logging"),
        patch("agentgraph.server.uds.bind_socket") as bind,
        patch("uvicorn.run") as run,
    ):
        result = runner.invoke(app, ["serve", "--reload"])

    assert result.exit_code == 0
    assert f"Reload mode serves TCP only; not listening on {uds_path}" in result.output
    bind.assert_not_called()
    run.assert_called_once_with(
        "agentgraph.server.app:app",
        host="127.0.0.1",
        port=8765,
        reload=True,
    )


def test_demo_add_creates_fixture_and_outputs_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "atlas-demo"
    monkeypatch.setenv("AGENTGRAPH_CONFIG_DIR", str(config_dir))

    result = runner.invoke(app, ["demo", "add", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["entities"] == 9
    assert (config_dir / "agentgraph.db").exists()


def test_demo_add_preserves_project_dotenv_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    working_dir = tmp_path / "demo-run"
    config_dir = tmp_path / "atlas-demo"
    working_dir.mkdir()
    environment_path = working_dir / ".env"
    environment = f"AGENTGRAPH_CONFIG_DIR={config_dir}\n"
    environment_path.write_text(environment, encoding="utf-8")
    monkeypatch.chdir(working_dir)
    monkeypatch.delenv("AGENTGRAPH_CONFIG_DIR", raising=False)

    result = runner.invoke(app, ["demo", "add", "--json"])

    assert result.exit_code == 0
    assert (config_dir / "agentgraph.db").exists()
    assert environment_path.read_text(encoding="utf-8") == environment


def test_demo_add_rejects_config_dir_option() -> None:
    result = runner.invoke(app, ["demo", "add", "--config-dir", "/tmp/atlas-demo"])

    assert result.exit_code != 0
    assert "No such option: --config-dir" in unstyle(result.output)


def test_demo_remove_outputs_removed_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "atlas-demo"
    monkeypatch.setenv("AGENTGRAPH_CONFIG_DIR", str(config_dir))

    assert runner.invoke(app, ["demo", "add"]).exit_code == 0
    result = runner.invoke(app, ["demo", "remove", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output)["removed"] == 12


def test_demo_add_rejects_reset_option() -> None:
    result = runner.invoke(app, ["demo", "add", "--reset"])

    assert result.exit_code != 0
    assert "No such option: --reset" in unstyle(result.output)


def test_bookmark_command_dispatches_to_cli_query() -> None:
    with patch("agentgraph.cli_query.cmd_bookmark") as cmd_bookmark:
        result = runner.invoke(app, ["bookmark", "abc123", "--json"])

    assert result.exit_code == 0
    cmd_bookmark.assert_called_once_with(target="abc123", bookmarked=True, as_json=True)


def test_bookmark_remove_command_dispatches_to_cli_query() -> None:
    with patch("agentgraph.cli_query.cmd_bookmark") as cmd_bookmark:
        result = runner.invoke(app, ["bookmark", "abc123", "--remove", "--json"])

    assert result.exit_code == 0
    cmd_bookmark.assert_called_once_with(target="abc123", bookmarked=False, as_json=True)


def test_delete_command_dispatches_to_cli_query() -> None:
    with patch("agentgraph.cli_query.cmd_delete") as cmd_delete:
        result = runner.invoke(app, ["delete", "abc123", "--json"])

    assert result.exit_code == 0
    cmd_delete.assert_called_once_with(targets=["abc123"], as_json=True)


def test_delete_command_accepts_multiple_targets() -> None:
    with patch("agentgraph.cli_query.cmd_delete") as cmd_delete:
        result = runner.invoke(app, ["delete", "first", "second", "--json"])

    assert result.exit_code == 0
    cmd_delete.assert_called_once_with(targets=["first", "second"], as_json=True)


def test_get_url_uses_shared_operation_without_http() -> None:
    from agentgraph.cli_query import cmd_get

    entity: EntityResult = {
        "id": "entity-12345678",
        "entity_type": "Document",
        "platform": "web",
        "title": "Page",
        "metadata": {},
    }

    with (
        patch("agentgraph.cli_query.backend_context", _fake_backend_context),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.graph.operations.get_entity_details",
            new=AsyncMock(return_value=entity),
        ) as get_entity_details,
        patch("httpx.get", side_effect=AssertionError("unexpected HTTP GET")),
        patch("httpx.post", side_effect=AssertionError("unexpected HTTP POST")),
    ):
        cmd_get("https://example.com/page", as_json=True)

    get_entity_details.assert_awaited_once_with("https://example.com/page", resolve=False)


def test_get_url_reports_not_found_from_backend(capsys: pytest.CaptureFixture[str]) -> None:
    from agentgraph.cli_query import cmd_get

    with (
        patch("agentgraph.cli_query.backend_context", _fake_backend_context),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.graph.operations.get_entity_details",
            new=AsyncMock(return_value=None),
        ),
    ):
        cmd_get("https://example.com/page", as_json=True)

    assert "not found" in capsys.readouterr().out


def test_get_prints_raw_content_without_rich_markup_errors() -> None:
    from agentgraph.cli_query import cmd_get

    entity = {
        "id": "entity-12345678",
        "entity_type": "Document",
        "platform": "web",
        "title": "Page [literal]",
        "content": "window.DD_RUM.init({allowedTracingUrls: [/https?:\\/\\/(.+\\/.)?substack(cdn)?\\.com/]});",
        "metadata": {"pattern": "[/not-rich-markup]"},
    }

    with (
        patch("agentgraph.cli_query.backend_context", _fake_backend_context),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.graph.operations.get_entity_details",
            new=AsyncMock(return_value=entity),
        ),
    ):
        cmd_get("entity-12345678", as_json=False)


def test_bookmark_uses_graph_operation_without_http() -> None:
    from agentgraph.cli_query import cmd_bookmark

    entity = {
        "id": "entity-12345678",
        "title": "Bookmarked",
        "platform_entity_id": "ref",
        "bookmarked": True,
    }

    with (
        patch("agentgraph.cli_query.backend_context", _fake_backend_context),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.graph.bookmark.bookmark_target",
            new=AsyncMock(return_value=entity),
        ) as bookmark_target,
        patch("httpx.post", side_effect=AssertionError("unexpected HTTP POST")),
    ):
        cmd_bookmark("https://example.com/page", bookmarked=True, as_json=True)

    bookmark_target.assert_awaited_once_with("https://example.com/page")


def test_delete_uses_graph_operation_without_http() -> None:
    from agentgraph.cli_query import cmd_delete

    result = {
        "deleted": True,
        "entity": {
            "id": "entity-12345678",
            "title": "Deleted",
            "platform_entity_id": "ref",
        },
    }

    with (
        patch("agentgraph.cli_query.backend_context", _fake_backend_context),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.graph.delete.delete_entity",
            new=AsyncMock(return_value=result),
        ) as delete_entity,
        patch("httpx.post", side_effect=AssertionError("unexpected HTTP POST")),
    ):
        cmd_delete(["abc123"], as_json=True)

    delete_entity.assert_awaited_once_with("abc123")


def test_delete_multiple_uses_bulk_graph_operation_without_http() -> None:
    from agentgraph.cli_query import cmd_delete

    result = {"deleted_count": 2, "entities": [{"id": "one"}, {"id": "two"}]}
    with (
        patch("agentgraph.cli_query.backend_context", _fake_backend_context),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.graph.delete.delete_entities",
            new=AsyncMock(return_value=result),
        ) as delete_entities,
        patch("httpx.post", side_effect=AssertionError("unexpected HTTP POST")),
    ):
        cmd_delete(["first", "second"], as_json=True)

    delete_entities.assert_awaited_once_with(["first", "second"])


def test_unify_persons_shows_the_canonical_person(capsys: pytest.CaptureFixture[str]) -> None:
    from agentgraph.cli_query import cmd_unify_persons

    result: dict[str, Any] = {
        "merged_count": 2,
        "merged_ids": ["duplicate-1", "duplicate-2"],
        "primary": {
            "id": "canonical-person-123",
            "entity_type": "Person",
            "platform": "canonical",
            "platform_entity_id": "simon.wade@gmail.com",
            "title": "Simon Wade",
            "content": "simon.wade@gmail.com",
            "metadata": {"slack_user_id": "T1/U1", "discord_user_id": "D1"},
        },
    }

    with (
        patch("agentgraph.cli_query.backend_context", _fake_backend_context),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.graph.person.unify_persons",
            new=AsyncMock(return_value=result),
        ) as unify_persons,
    ):
        cmd_unify_persons("canonical", ["duplicate-1", "duplicate-2"], as_json=False)

    unify_persons.assert_awaited_once_with(
        "canonical",
        ["duplicate-1", "duplicate-2"],
    )
    output = capsys.readouterr().out
    assert "Unified: 2 duplicate person(s). Canonical person:" in output
    assert "Person — canonical" in output
    assert "Simon Wade" in output
    assert "slack_user_id" in output


def test_download_uses_graph_operation_without_http() -> None:
    from agentgraph.cli_query import cmd_download

    result = {
        "filename": "sheet.xlsx",
        "bytes": 123,
        "path": "/tmp/sheet.xlsx",
    }

    with (
        patch("agentgraph.cli_query.backend_context", _fake_backend_context),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.graph.download.download_entity",
            new=AsyncMock(return_value=result),
        ) as download_entity,
        patch("httpx.post", side_effect=AssertionError("unexpected HTTP POST")),
    ):
        cmd_download("gsheets/sheet-id", output_path="/tmp", as_json=True)

    download_entity.assert_awaited_once_with("gsheets/sheet-id", "/tmp")


def test_fetch_uses_graph_operation_without_http() -> None:
    from agentgraph.cli_query import cmd_fetch

    fetch_result = {"entities": 1, "persons": 0, "edges": 0}

    with (
        patch("agentgraph.cli_query.backend_context", _fake_backend_context),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.graph.fetch.fetch_entity",
            new=AsyncMock(return_value=fetch_result),
        ) as fetch_entity,
        patch("httpx.post", side_effect=AssertionError("unexpected HTTP POST")),
    ):
        cmd_fetch("gsheets", "sheet-id", as_json=True)

    fetch_entity.assert_awaited_once_with("gsheets", "sheet-id")


def test_fetch_web_size_limit_suggests_compact_command(capsys: pytest.CaptureFixture[str]) -> None:
    from agentgraph_connector_web import WebConnector

    from agentgraph.cli_query import cmd_fetch

    with (
        patch("agentgraph.cli_query.backend_context", _fake_backend_context),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.graph.fetch.fetch_entity",
            new=AsyncMock(
                side_effect=ValueError("Response too large for web document: limit is 2000000 bytes")
            ),
        ),
        patch("agentgraph.connectors.registry.get_connector", return_value=WebConnector()),
        pytest.raises(SystemExit) as exc,
    ):
        cmd_fetch("web", "https://example.com/page", as_json=False)

    assert exc.value.code == 1
    output = capsys.readouterr().out
    assert "Response too large for web document" in output
    assert "agentgraph connector web fetch https://example.com/page --compact" in output


def test_fetch_entity_uses_graph_operation_without_http() -> None:
    from agentgraph.cli_query import cmd_fetch_entity

    fetch_result = {"entities": 1, "persons": 1, "edges": 1}

    with (
        patch("agentgraph.cli_query.backend_context", _fake_backend_context),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.graph.fetch.fetch_entity_by_id",
            new=AsyncMock(return_value=fetch_result),
        ) as fetch_entity_by_id,
        patch("httpx.post", side_effect=AssertionError("unexpected HTTP POST")),
    ):
        cmd_fetch_entity("entity-id", as_json=True)

    fetch_entity_by_id.assert_awaited_once_with("entity-id")


def test_backend_error_exits_nonzero() -> None:
    from agentgraph.cli_query import cmd_search

    with (
        patch("agentgraph.cli_query.backend_context", side_effect=RuntimeError("database offline")),
        pytest.raises(SystemExit) as exc,
    ):
        cmd_search(entity_types=["Email"], order_by="updated_at", as_json=True)

    assert exc.value.code == 1


def test_filtered_search_uses_graph_operation_without_http() -> None:
    from agentgraph.cli_query import cmd_search

    with (
        patch("agentgraph.cli_query.backend_context", _fake_backend_context),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.graph.query.search_entities",
            new=AsyncMock(return_value=[]),
        ) as search_entities,
        patch("httpx.get", side_effect=AssertionError("unexpected HTTP GET")),
        patch("httpx.post", side_effect=AssertionError("unexpected HTTP POST")),
    ):
        cmd_search(
            entity_types=["Email"],
            filters={"platform": "gmail"},
            limit=5,
            order_by="updated_at",
            as_json=True,
        )

    search_entities.assert_awaited_once_with(
        None,
        entity_types=["Email"],
        limit=5,
        min_score=0.03,
        platform=None,
        filters={"platform": "gmail"},
        since=None,
        observed_since=None,
        authored_by_me=False,
        has_attachments=False,
        order_by="updated_at",
    )


def test_search_limit_defaults_split_on_whether_a_query_was_given() -> None:
    """A ranked search stays at 10; a filter-only listing browses 50, as `query` did."""
    from agentgraph.cli_query import cmd_search

    with (
        patch("agentgraph.cli_query.backend_context", _fake_backend_context),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.graph.query.search_entities",
            new=AsyncMock(return_value=[]),
        ) as search_entities,
    ):
        cmd_search(query="atlas", as_json=True)
        cmd_search(as_json=True)

    assert [call.kwargs["limit"] for call in search_entities.await_args_list] == [10, 50]


def test_auth_help() -> None:
    result = runner.invoke(app, ["auth", "--help"])
    assert result.exit_code == 0
    assert "status" in result.output.lower()


def test_mcp_config_includes_desktop_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["agentgraph"])
    result = runner.invoke(app, ["mcp-config"])
    assert result.exit_code == 0
    assert "Claude Desktop" in result.output
    assert "ChatGPT Desktop Work Mode" in result.output
    assert "Command to launch" in result.output
    assert "Arguments" in result.output
    chatgpt_instructions = result.output.split("Claude Desktop:", maxsplit=1)[0]
    assert "Command to launch:\n  agentgraph\n  Arguments:\n  mcp-serve" in chatgpt_instructions
    assert "mcp-serve" in result.output
    assert "~/Library/Application Support/Claude/claude_desktop_config.json" in result.output
    assert '"mcpServers"' in result.output
    assert "streamable-http" not in result.output
    assert "Secure MCP Tunnel" not in result.output


def test_mcp_config_covers_the_coding_agent_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex and Claude Code register the same server from the terminal."""
    monkeypatch.setattr(sys, "argv", ["agentgraph"])
    result = runner.invoke(app, ["mcp-config"])

    assert result.exit_code == 0
    assert "codex mcp add agentgraph -- agentgraph mcp-serve" in result.output
    assert "claude mcp add --transport stdio --scope user agentgraph -- agentgraph mcp-serve" in result.output
    # Every client shares one transport setting, so say so once rather than per client.
    assert "AGENTGRAPH_QUERY_TRANSPORT" in result.output


def test_install_skill_defaults_to_user_agent_and_claude_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    result = runner.invoke(app, ["install-skill"])

    assert result.exit_code == 0
    skill_path = home / ".agents" / "skills" / "agentgraph" / "SKILL.md"
    assert skill_path.is_file()
    skill_content = skill_path.read_text(encoding="utf-8")
    assert "# AgentGraph skill" in skill_content
    assert "Start with `search` for discovery" in skill_content
    assert "Prefer the connected MCP tools when AgentGraph is available through MCP." in skill_content
    references = skill_path.parent / "references"
    assert {path.name for path in references.glob("*.md")} == {
        "commands.md",
        "data-model.md",
        "operations.md",
    }
    assert str(skill_path.parent) in result.output
    claude_path = home / ".claude" / "skills" / "agentgraph"
    assert claude_path.is_symlink()
    assert claude_path.resolve() == skill_path.parent


def test_install_skill_help_describes_install_locations() -> None:
    result = runner.invoke(app, ["install-skill", "--help"])

    assert result.exit_code == 0
    assert "~/.agents/skills" in result.output
    assert "./.agents/skills" in result.output
    assert "~/.claude/skills" in result.output
    assert "./.claude/skills" in result.output


def test_install_skill_no_claude_skips_claude_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    result = runner.invoke(app, ["install-skill", "--no-claude", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert not (home / ".claude").exists()
    assert parsed["claude_destination"] is None


def test_install_skill_refuses_to_overwrite_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    skill_path = home / ".agents" / "skills" / "agentgraph" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("custom", encoding="utf-8")

    result = runner.invoke(app, ["install-skill"])

    assert result.exit_code == 1
    assert "Use --force" in result.output
    assert skill_path.read_text(encoding="utf-8") == "custom"


def test_install_skill_force_overwrites_existing_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    skill_path = home / ".agents" / "skills" / "agentgraph" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("custom", encoding="utf-8")

    result = runner.invoke(app, ["install-skill", "--force", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["skill"] == "agentgraph"
    assert parsed["target"] == "user"
    assert parsed["overwritten"] is True
    assert "# AgentGraph skill" in skill_path.read_text(encoding="utf-8")


def test_install_skill_succeeds_when_claude_skills_links_to_agent_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    agent_skills = home / ".agents" / "skills"
    agent_skills.mkdir(parents=True)
    claude_skills = home / ".claude" / "skills"
    claude_skills.parent.mkdir(parents=True)
    claude_skills.symlink_to(Path("..") / ".agents" / "skills", target_is_directory=True)

    result = runner.invoke(app, ["install-skill", "--json"])

    assert result.exit_code == 0
    skill_path = agent_skills / "agentgraph" / "SKILL.md"
    assert skill_path.is_file()
    # The one copy is what Claude reads, so no nested link is made inside it.
    assert (claude_skills / "agentgraph" / "SKILL.md").is_file()
    assert not (skill_path.parent / "agentgraph").exists()
    parsed = json.loads(result.output)
    assert parsed["claude_linked"] is False
    assert parsed["claude_destination"] == str(claude_skills / "agentgraph")


def test_install_skill_force_reinstalls_through_a_linked_claude_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    skill_path = home / ".agents" / "skills" / "agentgraph" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("custom", encoding="utf-8")
    claude_skills = home / ".claude" / "skills"
    claude_skills.parent.mkdir(parents=True)
    claude_skills.symlink_to(Path("..") / ".agents" / "skills", target_is_directory=True)

    result = runner.invoke(app, ["install-skill", "--force", "--json"])

    assert result.exit_code == 0
    assert "File exists" not in result.output
    parsed = json.loads(result.output)
    assert parsed["overwritten"] is True
    assert parsed["claude_linked"] is False
    assert "# AgentGraph skill" in skill_path.read_text(encoding="utf-8")


def test_install_skill_force_replaces_a_separate_claude_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    stale_target = tmp_path / "stale"
    stale_target.mkdir()
    claude_skills = home / ".claude" / "skills"
    claude_skills.mkdir(parents=True)
    (claude_skills / "agentgraph").symlink_to(stale_target, target_is_directory=True)

    result = runner.invoke(app, ["install-skill", "--force", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["claude_linked"] is True
    claude_path = claude_skills / "agentgraph"
    assert claude_path.is_symlink()
    assert claude_path.resolve() == (home / ".agents" / "skills" / "agentgraph").resolve()


def test_install_skill_project_target_uses_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["install-skill", "--target", "project"])

    assert result.exit_code == 0
    skill_dir = tmp_path / ".agents" / "skills" / "agentgraph"
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "references" / "commands.md").is_file()


def test_install_skill_rejects_legacy_graph_name() -> None:
    result = runner.invoke(app, ["install-skill", "graph"])

    assert result.exit_code == 1
    assert "Skill 'graph' was not found" in result.output


def test_install_skill_rejects_legacy_agentgraph_name() -> None:
    result = runner.invoke(app, ["install-skill", "AgentGraph"])

    assert result.exit_code == 1
    assert "Skill 'AgentGraph' was not found" in result.output


def test_install_skill_leaves_legacy_graph_directory_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    legacy_skill = home / ".agents" / "skills" / "graph" / "SKILL.md"
    legacy_skill.parent.mkdir(parents=True)
    legacy_skill.write_text("custom legacy skill", encoding="utf-8")

    result = runner.invoke(app, ["install-skill", "--force"])

    assert result.exit_code == 0
    assert legacy_skill.read_text(encoding="utf-8") == "custom legacy skill"
    assert (home / ".agents" / "skills" / "agentgraph" / "SKILL.md").is_file()


class _FakeConnector:
    source = "slack"
    auth_label = "slack"
    auth_description = "Slack"
    onboard_prompt = "Set up Slack?"
    poll_interval = None
    poll_delegates: list[str] = []
    url_patterns: list[str] = []
    auth_called = False

    @classmethod
    def run_auth_flow(cls) -> None:
        cls.auth_called = True

    @classmethod
    def get_authenticated_user(cls) -> None:
        return None


class _FakeGoogleConnector:
    source = "gdocs"
    auth_label = "google"
    auth_description = "Google"
    onboard_prompt = "Set up Google?"
    poll_interval = None
    poll_delegates: list[str] = []
    url_patterns: list[str] = []
    entity_types = (
        EntityTypeDefinition(
            name="Project",
            resource_type="project",
            description="A Google project.",
        ),
    )

    @classmethod
    def run_auth_flow(
        cls,
        account_id: str | None = None,
        add: bool = False,
        args: list[str] | None = None,
    ) -> None:
        google_auth = cast(Any, import_module("agentgraph_connector_google.auth"))
        run_oauth_flow = cast(Callable[..., None], google_auth.run_oauth_flow)
        run_oauth_flow(account_id=account_id, add=add, args=args)

    @classmethod
    async def verify_auth(cls) -> tuple[str, str | None]:
        return ("ok", "user@example.com")

    @classmethod
    def list_accounts(cls) -> list[ConnectorAccount]:
        return [
            ConnectorAccount(
                account_id="acct-google",
                label="User Example",
                auth_group="google",
                source=cls.source,
                user_id="user@example.com",
                email="user@example.com",
                auth_method="oauth",
            )
        ]


class _FakeDriveConnector:
    source = "gdrive"
    auth_label = "google"
    auth_description = "Google Drive"
    poll_interval = timedelta(minutes=10)
    poll_delegates = ["gdocs"]
    url_patterns = ["https://drive.google.com/*"]

    @classmethod
    async def verify_auth(cls) -> tuple[str, str | None]:
        return ("ok", "user@example.com")

    @classmethod
    def list_accounts(cls) -> list[ConnectorAccount]:
        return _FakeGoogleConnector.list_accounts()


class _FakeRssConnector:
    source = "rss"
    auth_label = "rss"
    auth_description = "RSS"
    appears_in_auth_status = False
    poll_interval = None
    poll_delegates: list[str] = []
    url_patterns: list[str] = []

    @classmethod
    async def verify_auth(cls) -> tuple[str, str | None]:
        return ("missing", None)

    @classmethod
    def cli_help(cls) -> str:
        return "RSS connector help"

    @classmethod
    def format_cli_result(cls, result: dict[str, Any]) -> str:
        return f"formatted {result['status']}"

    @classmethod
    def command_effects(
        cls,
        args: list[str],
        result: dict[str, Any],
    ) -> ConnectorCommandEffects:
        _ = (args, result)
        return ConnectorCommandEffects()


class _FakeFeedConnector:
    source = "feed"
    auth_label = "feed"
    auth_description = "Shares local observations and bookmarks through an AgentGraph feed server."
    appears_in_auth_status = False
    poll_interval = timedelta(minutes=1)
    poll_delegates: list[str] = []
    url_patterns: list[str] = []


class _FakeGoogleToken:
    token = "new-access-token"
    refresh_token = "new-refresh-token"
    expiry = None


class _FakeGoogleFlow:
    captured_client_config: dict[str, Any] | None = None
    captured_scopes: list[str] | None = None
    last_instance: _FakeGoogleFlow | None = None

    def __init__(self) -> None:
        self.credentials = _FakeGoogleToken()
        self.code_verifier = "pkce-verifier"
        self.fetch_token = MagicMock()
        type(self).last_instance = self

    @classmethod
    def from_client_config(
        cls,
        client_config: dict[str, Any],
        *,
        scopes: list[str],
        redirect_uri: str,
    ) -> _FakeGoogleFlow:
        cls.captured_client_config = client_config
        cls.captured_scopes = scopes
        return cls()

    def authorization_url(self, *, access_type: str, prompt: str) -> tuple[str, None]:
        return ("https://accounts.google.test/auth", None)


class _FakeUserInfoResponse:
    ok = True

    def json(self) -> dict[str, str]:
        return {"email": "new@example.com", "name": "New User"}


def _fake_requests_get(
    url: str,
    *,
    headers: dict[str, str],
    timeout: int,
) -> _FakeUserInfoResponse:
    return _FakeUserInfoResponse()


def _fake_wait_for_callback(port: int) -> str:
    return "auth-code"


def _fake_webbrowser_open(url: str) -> bool:
    return True


def _install_fake_google_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeGoogleFlow.last_instance = None
    flow_module = ModuleType("google_auth_oauthlib.flow")
    flow_module.__dict__["Flow"] = _FakeGoogleFlow
    package_module = ModuleType("google_auth_oauthlib")
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", package_module)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_module)

    requests_module = ModuleType("requests")
    requests_module.__dict__["get"] = _fake_requests_get
    monkeypatch.setitem(sys.modules, "requests", requests_module)
    monkeypatch.setattr("agentgraph_connector_google.auth._find_free_port", lambda: 9999)
    monkeypatch.setattr(
        "agentgraph_connector_google.auth._wait_for_callback", _fake_wait_for_callback
    )
    monkeypatch.setattr("webbrowser.open", _fake_webbrowser_open)


@asynccontextmanager
async def _fake_backend_context() -> AsyncGenerator[Any, None]:
    backend = MagicMock()

    async def _get_platform_last_synced_at(platform: str) -> datetime | None:
        values = {
            "gdocs": datetime(2026, 5, 25, 1, 2, 3, tzinfo=UTC),
            "gdrive": None,
        }
        return values.get(platform)

    async def _get_platforms_last_synced_at(platforms: list[str]) -> dict[str, datetime | None]:
        return {platform: await _get_platform_last_synced_at(platform) for platform in platforms}

    async def _get_sources_last_synced_at(sources: list[str]) -> dict[str, datetime | None]:
        return {source: await _get_platform_last_synced_at(source) for source in sources}

    backend.get_platform_last_synced_at = AsyncMock(side_effect=_get_platform_last_synced_at)
    backend.get_platforms_last_synced_at = AsyncMock(side_effect=_get_platforms_last_synced_at)
    backend.get_sources_last_synced_at = AsyncMock(side_effect=_get_sources_last_synced_at)
    yield backend


def test_auth_unknown_target_exits_nonzero() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_all_connectors", return_value=[_FakeConnector()]),
    ):
        result = runner.invoke(app, ["auth", "notaplatform"])
    assert result.exit_code != 0
    assert "notaplatform" in result.output


def test_auth_provider_dispatches_to_connector() -> None:
    _FakeConnector.auth_called = False
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_all_connectors", return_value=[_FakeConnector()]),
    ):
        result = runner.invoke(app, ["auth", "slack"])
    assert result.exit_code == 0
    assert _FakeConnector.auth_called


def test_onboard_directs_users_to_install_chrome_extension() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_all_connectors", return_value=[_FakeConnector()]),
        patch("agentgraph.cli._server_is_running", return_value=False),
        patch("agentgraph.cli.questionary.confirm") as confirm,
    ):
        confirm.return_value.ask.return_value = False
        result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0
    assert "Step 2/2: Install the AgentGraph Chrome Extension" in result.output
    assert "Install the AgentGraph Chrome Extension" in result.output
    assert (
        "https://chromewebstore.google.com/detail/agentgraph-extension/"
        "iilkfclglabllelhjacijldknapbhidi"
    ) in result.output
    assert "Run `agentgraph serve` to start the server." in result.output
    confirm.assert_called_once_with("  Set up Slack?", default=True)
    confirm.return_value.ask.assert_called_once_with()


def test_onboard_omits_server_hint_when_server_is_running() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_all_connectors", return_value=[_FakeConnector()]),
        patch("agentgraph.cli._server_is_running", return_value=True),
        patch("agentgraph.cli.questionary.confirm") as confirm,
    ):
        confirm.return_value.ask.return_value = False
        result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0
    assert "Step 2/2: Install the AgentGraph Chrome Extension" in result.output
    assert "Run `agentgraph serve` to start the server." not in result.output


def test_onboard_skips_connectors_without_an_onboarding_flow() -> None:
    class _FakeWebConnector:
        source = "web"
        auth_label = None
        auth_description = "Web"
        onboard_prompt = None
        onboard_last = False
        poll_interval = None
        poll_delegates: list[str] = []
        url_patterns: list[str] = []

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[_FakeWebConnector()],
        ),
    ):
        result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0
    assert "Step 1/1: Install the AgentGraph Chrome Extension" in result.output
    assert "Set up web?" not in result.output


def test_onboard_places_last_step_connectors_at_the_end() -> None:
    class _FakeRssOnboardConnector:
        source = "rss"
        auth_label = "rss"
        auth_description = "RSS"
        onboard_prompt = "Set up RSS feeds?"
        onboard_last = True
        poll_interval = None
        poll_delegates: list[str] = []
        url_patterns: list[str] = []
        auth_calls = 0

        @classmethod
        def run_auth_flow(cls) -> None:
            cls.auth_calls += 1

    class _FakeSlackOnboardConnector:
        source = "slack"
        auth_label = "slack"
        auth_description = "Slack"
        onboard_prompt = "Set up Slack?"
        onboard_last = False
        poll_interval = None
        poll_delegates: list[str] = []
        url_patterns: list[str] = []

        @classmethod
        def run_auth_flow(cls) -> None:
            pass

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[_FakeRssOnboardConnector(), _FakeSlackOnboardConnector()],
        ),
        patch("agentgraph.cli.questionary.confirm") as confirm,
    ):
        confirm.return_value.ask.return_value = False
        result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0
    assert result.output.index("Step 1/3: Slack") < result.output.index("Step 2/3: RSS")


def test_auth_slack_accepts_noninteractive_options_after_provider() -> None:
    from agentgraph_connector_slack import SlackConnector

    captured: dict[str, object] = {}

    def fake_cookie_flow(
        *,
        account_id: str | None,
        add: bool,
        xoxc_token: str | None,
        d_cookie: str | None,
    ) -> None:
        captured.update(
            account_id=account_id,
            add=add,
            xoxc_token=xoxc_token,
            d_cookie=d_cookie,
        )

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[SlackConnector()],
        ),
        patch("agentgraph_connector_slack.auth.run_cookie_flow", side_effect=fake_cookie_flow),
    ):
        result = runner.invoke(
            app,
            [
                "auth",
                "slack",
                "--xoxc-token",
                "xoxc-test",
                "--d-cookie",
                "cookie",
                "--account",
                "slack:T1:U1",
                "--add",
            ],
        )

    assert result.exit_code == 0
    assert captured == {
        "account_id": "slack:T1:U1",
        "add": True,
        "xoxc_token": "xoxc-test",
        "d_cookie": "cookie",
    }


def test_auth_slack_explicit_oauth_rejects_browser_options() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[SlackConnector()],
        ),
    ):
        result = runner.invoke(
            app,
            ["auth", "slack", "--method", "oauth", "--xoxc-token", "xoxc-test"],
        )

    assert result.exit_code == 2
    assert "cannot be used with --method oauth" in result.output


def test_auth_slack_explicit_browser_dispatches_to_connector() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[SlackConnector()],
        ),
        patch("agentgraph_connector_slack.auth.run_cookie_flow") as browser,
    ):
        result = runner.invoke(app, ["auth", "slack", "--method=browser", "--add"])

    assert result.exit_code == 0
    browser.assert_called_once_with(
        account_id=None,
        add=True,
        xoxc_token=None,
        d_cookie=None,
    )


def test_auth_slack_client_id_implies_oauth_and_adds_account() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[SlackConnector()],
        ),
        patch("agentgraph_connector_slack.auth.run_guided_oauth_flow") as oauth,
    ):
        result = runner.invoke(
            app,
            ["auth", "slack", "--add", "--client-id", "admin-client-id"],
        )

    assert result.exit_code == 0
    oauth.assert_called_once_with(
        account_id=None,
        add=True,
        client_id="admin-client-id",
    )


def test_auth_slack_browser_rejects_client_id() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[SlackConnector()],
        ),
    ):
        result = runner.invoke(
            app,
            ["auth", "slack", "--method", "browser", "--client-id", "client-id"],
        )

    assert result.exit_code == 2
    assert "--client-id cannot be used with --method browser" in result.output


def test_auth_slack_without_method_runs_interactive_chooser() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[SlackConnector()],
        ),
        patch("agentgraph_connector_slack.auth.run_interactive_auth_flow") as interactive,
    ):
        result = runner.invoke(app, ["auth", "slack", "--add"])

    assert result.exit_code == 0
    interactive.assert_called_once_with(account_id=None, add=True)


def test_auth_remove_deletes_provider_credentials(tmp_creds: Path) -> None:
    save_platform("slack", {"xoxc_token": "xoxc-test", "d_cookie": "cookie"})

    result = runner.invoke(app, ["auth", "remove", "slack"])

    assert result.exit_code == 0
    assert result.output == "Removed stored credentials for slack.\n"
    assert load_platform("slack") is None


def test_auth_remove_account_outputs_json(tmp_creds: Path) -> None:
    upsert_platform_account("google", "one@example.com", {"user_email": "one@example.com"})
    upsert_platform_account("google", "two@example.com", {"user_email": "two@example.com"})

    result = runner.invoke(
        app, ["auth", "remove", "google", "--account", "one@example.com", "--json"]
    )

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed == {
        "provider": "google",
        "removed": True,
        "account_id": "one@example.com",
    }
    assert [account["account_id"] for account in load_platform_accounts("google")] == [
        "two@example.com"
    ]
    assert load_platform_account("google", "two@example.com") is not None


def test_auth_status_reports_corrupt_credentials_file(tmp_creds: Path) -> None:
    save_platform("slack", {"xoxc_token": "xoxc-test"})
    tmp_creds.write_text(tmp_creds.read_text() + '  "discord": {')

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 1
    assert "Could not parse" in result.output
    assert str(tmp_creds) in result.output


def test_auth_remove_reports_corrupt_credentials_file(tmp_creds: Path) -> None:
    tmp_creds.write_text("{not json")

    result = runner.invoke(app, ["auth", "remove", "slack"])

    assert result.exit_code == 1
    assert "Could not parse" in result.output


def test_auth_remove_missing_provider_exits_nonzero(tmp_creds: Path) -> None:
    result = runner.invoke(app, ["auth", "remove", "slack"])

    assert result.exit_code != 0
    assert "No stored credentials found for slack." in result.output


def test_connector_command_dispatches_to_connector() -> None:
    captured: dict[str, object] = {}

    class _DispatchRssConnector(_FakeRssConnector):
        @classmethod
        def run_cli_command(cls, args: list[str]) -> dict[str, object]:
            captured["args"] = args
            return {"status": "ok", "args": args}

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=_DispatchRssConnector()),
    ):
        result = runner.invoke(
            app, ["connector", "rss", "add", "https://simonwillison.net/atom/everything/"]
        )

    assert result.exit_code == 0
    assert captured == {"args": ["add", "https://simonwillison.net/atom/everything/"]}
    assert result.output == "formatted ok\n"


def test_connector_command_json_outputs_raw_result() -> None:
    class _JsonRssConnector(_FakeRssConnector):
        @classmethod
        def run_cli_command(cls, args: list[str]) -> dict[str, object]:
            return {"status": "ok", "args": args}

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=_JsonRssConnector()),
    ):
        result = runner.invoke(
            app, ["connector", "rss", "add", "https://example.com/feed.xml", "--json"]
        )

    assert result.exit_code == 0
    assert '"status": "ok"' in result.output
    assert '"args": [' in result.output


def test_connector_command_queues_requested_poll() -> None:
    class _PollingRssConnector(_FakeRssConnector):
        @classmethod
        def run_cli_command(cls, args: list[str]) -> dict[str, Any]:
            return {"status": "ok", "args": args}

        @classmethod
        def command_effects(
            cls,
            args: list[str],
            result: dict[str, Any],
        ) -> ConnectorCommandEffects:
            _ = (args, result)
            return ConnectorCommandEffects(poll=True)

    poll_result = {"source": "rss", "status": "queued", "reason": None}
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=_PollingRssConnector()),
        patch("agentgraph.cli_sync.queue_connector_poll", return_value=poll_result) as queue_poll,
    ):
        result = runner.invoke(
            app, ["connector", "rss", "add", "https://example.com/feed.xml", "--json"]
        )

    assert result.exit_code == 0
    assert json.loads(result.output)["poll"] == poll_result
    queue_poll.assert_called_once_with("rss")


def test_connector_command_executes_requested_entity_deletion() -> None:
    class _DeletingRssConnector(_FakeRssConnector):
        @classmethod
        def run_cli_command(cls, args: list[str]) -> dict[str, Any]:
            return {"status": "ok", "args": args}

        @classmethod
        def command_effects(
            cls,
            args: list[str],
            result: dict[str, Any],
        ) -> ConnectorCommandEffects:
            _ = (args, result)
            return ConnectorCommandEffects(
                delete_entities=(EntityReference("rss", "feed/example"),),
            )

    deleted = [{"id": "feed", "platform": "rss", "platform_entity_id": "feed/example"}]
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=_DeletingRssConnector()),
        patch("agentgraph.cli_query.run_graph_operation", return_value=deleted) as run_operation,
    ):
        result = runner.invoke(
            app, ["connector", "rss", "remove", "https://example.com/feed.xml", "--json"]
        )

    assert result.exit_code == 0
    assert json.loads(result.output)["deleted_entities"] == deleted
    run_operation.assert_called_once()


def test_connector_command_executes_requested_cursor_reset() -> None:
    class _ResettingRssConnector(_FakeRssConnector):
        @classmethod
        def run_cli_command(cls, args: list[str]) -> dict[str, Any]:
            return {"status": "ok", "args": args}

        @classmethod
        def command_effects(
            cls,
            args: list[str],
            result: dict[str, Any],
        ) -> ConnectorCommandEffects:
            _ = (args, result)
            return ConnectorCommandEffects(reset_cursors=("rss",))

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_connector",
            return_value=_ResettingRssConnector(),
        ),
        patch("agentgraph.cli_query.run_graph_operation", return_value=["rss"]) as run_operation,
    ):
        result = runner.invoke(app, ["connector", "rss", "enable", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output)["reset_cursors"] == ["rss"]
    run_operation.assert_called_once()


def test_connector_command_executes_requested_fetch() -> None:
    class _FetchingWebConnector(_FakeRssConnector):
        source = "web"

        @classmethod
        def run_cli_command(cls, args: list[str]) -> dict[str, Any]:
            return {"status": "ok", "url": args[1]}

        @classmethod
        def command_effects(
            cls,
            args: list[str],
            result: dict[str, Any],
        ) -> ConnectorCommandEffects:
            _ = (args, result)
            return ConnectorCommandEffects(
                fetch_references=(
                    SourceReference("web", "document", "https://example.com/page"),
                )
            )

    fetched = [
        {
            "source": "web",
            "resource_type": "document",
            "resource_id": "https://example.com/page",
            "entities": 1,
            "persons": 0,
            "edges": 0,
        }
    ]
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_connector",
            return_value=_FetchingWebConnector(),
        ),
        patch("agentgraph.cli_query.run_graph_operation", return_value=fetched) as run_operation,
    ):
        result = runner.invoke(
            app,
            ["connector", "web", "fetch", "https://example.com/page", "--json"],
        )

    assert result.exit_code == 0
    assert json.loads(result.output)["fetched"] == fetched
    run_operation.assert_called_once()


def test_connector_web_fetch_size_limit_suggests_compact_command() -> None:
    from agentgraph_connector_web import WebConnector

    with (
        patch("agentgraph.cli_query.backend_context", _fake_backend_context),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=WebConnector()),
        patch(
            "agentgraph.connectors.command_effects.execute_fetches",
            new=AsyncMock(
                side_effect=ValueError(
                    "Response too large for web document: limit is 2000000 bytes"
                )
            ),
        ),
    ):
        result = runner.invoke(
            app,
            ["connector", "web", "fetch", "https://example.com/page"],
        )

    assert result.exit_code == 1
    assert "Response too large for web document" in result.output
    assert "agentgraph connector web fetch https://example.com/page --compact" in result.output


def test_connector_command_queues_requested_ingest_for_account() -> None:
    class _IngestingGmailConnector(_FakeRssConnector):
        source = "gmail"

        @classmethod
        def run_cli_command(cls, args: list[str]) -> dict[str, Any]:
            return {"status": "queued", "source": cls.source, "account_id": "user@example.com"}

        @classmethod
        def command_effects(
            cls,
            args: list[str],
            result: dict[str, Any],
        ) -> ConnectorCommandEffects:
            _ = (args, result)
            return ConnectorCommandEffects(ingest=True, ingest_account_id="user@example.com")

    ingest_result = {"source": "gmail", "status": "started", "account_id": "user@example.com"}
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_connector", return_value=_IngestingGmailConnector()
        ),
        patch(
            "agentgraph.cli_sync.queue_connector_ingest", return_value=ingest_result
        ) as queue_ingest,
    ):
        result = runner.invoke(
            app, ["connector", "gmail", "ingest", "--account", "user@example.com", "--json"]
        )

    assert result.exit_code == 0
    assert json.loads(result.output)["ingest"] == ingest_result
    queue_ingest.assert_called_once_with("gmail", account_id="user@example.com")


def test_gmail_connector_ingest_command_parses_account_scope() -> None:
    from agentgraph_connector_google.gmail import GmailConnector

    assert GmailConnector.run_cli_command(["ingest"]) == {
        "status": "queued",
        "source": "gmail",
        "account_id": None,
    }
    assert (
        GmailConnector.run_cli_command(["ingest", "--account=user@example.com"])["account_id"]
        == "user@example.com"
    )
    assert GmailConnector.command_effects(
        ["ingest", "--account", "user@example.com"],
        GmailConnector.run_cli_command(["ingest", "--account", "user@example.com"]),
    ) == ConnectorCommandEffects(ingest=True, ingest_account_id="user@example.com")

    with pytest.raises(ValueError, match="Usage: agentgraph connector gmail ingest"):
        GmailConnector.run_cli_command(["ingest", "--unexpected"])


def test_connector_command_does_not_poll_after_validation_error() -> None:
    class _InvalidRssConnector(_FakeRssConnector):
        @classmethod
        def run_cli_command(cls, args: list[str]) -> dict[str, Any]:
            _ = args
            raise ValueError("Not a valid RSS/Atom feed")

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=_InvalidRssConnector()),
        patch("agentgraph.cli_sync.queue_connector_poll") as queue_poll,
    ):
        result = runner.invoke(app, ["connector", "rss", "add", "https://example.com/not-a-feed"])

    assert result.exit_code == 1
    assert "Not a valid RSS/Atom feed" in result.output
    queue_poll.assert_not_called()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            {"polled": ["rss"], "already_running": [], "skipped": []},
            {"source": "rss", "status": "queued", "reason": None},
        ),
        (
            {"polled": [], "already_running": ["rss"], "skipped": []},
            {"source": "rss", "status": "already_running", "reason": None},
        ),
        (
            {
                "polled": [],
                "already_running": [],
                "skipped": [{"source": "rss", "reason": "authentication missing"}],
            },
            {"source": "rss", "status": "skipped", "reason": "authentication missing"},
        ),
    ],
)
def test_queue_connector_poll_normalizes_server_result(
    response: dict[str, object],
    expected: dict[str, object],
) -> None:
    from agentgraph.cli_sync import queue_connector_poll

    with patch("agentgraph.cli_sync._post", return_value=response):
        result = queue_connector_poll("rss")

    assert result == expected


def test_connector_command_dispatches_help_to_connector() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=_FakeRssConnector()),
    ):
        result = runner.invoke(app, ["connector", "rss", "--help"])

    assert result.exit_code == 0
    assert result.output == "RSS connector help\n"


def test_connector_command_reports_connector_load_error() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_connector", return_value=None),
        patch(
            "agentgraph.connectors.registry.get_connector_load_error",
            return_value="No module named 'curl_cffi'",
        ),
    ):
        result = runner.invoke(app, ["connector", "web", "--help"])

    assert result.exit_code == 1
    assert "Failed to load connector 'web': No module named 'curl_cffi'" in result.output
    assert "Unknown connector" not in result.output


def test_list_connectors_reports_delegated_polling() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.core.runtime.backend_context", _fake_backend_context),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[_FakeGoogleConnector(), _FakeDriveConnector()],
        ),
    ):
        result = runner.invoke(app, ["list-connectors"])

    assert result.exit_code == 0
    assert "Connectors" in result.output
    assert "Connector" in result.output
    assert "Types" in result.output
    assert "Project" in result.output
    assert "Auth" in result.output
    assert "Last sync" in result.output
    assert "gdocs" in result.output
    assert "via" in result.output
    assert "polling" in result.output
    assert "10m" in result.output
    assert "2026-05-25 01:02:03Z" in result.output
    assert "account:" not in result.output


def test_list_connectors_json_reports_delegated_polling() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.core.runtime.backend_context", _fake_backend_context),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[_FakeGoogleConnector(), _FakeDriveConnector()],
        ),
    ):
        result = runner.invoke(app, ["list-connectors", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed[0]["auth_provider"] == "google"
    assert parsed[0]["entity_types"] == [
        {
            "name": "Project",
            "resource_type": "project",
            "description": "A Google project.",
        }
    ]
    assert parsed[0]["last_synced_at"] == "2026-05-25T01:02:03+00:00"
    assert parsed[0]["polled_by"] == ["gdrive"]
    assert parsed[1]["poll_delegates"] == ["gdocs"]


def test_list_connectors_default_uses_local_auth_status_without_live_verify() -> None:
    class _LocalConnector(_FakeGoogleConnector):
        verify_called = False

        @classmethod
        async def verify_auth(cls) -> tuple[str, str | None]:
            cls.verify_called = True
            return ("invalid", "live check failed")

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.core.runtime.backend_context", _fake_backend_context),
        patch(
            "agentgraph.connectors.registry.get_all_connectors", return_value=[_LocalConnector()]
        ),
    ):
        result = runner.invoke(app, ["list-connectors", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed[0]["auth_status"] == "ok"
    assert parsed[0]["auth_verified"] is False
    assert _LocalConnector.verify_called is False


def test_list_connectors_verify_runs_live_auth_check() -> None:
    class _VerifiedConnector(_FakeGoogleConnector):
        verify_called = False

        @classmethod
        async def verify_auth(cls) -> tuple[str, str | None]:
            cls.verify_called = True
            return ("invalid", "live check failed")

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.core.runtime.backend_context", _fake_backend_context),
        patch(
            "agentgraph.connectors.registry.get_all_connectors", return_value=[_VerifiedConnector()]
        ),
    ):
        result = runner.invoke(app, ["list-connectors", "--verify", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed[0]["auth_status"] == "invalid"
    assert parsed[0]["auth_detail"] == "live check failed"
    assert parsed[0]["auth_verified"] is True
    assert _VerifiedConnector.verify_called is True


def test_list_connectors_omits_auth_status_for_non_auth_connectors() -> None:
    class _NonAuthRssConnector(_FakeRssConnector):
        verify_called = False

        @classmethod
        async def verify_auth(cls) -> tuple[str, str | None]:
            cls.verify_called = True
            return ("ok", "should not be called")

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.core.runtime.backend_context", _fake_backend_context),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[_NonAuthRssConnector()],
        ),
    ):
        json_result = runner.invoke(app, ["list-connectors", "--verify", "--json"])
        text_result = runner.invoke(app, ["list-connectors", "--verify"])

    assert json_result.exit_code == 0
    parsed = json.loads(json_result.output)
    assert parsed[0]["source"] == "rss"
    assert parsed[0]["auth_provider"] is None
    assert parsed[0]["auth_status"] is None
    assert parsed[0]["auth_detail"] is None
    assert parsed[0]["auth_verified"] is False
    assert parsed[0]["account_count"] == 0
    assert text_result.exit_code == 0
    assert "rss" in text_result.output
    assert "on-demand" in text_result.output
    assert _NonAuthRssConnector.verify_called is False


def test_list_connectors_includes_feed_connector() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.core.runtime.backend_context", _fake_backend_context),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[_FakeFeedConnector()],
        ),
    ):
        result = runner.invoke(app, ["list-connectors", "--json"])
        text_result = runner.invoke(app, ["list-connectors"])

    assert result.exit_code == 0
    assert text_result.exit_code == 0
    assert "feed" in text_result.output
    assert "Types" in text_result.output
    assert "core" not in text_result.output
    assert json.loads(result.output) == [
        {
            "source": "feed",
            "description": "Shares local observations and bookmarks through an AgentGraph feed server.",
            "entity_types": [],
            "auth_provider": None,
            "shared_auth": False,
            "auth_status": None,
            "auth_detail": None,
            "auth_verified": False,
            "account_count": 0,
            "url_patterns": [],
            "polls": True,
            "poll_interval_seconds": 60,
            "poll_delegates": [],
            "polled_by": [],
            "sync": "polling every 1m",
            "last_synced_at": None,
            "last_sync": "never",
        }
    ]


def test_connectors_legacy_command_is_rejected() -> None:
    result = runner.invoke(app, ["connectors"])

    assert result.exit_code != 0
    assert "No such command 'connectors'" in result.output


def test_auth_status_dedupes_shared_google_provider() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[_FakeGoogleConnector(), _FakeDriveConnector()],
        ),
    ):
        result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0
    assert "Authentication status" in result.output
    assert "Provider" in result.output
    assert "Account ID" in result.output
    assert result.output.count("User Example") == 1
    assert "acct-google" in result.output
    assert "oauth" in result.output
    assert "OK" in result.output
    assert "gdocs, gdrive" in result.output


def test_auth_status_exposes_slack_auth_method(tmp_creds: Path) -> None:
    save_platform(
        "slack",
        {
            "xoxc_token": "xoxc-T1-old",
            "d_cookie": "cookie",
            "team_id": "T1",
            "user_id": "U1",
        },
    )
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[SlackConnector()],
        ),
    ):
        result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0
    assert "Provider" in result.output
    assert "Account" in result.output
    assert "Method" in result.output
    assert "Status" in result.output
    assert "Connectors" in result.output
    assert "slack:T1:U1" in result.output
    assert "browser" in result.output


def test_auth_status_renders_provider_without_accounts() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[_FakeConnector()],
        ),
    ):
        result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0
    assert "slack" in result.output
    assert "Missing" in result.output


def test_auth_status_excludes_non_auth_connectors() -> None:
    class _FakeWebConnector:
        source = "web"
        auth_label = None
        auth_description = "Web"
        appears_in_auth_status = False
        poll_interval = None
        poll_delegates: list[str] = []
        url_patterns: list[str] = []

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[_FakeGoogleConnector(), _FakeRssConnector(), _FakeWebConnector()],
        ),
    ):
        result = runner.invoke(app, ["auth", "--json", "status"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert [item["provider"] for item in parsed] == ["google"]


def test_auth_google_invalid_existing_credentials_reuses_client_config(
    tmp_creds: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_platform(
        "google",
        GoogleCredentials(
            client_id="stored-client-id",
            access_token="old-access-token",
            refresh_token="old-refresh-token",
            user_email="old@example.com",
        ),
    )
    _FakeGoogleFlow.captured_client_config = None

    flow_module = ModuleType("google_auth_oauthlib.flow")
    flow_module.__dict__["Flow"] = _FakeGoogleFlow
    package_module = ModuleType("google_auth_oauthlib")
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", package_module)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_module)

    requests_module = ModuleType("requests")
    requests_module.__dict__["get"] = _fake_requests_get
    monkeypatch.setitem(sys.modules, "requests", requests_module)

    monkeypatch.setattr(
        "agentgraph_connector_google.auth.verify_google_auth",
        lambda: (
            "invalid",
            "Google refresh token was rejected (RefreshError) - run: agentgraph auth google",
        ),
    )
    monkeypatch.setattr("agentgraph_connector_google.auth._find_free_port", lambda: 9999)
    monkeypatch.setattr(
        "agentgraph_connector_google.auth._wait_for_callback", _fake_wait_for_callback
    )
    monkeypatch.setattr("webbrowser.open", _fake_webbrowser_open)

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[_FakeGoogleConnector()],
        ),
    ):
        result = runner.invoke(app, ["auth", "google"])

    assert result.exit_code == 0
    assert "Google credentials need re-authentication" in result.output
    assert "packaged OAuth client" in result.output
    assert "Google OAuth client ID" not in result.output
    assert _FakeGoogleFlow.captured_client_config is not None
    installed = _FakeGoogleFlow.captured_client_config["installed"]
    assert installed["client_id"] == GOOGLE_OAUTH_CLIENT_ID
    assert installed["client_secret"] == GOOGLE_OAUTH_CLIENT_SECRET

    saved = load_platform("google")
    assert saved is not None
    assert saved["access_token"] == "new-access-token"
    assert saved["refresh_token"] == "new-refresh-token"


def test_auth_google_uses_packaged_client_without_prompt(
    tmp_creds: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeGoogleFlow.captured_client_config = None
    _FakeGoogleFlow.captured_scopes = None
    _install_fake_google_oauth(monkeypatch)

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[_FakeGoogleConnector()],
        ),
    ):
        result = runner.invoke(app, ["auth", "google"])

    assert result.exit_code == 0
    assert "Google OAuth client" not in result.output
    assert _FakeGoogleFlow.captured_client_config is not None
    installed = _FakeGoogleFlow.captured_client_config["installed"]
    assert installed["client_id"] == GOOGLE_OAUTH_CLIENT_ID
    assert installed["client_secret"] == GOOGLE_OAUTH_CLIENT_SECRET
    assert _FakeGoogleFlow.captured_scopes == [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "openid",
    ]
    assert _FakeGoogleFlow.last_instance is not None
    _FakeGoogleFlow.last_instance.fetch_token.assert_called_once_with(code="auth-code")
    saved = load_platform("google")
    assert saved is not None
    assert saved["client_id"] == GOOGLE_OAUTH_CLIENT_ID
    assert "client_secret" not in saved
    assert "Run later to import Gmail history" in result.output
    assert "agentgraph connector gmail ingest --account new@example.com" in result.output


def test_auth_google_queues_gmail_backfill_for_new_account(
    tmp_creds: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_google_oauth(monkeypatch)
    monkeypatch.setattr(
        "agentgraph_connector_google.auth.sys",
        SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True)),
    )
    queued = {"source": "gmail", "status": "started", "account_id": "new@example.com"}

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[_FakeGoogleConnector()],
        ),
        patch("agentgraph_connector_google.auth.typer.confirm", return_value=True),
        patch("agentgraph.cli_sync.queue_connector_ingest", return_value=queued) as queue_ingest,
    ):
        result = runner.invoke(app, ["auth", "google"], input="y\n")

    assert result.exit_code == 0
    queue_ingest.assert_called_once_with("gmail", account_id="new@example.com")
    assert "Gmail backfill queued" in result.output


def test_auth_google_rejects_client_id_override() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[_FakeGoogleConnector()],
        ),
    ):
        result = runner.invoke(
            app,
            ["auth", "google", "--client-id", "override-client-id"],
        )

    assert result.exit_code == 2
    assert "unrecognized arguments: --client-id override-client-id" in result.output


def test_auth_google_rejects_unknown_provider_option() -> None:
    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[_FakeGoogleConnector()],
        ),
    ):
        result = runner.invoke(app, ["auth", "google", "--unknown"])

    assert result.exit_code == 2
    assert "unrecognized arguments: --unknown" in result.output


def test_auth_google_valid_credentials_can_skip_reauth(
    tmp_creds: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_platform(
        "google",
        GoogleCredentials(
            client_id="stored-client-id",
            access_token="access-token",
            refresh_token="refresh-token",
            user_email="user@example.com",
        ),
    )
    monkeypatch.setattr(
        "agentgraph_connector_google.auth.verify_google_auth",
        lambda: ("ok", "user@example.com"),
    )

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[_FakeGoogleConnector()],
        ),
    ):
        result = runner.invoke(app, ["auth", "google"], input="n\n")

    assert result.exit_code == 0
    assert "Google is already authenticated as user@example.com" in result.output
    assert "Keeping existing credentials" in result.output


def test_search_without_a_query_lists_recent_entities() -> None:
    """The query argument is optional: filters alone are a valid selection."""
    entity = {
        "id": "22c57772-78cb-4234-ada7-36730b26e52c",
        "entity_type": "Message",
        "platform": "slack",
        "title": "Standup notes",
    }

    with (
        patch("agentgraph.cli_query.backend_context", _fake_backend_context),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch(
            "agentgraph.graph.query.search_entities",
            new=AsyncMock(return_value=[entity]),
        ) as search_entities,
    ):
        result = runner.invoke(app, ["search"])

    assert result.exit_code == 0
    assert "Standup notes" in result.output
    # No query string, so there is no relevance column to render.
    assert "Score" not in result.output
    assert search_entities.await_args is not None
    assert search_entities.await_args.args[0] is None
