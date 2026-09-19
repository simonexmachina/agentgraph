"""Capability discovery uses declarations from the shipped connector classes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agentgraph_connector_discord import DiscordConnector
from agentgraph_connector_google.gdocs import GoogleDocsConnector
from agentgraph_connector_google.gdrive import DriveChangesConnector
from agentgraph_connector_google.gmail import GmailConnector
from agentgraph_connector_google.gsheets import GoogleSheetsConnector
from agentgraph_connector_rss import RssConnector
from agentgraph_connector_slack import SlackConnector
from agentgraph_connector_web import WebConnector
from typer.testing import CliRunner

from agentgraph.cli import app
from agentgraph.connectors.base import BaseConnector, EntityTypeDefinition
from agentgraph.connectors.registry import register
from agentgraph.core.storage import StorageBackend
from agentgraph.mcp.server import list_connectors_tool


@pytest.mark.parametrize(
    ("connector_class", "expected_types"),
    [
        (DiscordConnector, {"Channel": "channel", "Message": "message"}),
        (GoogleDocsConnector, {"Document": "document"}),
        (DriveChangesConnector, {"Document": "document", "Folder": "folder"}),
        (GmailConnector, {"Email": "thread", "Document": "document"}),
        (GoogleSheetsConnector, {"Spreadsheet": "spreadsheet"}),
        (RssConnector, {"Document": "document", "Folder": "folder"}),
        (SlackConnector, {"Channel": "channel", "Message": "message"}),
        (WebConnector, {"Document": "document"}),
    ],
)
def test_cli_and_mcp_discover_builtin_resource_types(
    monkeypatch: pytest.MonkeyPatch,
    connector_class: type[BaseConnector],
    expected_types: dict[str, str],
) -> None:
    connector = connector_class()
    monkeypatch.setattr("agentgraph.connectors.registry._registry", {})
    register(connector)
    backend = MagicMock(spec=StorageBackend)
    backend.get_platforms_last_synced_at = AsyncMock(return_value={})
    backend.get_sources_last_synced_at = AsyncMock(return_value={})

    @asynccontextmanager
    async def backend_context() -> AsyncIterator[StorageBackend]:
        yield backend

    with (
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.connectors.registry.get_all_connectors", return_value=[connector]),
        patch.object(connector_class, "list_accounts", return_value=[]),
        patch.object(connector_class, "get_authenticated_user", return_value=None),
        patch.object(connector_class, "verify_auth", new_callable=AsyncMock) as verify_auth,
        patch("agentgraph.core.runtime.backend_context", backend_context),
        patch("agentgraph.mcp.server._ensure_backend", new_callable=AsyncMock),
        patch("agentgraph.mcp.server.get_backend", return_value=backend),
    ):
        cli_result = CliRunner().invoke(app, ["list-connectors", "--json"])
        assert cli_result.exit_code == 0, cli_result.output
        cli_rows = json.loads(cli_result.output)
        mcp_result = asyncio.run(list_connectors_tool())
        verify_auth.assert_not_called()

    assert mcp_result.isError is False
    assert mcp_result.structuredContent is not None
    mcp_data = mcp_result.structuredContent["data"]
    assert isinstance(mcp_data, dict)
    mcp_rows = cast(list[dict[str, Any]], mcp_data["items"])
    assert cli_rows == mcp_rows
    assert len(cli_rows) == 1
    assert cli_rows[0]["source"] == connector.source
    definitions = [
        EntityTypeDefinition.model_validate(item) for item in cli_rows[0]["entity_types"]
    ]
    assert {item.name: item.resource_type for item in definitions} == expected_types
    assert len(definitions) == len(expected_types)
    for definition in definitions:
        assert (
            connector_class.entity_type_for_resource_type(definition.resource_type)
            == definition.name
        )
        assert (
            connector_class.resource_type_for_entity_type(definition.name)
            == definition.resource_type
        )
