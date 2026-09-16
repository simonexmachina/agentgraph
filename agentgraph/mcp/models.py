"""Structured MCP response models."""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, RootModel

from agentgraph.graph.operations import is_stub


class GraphEntity(BaseModel):
    """An entity returned to an MCP client."""

    model_config = ConfigDict(extra="allow")

    id: str
    entity_type: str
    platform: str
    platform_entity_id: str
    title: str | None = None
    content: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None
    source_created_at: str | None = None
    source_updated_at: str | None = None
    synced_at: str | None = None
    observed_at: str | None = None
    retention_policy: str = "observed"
    retention_parent_id: str | None = None
    cumulative_observation_duration_ms: int = 0
    bookmarked: bool = False
    score: float | None = None
    content_truncated: bool = False
    is_stub: bool
    source_url: str | None = None


class GraphEdge(BaseModel):
    """An edge returned to an MCP client."""

    model_config = ConfigDict(extra="allow")

    id: str
    edge_type: str
    platform: str
    properties: dict[str, Any] = Field(default_factory=dict)
    source_entity_id: str | None = None
    target_entity_id: str | None = None
    source_ref: str | None = None
    target_ref: str | None = None


class EntityReference(BaseModel):
    """Stable reference to a graph entity returned by a mutation."""

    id: str
    platform: str
    platform_entity_id: str
    entity_type: str
    source_url: str | None = None


class SearchData(BaseModel):
    entities: list[GraphEntity]
    limit: int
    returned: int
    has_more: bool


class EntityData(BaseModel):
    entity: GraphEntity


class EdgesData(BaseModel):
    entity: EntityReference
    edges: list[GraphEdge]


class TraverseData(BaseModel):
    nodes: list[GraphEntity]
    edges: list[GraphEdge]
    max_depth: int


class FetchData(BaseModel):
    entity: EntityReference | None = None
    entities: int
    metadata_patches: int
    persons: int
    edges: int


class ErrorData(BaseModel):
    status: Literal["error"] = "error"
    code: str
    message: str
    recovery: str | None = None


class SuccessData[PayloadT](BaseModel):
    status: Literal["ok"] = "ok"
    data: PayloadT


class ToolData[PayloadT](RootModel[SuccessData[PayloadT] | ErrorData]):
    """The structured content schema shared by MCP tool calls."""


def entity_from_record(record: dict[str, Any]) -> GraphEntity:
    """Add stable presentation fields to a graph entity before validation."""
    payload = dict(record)
    metadata = payload.get("metadata")
    metadata_dict = cast(dict[str, Any], metadata) if isinstance(metadata, dict) else {}
    payload["metadata"] = metadata_dict
    payload["is_stub"] = is_stub(record)
    web_url = metadata_dict.get("web_url")
    payload["source_url"] = web_url if isinstance(web_url, str) else None
    return GraphEntity.model_validate(payload)


def entity_reference_from_record(record: dict[str, Any]) -> EntityReference:
    entity = entity_from_record(record)
    return EntityReference(
        id=entity.id,
        platform=entity.platform,
        platform_entity_id=entity.platform_entity_id,
        entity_type=entity.entity_type,
        source_url=entity.source_url,
    )
