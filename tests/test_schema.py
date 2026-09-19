"""Tests for the SQLite database schema."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import sqlite3
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agentgraph.backends.sqlite.backend import _SCHEMA_SQL, _SCHEMA_VERSION, SQLiteBackend
from agentgraph.connectors.base import EntityBatch, EntityRecord
from agentgraph.core.context import set_backend


@pytest.fixture()
async def sqlite_backend() -> AsyncGenerator[SQLiteBackend, None]:
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    set_backend(backend)
    yield backend
    await backend.close()


async def test_tables_exist(sqlite_backend: SQLiteBackend) -> None:
    rows = await sqlite_backend._fetchall(
        """
        SELECT name FROM sqlite_master
        WHERE type IN ('table', 'view')
        ORDER BY name
        """
    )
    names = {row["name"] for row in rows}
    assert {"entities", "edges", "observations", "sync_state", "entities_fts"} <= names
    assert "persons" not in names
    assert "platform_identities" not in names

    columns = await sqlite_backend._fetchall("PRAGMA table_info(entities)")
    assert "observed_at" in {row["name"] for row in columns}


async def test_get_existing_platform_entity_ids_returns_only_matching_ids(
    sqlite_backend: SQLiteBackend,
) -> None:
    await sqlite_backend.upsert_batch(
        EntityBatch(
            entities=[
                EntityRecord(
                    entity_type="Document",
                    platform="rss",
                    platform_entity_id="entry/known",
                    title="Known",
                    content="Known entry",
                ),
                EntityRecord(
                    entity_type="Document",
                    platform="web",
                    platform_entity_id="https://example.com/known",
                    title="Other platform",
                    content="Known elsewhere",
                ),
            ]
        ),
        {},
        {},
    )

    existing = await sqlite_backend.get_existing_platform_entity_ids(
        "rss",
        ["entry/known", "entry/missing", "entry/known"],
    )

    assert existing == {"entry/known"}
    assert await sqlite_backend.get_existing_platform_entity_ids("rss", []) == set()


async def test_version_four_migration_replaces_rss_metadata_index(tmp_path: Path) -> None:
    db_path = tmp_path / "v4.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA_SQL)
    conn.execute("DROP INDEX idx_edges_target_type_created_source")
    conn.execute(
        "CREATE INDEX idx_entities_platform_type_feed_updated "
        "ON entities(platform, entity_type, json_extract(metadata, '$.feed_url'), updated_at DESC)"
    )
    conn.executemany(
        """
        INSERT INTO entities (id, entity_type, platform, platform_entity_id)
        VALUES (?, ?, 'rss', ?)
        """,
        [
            ("feed", "Folder", "feed/example"),
            ("article", "Document", "entry/example"),
        ],
    )
    conn.execute(
        """
        INSERT INTO edges (id, edge_type, source_entity_id, target_entity_id, platform)
        VALUES ('edge', 'posted_in', 'article', 'feed', 'rss')
        """
    )
    conn.execute("PRAGMA user_version=4")
    conn.commit()
    conn.close()

    backend = SQLiteBackend(str(db_path), vector_mode="bm25-only")
    await backend.initialize()
    try:
        indexes = {
            str(row["name"])
            for row in await backend._fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        edge_count = await backend._fetchval("SELECT count(*) FROM edges")
        schema_version = await backend._fetchval("PRAGMA user_version")
    finally:
        await backend.close()

    assert "idx_entities_platform_type_feed_updated" not in indexes
    assert "idx_edges_target_type_created_source" in indexes
    assert edge_count == 1
    assert schema_version == _SCHEMA_VERSION


async def test_version_five_migration_replaces_edge_target_index(tmp_path: Path) -> None:
    db_path = tmp_path / "v5.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA_SQL)
    conn.execute("DROP INDEX idx_edges_target_type_created_source")
    conn.execute(
        "CREATE INDEX idx_edges_target_type_source "
        "ON edges(target_entity_id, edge_type, source_entity_id)"
    )
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    conn.close()

    backend = SQLiteBackend(str(db_path), vector_mode="bm25-only")
    await backend.initialize()
    try:
        indexes = {
            str(row["name"])
            for row in await backend._fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        schema_version = await backend._fetchval("PRAGMA user_version")
    finally:
        await backend.close()

    assert "idx_edges_target_type_source" not in indexes
    assert "idx_edges_target_type_created_source" in indexes
    assert schema_version == _SCHEMA_VERSION


async def test_schema_migration_makes_rss_folders_persistent(tmp_path: Path) -> None:
    db_path = tmp_path / "v1.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            platform TEXT NOT NULL,
            platform_entity_id TEXT NOT NULL,
            title TEXT,
            content TEXT,
            content_embedding BLOB,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source_created_at TEXT,
            source_updated_at TEXT,
            synced_at TEXT,
            observed_at TEXT,
            retention_policy TEXT NOT NULL DEFAULT 'observed'
                CHECK (retention_policy IN ('observed', 'owned', 'connected')),
            retention_parent_id TEXT,
            cumulative_observation_duration_ms INTEGER NOT NULL DEFAULT 0,
            bookmarked INTEGER NOT NULL DEFAULT 0,
            UNIQUE (platform, platform_entity_id)
        );
        PRAGMA user_version=1;
        """
    )
    conn.executemany(
        """
        INSERT INTO entities (
            id, entity_type, platform, platform_entity_id, created_at, updated_at,
            observed_at, retention_policy, cumulative_observation_duration_ms
        ) VALUES (?, ?, ?, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', ?, 'observed', 1)
        """,
        [
            ("feed", "Folder", "rss", "feed/example", "2026-01-02T00:00:00Z"),
            ("document", "Document", "web", "https://example.com", "2026-01-03T00:00:00Z"),
        ],
    )
    conn.commit()
    conn.close()

    backend = SQLiteBackend(str(db_path), vector_mode="bm25-only")
    await backend.initialize()
    try:
        feed = await backend.get_entity_by_id("feed")
        document = await backend.get_entity_by_id("document")
    finally:
        await backend.close()

    assert feed is not None
    assert feed["retention_policy"] == "persistent"
    assert feed["observed_at"] is None
    assert document is not None
    assert document["retention_policy"] == "observed"
    assert document["observed_at"] == "2026-01-03T00:00:00Z"


