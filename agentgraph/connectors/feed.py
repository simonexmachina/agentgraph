"""Mutation delivery hooks for feed-style connectors."""

from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from agentgraph.connectors.base import BaseConnector, ResourceType

logger = logging.getLogger(__name__)

_NOTIFICATION_TIMEOUT_SECONDS = 5.0
_notifications_suppressed: ContextVar[bool] = ContextVar(
    "feed_notifications_suppressed", default=False
)


class MutationTarget(BaseModel):
    """Stable reference to the entity affected by a mutation."""

    platform: str
    platform_entity_id: str
    entity_type: str
    resource_type: ResourceType | None = None
    url: str | None = None


class EntitySnapshot(BaseModel):
    """Committed graph entity state delivered with an upsert mutation."""

    id: str
    entity_type: str
    platform: str
    platform_entity_id: str
    title: str | None = None
    content: str | None = None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    source_created_at: str | None = None
    source_updated_at: str | None = None
    synced_at: str | None = None
    observed_at: str | None = None
    retention_policy: str
    retention_parent_id: str | None = None
    cumulative_observation_duration_ms: int
    bookmarked: bool


class EdgeSnapshot(BaseModel):
    """Committed graph edge state incident to an upserted entity."""

    id: str
    edge_type: str
    platform: str
    properties: dict[str, Any]
    source_entity_id: str
    target_entity_id: str
    source_ref: str
    target_ref: str


class ObservationMutation(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    kind: Literal["observation"] = "observation"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    target: MutationTarget
    observation_duration_ms: int = Field(gt=0)
    meta: dict[str, str] | None = None


class BookmarkMutation(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    kind: Literal["bookmark"] = "bookmark"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    target: MutationTarget
    bookmarked: bool


class TombstoneMutation(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    kind: Literal["tombstone"] = "tombstone"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    target: MutationTarget


class TombstoneBatchMutation(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    kind: Literal["tombstone_batch"] = "tombstone_batch"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    targets: list[MutationTarget] = Field(min_length=1)


class EntityUpsertMutation(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    kind: Literal["upsert"] = "upsert"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    target: MutationTarget
    entity: EntitySnapshot
    edges: list[EdgeSnapshot]


type MutationEvent = (
    ObservationMutation
    | BookmarkMutation
    | TombstoneMutation
    | TombstoneBatchMutation
    | EntityUpsertMutation
)


class FeedConnector(BaseConnector):
    """Connector that receives local mutations and polls a shared feed."""

    @abstractmethod
    async def publish_mutation(self, event: MutationEvent) -> None:
        """Publish one committed local mutation without changing local state."""
        ...


@contextmanager
def suppress_feed_notifications() -> Any:
    """Prevent imported mutations from being published back to their feed."""

    token = _notifications_suppressed.set(True)
    try:
        yield
    finally:
        _notifications_suppressed.reset(token)


async def notify_feed_connectors(event: MutationEvent) -> None:
    """Best-effort delivery of a committed mutation to installed feed connectors."""

    if _notifications_suppressed.get():
        return

    from agentgraph.connectors.registry import get_all_connectors

    connectors = [
        connector for connector in get_all_connectors() if isinstance(connector, FeedConnector)
    ]
    if not connectors:
        return
    await asyncio.gather(*(_notify_connector(connector, event) for connector in connectors))


async def _notify_connector(connector: FeedConnector, event: MutationEvent) -> None:
    try:
        await asyncio.wait_for(
            connector.publish_mutation(event),
            timeout=_NOTIFICATION_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.exception(
            "Feed connector %s failed to publish %s mutation %s",
            connector.source,
            event.kind,
            event.event_id,
        )


def mutation_target_from_entity(
    entity: dict[str, Any],
    *,
    resource_type: ResourceType | None = None,
    url: str | None = None,
) -> MutationTarget:
    """Build a stable mutation target from a stored entity result."""

    metadata_value: object = entity.get("metadata")
    metadata = cast(dict[str, object], metadata_value) if isinstance(metadata_value, dict) else {}
    metadata_url = metadata.get("web_url")
    return MutationTarget(
        platform=str(entity["platform"]),
        platform_entity_id=str(entity["platform_entity_id"]),
        entity_type=str(entity["entity_type"]),
        resource_type=resource_type,
        url=url or (metadata_url if isinstance(metadata_url, str) else None),
    )


def entity_snapshot_from_entity(entity: dict[str, Any]) -> EntitySnapshot:
    """Validate a committed storage result for use in an upsert mutation."""

    return EntitySnapshot.model_validate(entity)


def edge_snapshot_from_edge(edge: dict[str, Any]) -> EdgeSnapshot:
    """Validate a committed storage edge for use in an upsert mutation."""

    return EdgeSnapshot.model_validate(edge)


def mutation_target_from_reference(
    *,
    platform: str,
    platform_entity_id: str,
    resource_type: ResourceType,
    url: str,
) -> MutationTarget:
    """Build a target from a connector-owned source reference."""
    from agentgraph.connectors.base import SourceReference
    from agentgraph.connectors.registry import entity_type_for_reference

    return MutationTarget(
        platform=platform,
        platform_entity_id=platform_entity_id,
        entity_type=entity_type_for_reference(
            SourceReference(
                source=platform,
                resource_type=resource_type,
                resource_id=platform_entity_id,
            )
        ),
        resource_type=resource_type,
        url=url,
    )
