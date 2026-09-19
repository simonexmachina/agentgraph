"""StorageBackend ABC — the persistence-agnostic interface for AgentGraph."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from agentgraph.connectors.base import EntityBatch

EntityResult = dict[str, Any]
EdgeResult = dict[str, Any]


class StorageBackend(ABC):
    """Persistence-agnostic interface for the AgentGraph knowledge store.

    All methods are async. The backend is responsible for all SQL/storage
    operations; the graph layer computes embeddings and passes them in.
    """

    # --- Lifecycle ---

    @abstractmethod
    async def initialize(self) -> None:
        """Apply schema, run migrations, open connection pool."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release all connections and resources."""
        ...

    # --- Write ---

    @abstractmethod
    async def upsert_batch(
        self,
        batch: EntityBatch,
        person_embeddings: dict[str, list[float] | None],
        entity_embeddings: dict[str, list[float] | None],
    ) -> list[EntityResult]:
        """Atomically persist an EntityBatch.

        person_embeddings: canonical_key (email or "platform:user_id") → vector
        entity_embeddings: platform_entity_id → vector
        Returns committed snapshots for inserted or materially changed entities.
        """
        ...

    @abstractmethod
    async def merge_person_entities(
        self,
        primary_entity_id: str,
        duplicate_entity_ids: list[str],
    ) -> EntityResult:
        """Merge duplicate Person entities into primary_entity_id.

        Backends must move all edges from duplicate persons to the primary person,
        merge non-conflicting metadata onto the primary, delete the duplicates,
        and return the updated primary entity.
        """
        ...

    @abstractmethod
    async def set_entity_bookmarked(
        self,
        entity_id: str,
        bookmarked: bool,
    ) -> EntityResult:
        """Set or clear expiration protection for an entity and return the updated entity."""
        ...

    @abstractmethod
    async def delete_entity(self, entity_id: str) -> EntityResult:
        """Delete an entity and return the deleted entity."""
        ...

    # --- Read: entities ---

    @abstractmethod
    async def search_entities(
        self,
        query_vec: list[float] | None,
        query_text: str | None,
        entity_types: list[str] | None,
        limit: int,
        min_score: float,
        platform: str | None = None,
        filters: dict[str, str] | None = None,
        since: datetime | None = None,
        authored_by: list[str] | None = None,
        has_attachments: bool = False,
        order_by: str | None = None,
        content_limit: int | None = None,
        observed_since: datetime | None = None,
    ) -> list[EntityResult]:
        """Select entities by predicate, ranked by relevance or ordered by date.

        Passing ``query_text=None`` (and no vector) drops the retrieval legs, leaving
        the filters to select the rows and ``order_by`` to sort them; ``min_score`` is
        then inert. With a query, ``order_by`` still applies, so results can be
        relevance-filtered but date-sorted.
        """
        ...

    @abstractmethod
    async def get_entity_by_id(
        self, entity_id: str, content_limit: int | None = None
    ) -> EntityResult | None: ...

    @abstractmethod
    async def get_entities_by_ids(
        self, entity_ids: list[str], content_limit: int | None = None
    ) -> list[EntityResult]: ...

    @abstractmethod
    async def get_entities_by_id_prefix(
        self, prefix: str, content_limit: int | None = None
    ) -> list[EntityResult]: ...

    @abstractmethod
    async def get_entity_by_platform(
        self,
        platform: str,
        platform_entity_id: str,
        content_limit: int | None = None,
    ) -> EntityResult | None: ...

    @abstractmethod
    async def get_existing_platform_entity_ids(
        self,
        platform: str,
        platform_entity_ids: list[str],
    ) -> set[str]:
        """Return the supplied platform entity IDs that are already stored."""
        ...

    @abstractmethod
    async def list_entities(
        self,
        entity_types: list[str] | None,
        platform: str | None,
        since: datetime | None,
        limit: int,
        content_limit: int | None = None,
    ) -> list[EntityResult]: ...

    @abstractmethod
    async def list_entities_page(
        self,
        entity_types: list[str] | None,
        platform: str | None,
        since: datetime | None,
        limit: int,
        offset: int,
        order_by: str | None,
        order_dir: str,
        content_limit: int | None = None,
    ) -> tuple[list[EntityResult], int]:
        """Return an entity page and total count, optionally ordered by a supported field."""
        ...

    @abstractmethod
    async def query_by_filter(
        self,
        entity_type: str,
        filters: dict[str, str],
        limit: int,
        order_by: str,
        since: datetime | None,
        authored_by: list[str] | None,
        has_attachments: bool = False,
        content_limit: int | None = None,
    ) -> list[EntityResult]:
        """Single-type filtered read, kept for connectors that hold a backend directly.

        A narrow spelling of ``search_entities`` with no query string.
        """
        ...

    @abstractmethod
    async def list_recent_metadata_by_edge_target(
        self,
        entity_type: str,
        filters: dict[str, str],
        edge_type: str,
        target_platform: str,
        target_platform_entity_ids: list[str],
        per_target_limit: int,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return source metadata for each target's newest matching edges."""
        ...

    # --- Read: edges ---

    @abstractmethod
    async def get_edges(
        self,
        entity_id: str,
        edge_type: str | None,
        direction: str,
    ) -> list[EdgeResult]: ...

    @abstractmethod
    async def get_edges_for_entities(self, entity_ids: list[str]) -> list[EdgeResult]: ...

    @abstractmethod
    async def traverse_graph(
        self, entity_id: str, max_depth: int, content_limit: int | None = None
    ) -> dict[str, Any]: ...

    # --- Linking ---

    @abstractmethod
    async def find_entity_id(
        self, platform: str, platform_entity_id: str
    ) -> str | None: ...

    @abstractmethod
    async def upsert_stub_entity(
        self,
        entity_type: str,
        platform: str,
        platform_entity_id: str,
    ) -> str:
        """Insert a stub entity with synced_at=NULL. Returns internal UUID string."""
        ...

    @abstractmethod
    async def insert_references_edge(self, source_id: str, target_id: str) -> None: ...

    # --- Expiration ---

    @abstractmethod
    async def expire_entities(self, retention_days: float, dry_run: bool = False) -> int: ...

    # --- Sync state ---

    @abstractmethod
    async def load_cursor(self, source: str) -> dict[str, Any]: ...

    @abstractmethod
    async def save_cursor(self, source: str, cursor: dict[str, Any]) -> None: ...

    # --- Connector support ---

    @abstractmethod
    async def increment_observation_duration(
        self, platform: str, platform_entity_id: str, observation_duration_ms: int
    ) -> None:
        """Increment cumulative observation duration for an entity."""
        ...

    @abstractmethod
    async def record_observation(
        self, platform: str, platform_entity_id: str, observation_duration_ms: int
    ) -> None:
        """Record direct observation and its duration for an observable entity."""
        ...

    @abstractmethod
    async def record_observation_once(
        self,
        platform: str,
        platform_entity_id: str,
        observation_id: str,
        url: str,
        observation_duration_ms: int,
    ) -> bool:
        """Record an observation event once and return whether it was new."""
        ...

    @abstractmethod
    async def observation_exists(self, observation_id: str) -> bool:
        """Return whether an observation event has already completed."""
        ...

    @abstractmethod
    async def get_last_synced_at(
        self, platform: str, platform_entity_id: str
    ) -> datetime | None: ...

    @abstractmethod
    async def get_platform_last_synced_at(self, platform: str) -> datetime | None: ...

    async def get_platforms_last_synced_at(
        self,
        platforms: list[str],
    ) -> dict[str, datetime | None]:
        return {
            platform: await self.get_platform_last_synced_at(platform)
            for platform in platforms
        }

    @abstractmethod
    async def reset_synced_at(
        self, platform: str, platform_entity_id: str
    ) -> None: ...

    @abstractmethod
    @abstractmethod
    async def get_entity_type(
        self, platform: str, platform_entity_id: str
    ) -> str | None: ...

    @abstractmethod
    async def get_entity_platform_ref(
        self, entity_id: str
    ) -> tuple[str, str] | None:
        """Return (platform, platform_entity_id) for an internal UUID, or None."""
        ...
