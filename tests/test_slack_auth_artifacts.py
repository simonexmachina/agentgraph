"""Contracts for Slack auth documentation, skill, and app manifest."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any, cast

from agentgraph_connector_slack.auth import (
    DEFAULT_REDIRECT_URI,
    OPTIONAL_SCOPES,
    REQUIRED_SCOPES,
)

ROOT = Path(__file__).resolve().parent.parent
SLACK_PACKAGE = ROOT / "packages" / "agentgraph-connector-slack"
MANIFEST_PATH = SLACK_PACKAGE / "agentgraph_connector_slack" / "slack-app-manifest.json"


def test_slack_manifest_enables_pkce_rotation_and_scopes() -> None:
    raw = json.loads(MANIFEST_PATH.read_text())
    manifest = cast(dict[str, Any], raw)
    oauth = cast(dict[str, Any], manifest["oauth_config"])
    scope_config = cast(dict[str, list[str]], oauth["scopes"])
    settings = cast(dict[str, Any], manifest["settings"])

    assert manifest["display_information"]["name"] == "AgentGraph"
    assert oauth["pkce_enabled"] is True
    assert oauth["token_management_enabled"] is True
    assert settings["token_rotation_enabled"] is True
    assert oauth["redirect_urls"] == [DEFAULT_REDIRECT_URI]
    assert set(scope_config["user"]) == REQUIRED_SCOPES | OPTIONAL_SCOPES
    # Slack requires the base users:read scope in the optional list when its
    # users:read.email extension is optional. AgentGraph still validates
    # users:read as operationally required after authorization.
    assert set(scope_config["user_optional"]) == OPTIONAL_SCOPES | {"users:read"}


def test_slack_manifest_is_in_package_data() -> None:
    config = tomllib.loads((SLACK_PACKAGE / "pyproject.toml").read_text())
    assert config["tool"]["setuptools"]["package-data"]["agentgraph_connector_slack"] == [
        "slack-app-manifest.json"
    ]


def test_slack_auth_skill_documents_oauth_and_explicit_fallback() -> None:
    skill = (ROOT / ".agents" / "skills" / "slack-auth" / "SKILL.md").read_text()
    assert "agentgraph auth slack" in skill
    assert "interactive chooser" in skill
    assert "AGENTGRAPH_SLACK_CLIENT_ID" in skill
    assert "AGENTGRAPH_SLACK_OAUTH_CALLBACK_PORT" in skill
    assert "--add --client-id" in skill
    assert "Enter a Client ID provided by a Slack admin" in skill
    assert "Pick a workspace" in skill
    assert "--method browser" in skill
    assert "users:read.email" in skill
    assert "agentgraph auth remove slack" in skill


def test_agentgraph_skill_has_cli_and_mcp_auth_parity() -> None:
    operations = (
        ROOT / ".agents" / "skills" / "agentgraph" / "references" / "operations.md"
    ).read_text()
    assert "agentgraph auth <provider>" in operations
    assert "list_auth_providers_tool" in operations
    assert "authenticate_provider_tool" in operations
    assert "remove_auth_provider_tool" in operations


def test_slack_authentication_is_documented_in_cli_reference() -> None:
    reference = (ROOT / "docs-src" / "commands" / "auth.md").read_text()
    assert "agentgraph auth slack [--method oauth|browser]" in reference
    assert "agentgraph auth slack --add --client-id" in reference