async def test_insert_person_and_entity_and_edge(sqlite_backend: SQLiteBackend) -> None:
    conn = sqlite_backend._conn_or_raise()
    person_id = "person-1"
    entity_id = "entity-1"
    edge_id = "edge-1"

    await conn.execute(
        """
        INSERT INTO entities (id, entity_type, platform, platform_entity_id, title, content)
        VALUES (?, 'Person', 'canonical', 'test@example.com', 'Test User', 'test@example.com')
        ON CONFLICT (platform, platform_entity_id) DO UPDATE SET title = EXCLUDED.title
        """,
        [person_id],
    )
    await conn.execute(
        """
        INSERT INTO entities (id, entity_type, platform, platform_entity_id, title, content)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (platform, platform_entity_id) DO UPDATE SET title = EXCLUDED.title
        """,
        [entity_id, "Document", "gdocs", "test-doc-001", "Test Document", "Some content here"],
    )
    await conn.execute(
        """
        INSERT INTO edges (id, edge_type, source_entity_id, target_entity_id)
        VALUES (?, ?, ?, ?)
        """,
        [edge_id, "authored", person_id, entity_id],
    )

    await conn.execute("DELETE FROM entities WHERE id = ?", [entity_id])
    count = await sqlite_backend._fetchval("SELECT count(*) FROM edges WHERE id = ?", [edge_id])
    assert count == 0


async def test_traverse_depth_zero_returns_only_the_starting_entity(
    sqlite_backend: SQLiteBackend,
) -> None:
    """Depth zero includes the focal node without expanding any relationships."""
    conn = sqlite_backend._conn_or_raise()
    await conn.executemany(
        """
        INSERT INTO entities (id, entity_type, platform, platform_entity_id, title)
        VALUES (?, 'Document', 'web', ?, ?)
        """,
        [
            ["entity-1", "document-1", "Starting entity"],
            ["entity-2", "document-2", "Neighbour"],
        ],
    )
    await conn.execute(
        """
        INSERT INTO edges (id, edge_type, source_entity_id, target_entity_id)
        VALUES ('edge-1', 'references', 'entity-1', 'entity-2')
        """
    )

    result = await sqlite_backend.traverse_graph("entity-1", max_depth=0)

    assert [node["id"] for node in result["nodes"]] == ["entity-1"]
    assert result["edges"] == []


