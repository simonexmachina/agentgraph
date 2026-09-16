"""Tests for targeted connector fetches."""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentgraph.connectors.base import (
    BaseConnector,
    EdgeRecord,
    EntityBatch,
    EntityMetadataPatch,
    EntityRecord,
    PersonRecord,
    ResourceType,
)
from agentgraph.core.context import set_backend
from agentgraph.graph.fetch import fetch_entity


class _FetchConnector(BaseConnector):
    source: ClassVar[str] = "test"

    def __init__(self, batch: EntityBatch) -> None:
        self.batch = batch
        self.fetch_calls: list[tuple[ResourceType, str, dict[str, str] | None]] = []

    def can_handle(self, url: str) -> bool:
        _ = url
        return False

    async def fetch(
        self,
        resource_type: ResourceType,
        resource_id: str,
        meta: dict[str, str] | None = None,
        account_id: str | None = None,
    ) -> EntityBatch:
        _ = account_id
        self.fetch_calls.append((resource_type, resource_id, meta))
        return self.batch


@pytest.mark.asyncio
async def test_fetch_entity_persists_connector_batch() -> None:
    batch = EntityBatch(
        persons=[
            PersonRecord(
                platform="test",
                platform_user_id="author-1",
                display_name="Author One",
            )
        ],
        entities=[
            EntityRecord(
                entity_type="Document",
                platform="test",
                platform_entity_id="article-1",
                title="Article One",
            )
        ],
        edges=[
            EdgeRecord(
                edge_type="authored",
                source_platform_user_id="author-1",
                target_platform_entity_id="article-1",
                platform="test",
            )
        ],
    )
    connector = _FetchConnector(batch)
    backend = MagicMock()
    backend.get_entity_by_platform = AsyncMock(
        return_value={
            "entity_type": "Document",
            "metadata": {"web_url": "https://example.com/article-1"},
        }
    )
    backend.reset_synced_at = AsyncMock()
    set_backend(backend)

    with (
        patch("agentgraph.connectors.registry.get_connector", return_value=connector),
        patch("agentgraph.graph.upsert.upsert_batch", new=AsyncMock()) as upsert_batch,
    ):
        result = await fetch_entity("test", "article-1")

    assert connector.fetch_calls == [
        (
            "document",
            "article-1",
            {"web_url": "https://example.com/article-1"},
        )
    ]
    backend.reset_synced_at.assert_awaited_once_with("test", "article-1")
    upsert_batch.assert_awaited_once_with(batch)
    assert result == {
        "entity": {
            "entity_type": "Document",
            "metadata": {"web_url": "https://example.com/article-1"},
        },
        "entities": 1,
        "metadata_patches": 0,
        "persons": 1,
        "edges": 1,
    }


@pytest.mark.asyncio
async def test_fetch_entity_persists_metadata_patch_batch() -> None:
    batch = EntityBatch(
        metadata_patches=[
            EntityMetadataPatch(
                platform="test",
                platform_entity_id="article-1",
                metadata={"revision": "2"},
            )
        ]
    )
    connector = _FetchConnector(batch)
    backend = MagicMock()
    backend.get_entity_by_platform = AsyncMock(
        return_value={"entity_type": "Document", "metadata": {}}
    )
    backend.reset_synced_at = AsyncMock()
    set_backend(backend)

    with (
        patch("agentgraph.connectors.registry.get_connector", return_value=connector),
        patch("agentgraph.graph.upsert.upsert_batch", new=AsyncMock()) as upsert_batch,
    ):
        result = await fetch_entity("test", "article-1")

    upsert_batch.assert_awaited_once_with(batch)
    assert result == {
        "entity": {"entity_type": "Document", "metadata": {}},
        "entities": 0,
        "metadata_patches": 1,
        "persons": 0,
        "edges": 0,
    }