async def test_traverse_graph_returns_each_edge_once_across_depths(
    sqlite_backend: SQLiteBackend,
) -> None:
    conn = sqlite_backend._conn_or_raise()
    await conn.executemany(
        """
        INSERT INTO entities (id, entity_type, platform, platform_entity_id)
        VALUES (?, 'Document', 'web', ?)
        """,
        [["entity-1", "document-1"], ["entity-2", "document-2"], ["entity-3", "document-3"]],
    )
    await conn.executemany(
        """
        INSERT INTO edges (id, edge_type, source_entity_id, target_entity_id)
        VALUES (?, 'references', ?, ?)
        """,
        [["edge-1", "entity-1", "entity-2"], ["edge-2", "entity-2", "entity-3"]],
    )

    result = await sqlite_backend.traverse_graph("entity-1", max_depth=2)

    assert {edge["id"] for edge in result["edges"]} == {"edge-1", "edge-2"}
    assert len(result["edges"]) == 2


async def test_get_platforms_last_synced_at_groups_platforms(sqlite_backend: SQLiteBackend) -> None:
    conn = sqlite_backend._conn_or_raise()
    await conn.executemany(
        """
        INSERT INTO entities (id, entity_type, platform, platform_entity_id, synced_at)
        VALUES (?, 'Document', ?, ?, ?)
        """,
        [
            ["entity-1", "rss", "feed-1", "2026-06-23T10:00:00+00:00"],
            ["entity-2", "rss", "feed-2", "2026-06-23T12:00:00+00:00"],
            ["entity-3", "gmail", "thread-1", "2026-06-22T09:00:00+00:00"],
        ],
    )

    result = await sqlite_backend.get_platforms_last_synced_at(["rss", "gmail", "slack"])

    rss_synced_at = result["rss"]
    gmail_synced_at = result["gmail"]
    assert rss_synced_at is not None
    assert gmail_synced_at is not None
    assert rss_synced_at.isoformat() == "2026-06-23T12:00:00+00:00"
    assert gmail_synced_at.isoformat() == "2026-06-22T09:00:00+00:00"
    assert result["slack"] is None


async def test_schema_has_platform_synced_at_index(sqlite_backend: SQLiteBackend) -> None:
    rows = await sqlite_backend._fetchall(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'index'
        """
    )
    names = {row["name"] for row in rows}
    assert "idx_entities_platform_synced_at" in names
    assert "idx_entities_type_observed_at" in names
    assert "idx_entities_type_created_at" in names
    assert "idx_entities_type_updated_at" in names
    assert "idx_entities_type_source_created_at" in names
    assert "idx_entities_type_source_updated_at" in names
    assert "idx_entities_retention_parent" in names
    assert "idx_entities_platform_type_observed_at" in names
    assert "idx_entities_observed_at_id" in names
    assert "idx_entities_type_observed_at_id" in names
    assert "idx_entities_platform_observed_at_id" in names


async def test_list_entities_page_orders_by_visible_table_columns(
    sqlite_backend: SQLiteBackend,
) -> None:
    """Server-side list ordering accepts the table's non-timestamp columns."""
    conn = sqlite_backend._conn_or_raise()
    await conn.executemany(
        """
        INSERT INTO entities (
            id, entity_type, platform, platform_entity_id, title, metadata, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ["entity-1", "Document", "zeta", "doc-1", "Zulu", "{}", "2026-01-03T00:00:00Z"],
            ["entity-2", "Message", "alpha", "message-1", "", '{"display_name":"Alpha"}', "2026-01-01T00:00:00Z"],
            ["entity-3", "Channel", "middle", "channel-1", "Middle", "{}", "2026-01-02T00:00:00Z"],
        ],
    )

    by_name, _ = await sqlite_backend.list_entities_page(
        None, None, None, 10, 0, "display_name", "asc"
    )
    by_type, _ = await sqlite_backend.list_entities_page(
        None, None, None, 10, 0, "entity_type", "asc"
    )
    by_platform, _ = await sqlite_backend.list_entities_page(
        None, None, None, 10, 0, "platform", "asc"
    )
    by_observed, _ = await sqlite_backend.list_entities_page(
        None, None, None, 10, 0, "observed_at", "desc"
    )

    assert [entity["id"] for entity in by_name] == ["entity-2", "entity-3", "entity-1"]
    assert [entity["id"] for entity in by_type] == ["entity-3", "entity-1", "entity-2"]
    assert [entity["id"] for entity in by_platform] == ["entity-2", "entity-3", "entity-1"]
    assert [entity["id"] for entity in by_observed] == ["entity-1", "entity-3", "entity-2"]


async def test_list_entities_page_can_omit_ordering(
    sqlite_backend: SQLiteBackend,
) -> None:
    """Graph pages can avoid a temporary SQL sort when row order is irrelevant."""
    with patch.object(
        sqlite_backend,
        "_fetchone",
        AsyncMock(return_value={"count": 0}),
    ), patch.object(
        sqlite_backend,
        "_fetchall",
        AsyncMock(return_value=[]),
    ) as fetchall:
        await sqlite_backend.list_entities_page(
            ["Message", "Email"], None, None, 50, 0, None, "desc"
        )

    assert fetchall.await_args is not None
    sql = fetchall.await_args.args[0]
    assert "ORDER BY" not in sql


async def test_content_projection_bounds_viewer_reads_but_preserves_detail_content(
    sqlite_backend: SQLiteBackend,
) -> None:
    """Summary projections avoid hydrating full content for viewer requests."""
    content = "x" * 320
    conn = sqlite_backend._conn_or_raise()
    await conn.execute(
        """
        INSERT INTO entities (id, entity_type, platform, platform_entity_id, title, content)
        VALUES ('entity-1', 'Document', 'web', 'document-1', 'Long document', ?)
        """,
        [content],
    )

    listed, total = await sqlite_backend.list_entities_page(
        None, None, None, 10, 0, "observed_at", "desc", content_limit=300
    )
    traversed = await sqlite_backend.traverse_graph(
        "entity-1", max_depth=0, content_limit=300
    )
    detail = await sqlite_backend.get_entity_by_id("entity-1")

    assert total == 1
    assert listed[0]["content"] == ("x" * 299) + "…"
    assert listed[0]["content_truncated"] is True
    assert traversed["nodes"][0]["content"] == listed[0]["content"]
    assert traversed["nodes"][0]["content_truncated"] is True
    assert detail is not None
    assert detail["content"] == content
    assert "content_truncated" not in detail


async def test_file_database_uses_separate_read_connection(tmp_path: Path) -> None:
    backend = SQLiteBackend(str(tmp_path / "graph.db"))
    await backend.initialize()
    try:
        assert backend._read_conn is not backend._conn
    finally:
        await backend.close()


async def test_existing_database_gets_columns_and_renames_threads_to_email(tmp_path: Path) -> None:
    db_path = tmp_path / "old-schema.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE entities (
            id                 TEXT PRIMARY KEY,
            entity_type        TEXT NOT NULL,
            platform           TEXT NOT NULL,
            platform_entity_id TEXT NOT NULL,
            title              TEXT,
            content            TEXT,
            content_embedding  BLOB,
            metadata           TEXT NOT NULL DEFAULT '{}',
            created_at         TEXT,
            updated_at         TEXT,
            synced_at          TEXT,
            last_accessed      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            cumulative_dwell_ms INTEGER NOT NULL DEFAULT 0,
            UNIQUE (platform, platform_entity_id)
        );
        CREATE VIRTUAL TABLE entities_fts USING fts5(
            id      UNINDEXED,
            title,
            content,
            tokenize='porter ascii'
        );
        CREATE TABLE observations (
            id         TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            url        TEXT NOT NULL,
            title      TEXT,
            tab_id     INTEGER,
            timestamp  TEXT NOT NULL,
            evaluated  INTEGER NOT NULL DEFAULT 0,
            meta       TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        """
    )
    conn.execute(
        """
            INSERT INTO entities (
                id, entity_type, platform, platform_entity_id, title, content, last_accessed,
                cumulative_dwell_ms
            )
            VALUES (?, 'Thread', 'gmail', 'test-thread-001', 'Test Email', 'Some content', ?, 987)
            """,
        ["entity-1", "2020-01-02T03:04:05Z"],
    )
    conn.execute(
        """
        INSERT INTO observations (id, event_type, url, timestamp, evaluated)
        VALUES ('legacy-observation', 'dwell_threshold', 'https://example.com', '2020-01-02T03:04:05Z', 1)
        """
    )
    conn.commit()
    conn.close()

    migrated = SQLiteBackend(str(db_path))
    await migrated.initialize()
    try:
        columns = await migrated._fetchall("PRAGMA table_info(entities)")
        column_names = {row["name"] for row in columns}
        assert "cumulative_dwell_ms" not in column_names
        assert "cumulative_observation_duration_ms" in column_names
        assert "observed_at" in column_names
        assert "source_created_at" in column_names
        assert "source_updated_at" in column_names
        assert "retention_policy" in column_names
        assert "retention_parent_id" in column_names

        by_name = {row["name"]: row for row in columns}
        assert by_name["created_at"]["notnull"] == 1
        assert by_name["updated_at"]["notnull"] == 1
        assert by_name["created_at"]["dflt_value"] is not None
        assert by_name["updated_at"]["dflt_value"] is not None
        assert by_name["observed_at"]["notnull"] == 0

        entity = await migrated.get_entity_by_platform("gmail", "test-thread-001")
        assert entity is not None
        assert entity["entity_type"] == "Email"
        assert entity["cumulative_observation_duration_ms"] == 987
        assert entity["created_at"] == "2020-01-02T03:04:05Z"
        assert entity["updated_at"] == "2020-01-02T03:04:05Z"
        assert entity["observed_at"] is None
        assert entity["retention_policy"] == "observed"

        await migrated.record_observation("gmail", "test-thread-001", 1234)
        updated = await migrated.get_entity_by_platform("gmail", "test-thread-001")
        assert updated is not None
        assert updated["cumulative_observation_duration_ms"] == 2221
        assert updated["observed_at"] is not None
        await migrated.upsert_batch(
            EntityBatch(
                entities=[
                    EntityRecord(
                        entity_type="Message",
                        platform="slack",
                        platform_entity_id="T/C/123.456",
                        content="A migrated database must accept new entities",
                    )
                ]
            ),
            {},
            {},
        )
        inserted = await migrated.get_entity_by_platform("slack", "T/C/123.456")
        assert inserted is not None
        assert inserted["created_at"] is not None
        assert inserted["updated_at"] is not None
        event_type = await migrated._fetchval(
            "SELECT event_type FROM observations WHERE id = ?", ["legacy-observation"]
        )
        assert event_type == "observation_threshold"
    finally:
        await migrated.close()


async def test_version_two_database_gets_timestamp_default_repair(tmp_path: Path) -> None:
    db_path = tmp_path / "missing-timestamp-defaults.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            platform TEXT NOT NULL,
            platform_entity_id TEXT NOT NULL,
            title TEXT,
            content TEXT,
            content_embedding BLOB,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source_created_at TEXT,
            source_updated_at TEXT,
            synced_at TEXT,
            observed_at TEXT,
            retention_policy TEXT NOT NULL DEFAULT 'observed'
                CHECK (retention_policy IN ('observed', 'owned', 'connected', 'persistent')),
            retention_parent_id TEXT REFERENCES entities(id) ON DELETE CASCADE,
            cumulative_observation_duration_ms INTEGER NOT NULL DEFAULT 0,
            bookmarked INTEGER NOT NULL DEFAULT 0,
            UNIQUE (platform, platform_entity_id)
        );
        CREATE VIRTUAL TABLE entities_fts USING fts5(id UNINDEXED, title, content);
        CREATE TABLE observations (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT,
            tab_id INTEGER,
            timestamp TEXT NOT NULL,
            evaluated INTEGER NOT NULL DEFAULT 0,
            meta TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        INSERT INTO entities (
            id, entity_type, platform, platform_entity_id, created_at, updated_at
        ) VALUES ('existing', 'Document', 'web', 'https://example.com', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        PRAGMA user_version=2;
        """
    )
    conn.commit()
    conn.close()

    migrated = SQLiteBackend(str(db_path))
    await migrated.initialize()
    try:
        columns = {
            row["name"]: row for row in await migrated._fetchall("PRAGMA table_info(entities)")
        }
        assert columns["created_at"]["dflt_value"] is not None
        assert columns["updated_at"]["dflt_value"] is not None
        await migrated.upsert_batch(
            EntityBatch(
                entities=[
                    EntityRecord(
                        entity_type="Message",
                        platform="slack",
                        platform_entity_id="T/C/123.456",
                    )
                ]
            ),
            {},
            {},
        )
        inserted = await migrated.get_entity_by_platform("slack", "T/C/123.456")
        assert inserted is not None
        assert inserted["created_at"] is not None
    finally:
        await migrated.close()
