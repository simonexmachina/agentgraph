"""Tests for backend-level expiration behavior."""

# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import sqlite3
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentgraph.backends.sqlite.backend import SQLiteBackend
from agentgraph.graph.expiration import parse_retention_window


@pytest.mark.parametrize(
    ("value", "expected_days"),
    [("30m", 30 / (24 * 60)), ("12h", 0.5), ("30d", 30.0), ("2w", 14.0)],
)
def test_parse_retention_window(value: str, expected_days: float) -> None:
    assert parse_retention_window(value) == pytest.approx(expected_days)


def test_parse_retention_window_accepts_timestamp() -> None:
    now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    assert parse_retention_window("2026-08-01T12:00:00Z", now=now) == pytest.approx(13.0)


def test_parse_retention_window_accepts_viewer_timestamp() -> None:
    now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    assert parse_retention_window("01/08/2026, 12:00:00", now=now) == pytest.approx(13.0)


def test_parse_retention_window_rejects_future_timestamp() -> None:
    now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="must be in the past"):
        parse_retention_window("2026-08-15T00:00:00Z", now=now)


@pytest.mark.parametrize("value", ["", "0d", "-1d", "30", "30months"])
def test_parse_retention_window_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_retention_window(value)


@pytest.fixture()
async def sqlite_backend() -> AsyncGenerator[SQLiteBackend, None]:
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    yield backend
    await backend.close()


async def test_sqlite_expiration_removes_stale_entities_edges_and_fts(
    sqlite_backend: SQLiteBackend,
) -> None:
    conn = sqlite_backend._conn_or_raise()
    stale_id = "stale-entity"
    fresh_id = "fresh-entity"
    stale_time = (datetime.now(UTC) - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh_time = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    await conn.execute(
        """
        INSERT INTO entities (id, entity_type, platform, platform_entity_id, title, observed_at)
        VALUES (?, 'Document', 'gdrive', 'old-file', 'Old file', ?)
        """,
        [stale_id, stale_time],
    )
    await conn.execute(
        """
        INSERT INTO entities (id, entity_type, platform, platform_entity_id, title, observed_at)
        VALUES (?, 'Document', 'gdrive', 'new-file', 'New file', ?)
        """,
        [fresh_id, fresh_time],
    )
    await conn.execute(
        "INSERT INTO entities_fts (id, title, content) VALUES (?, ?, ?)",
        [stale_id, "Old file", "stale content"],
    )
    await conn.execute(
        """
        INSERT INTO edges (id, edge_type, source_entity_id, target_entity_id, platform)
        VALUES (?, 'references', ?, ?, 'cross')
        """,
        ["edge-1", stale_id, fresh_id],
    )

    deleted = await sqlite_backend.expire_entities(90)
    assert deleted == 1

    stale_count = await sqlite_backend._fetchval(
        "SELECT count(*) FROM entities WHERE platform_entity_id = ?",
        ["old-file"],
    )
    fresh_count = await sqlite_backend._fetchval(
        "SELECT count(*) FROM entities WHERE platform_entity_id = ?",
        ["new-file"],
    )
    edge_count = await sqlite_backend._fetchval("SELECT count(*) FROM edges")
    fts_count = await sqlite_backend._fetchval(
        "SELECT count(*) FROM entities_fts WHERE id = ?",
        [stale_id],
    )

    assert stale_count == 0
    assert fresh_count == 1
    assert edge_count == 0
    assert fts_count == 0


async def test_sqlite_expiration_dry_run_does_not_change_storage(
    sqlite_backend: SQLiteBackend,
) -> None:
    stale_time = (datetime.now(UTC) - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
    await sqlite_backend._execute(
        """
        INSERT INTO entities (id, entity_type, platform, platform_entity_id, observed_at)
        VALUES ('dry-run-stale', 'Document', 'web', 'old', ?)
        """,
        [stale_time],
    )

    assert await sqlite_backend.expire_entities(90, dry_run=True) == 1
    assert await sqlite_backend.get_entity_by_id("dry-run-stale") is not None


async def test_sqlite_expiration_expires_entity_at_cutoff_boundary(
    sqlite_backend: SQLiteBackend,
) -> None:
    cutoff = datetime.now(UTC).replace(microsecond=0)
    await sqlite_backend._execute(
        """
        INSERT INTO entities (id, entity_type, platform, platform_entity_id, observed_at)
        VALUES ('boundary-stale', 'Document', 'web', 'boundary', ?)
        """,
        [cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")],
    )

    assert await sqlite_backend.expire_entities(0, dry_run=True) == 1


async def test_sqlite_expiration_keeps_bookmarked_stale_entities(
    sqlite_backend: SQLiteBackend,
) -> None:
    conn = sqlite_backend._conn_or_raise()
    stale_id = "bookmarked-stale-entity"
    stale_time = (datetime.now(UTC) - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")

    await conn.execute(
        """
        INSERT INTO entities (
            id, entity_type, platform, platform_entity_id, title, observed_at, bookmarked
        )
        VALUES (?, 'Document', 'gdrive', 'old-file', 'Old file', ?, 1)
        """,
        [stale_id, stale_time],
    )
    await conn.execute(
        "INSERT INTO entities_fts (id, title, content) VALUES (?, ?, ?)",
        [stale_id, "Old file", "stale content"],
    )

    deleted = await sqlite_backend.expire_entities(90)
    assert deleted == 0

    entity = await sqlite_backend.get_entity_by_id(stale_id)
    assert entity is not None
    assert entity["bookmarked"] is True
    fts_count = await sqlite_backend._fetchval(
        "SELECT count(*) FROM entities_fts WHERE id = ?",
        [stale_id],
    )
    assert fts_count == 1


async def test_sqlite_expiration_uses_created_at_for_never_observed_entities(
    sqlite_backend: SQLiteBackend,
) -> None:
    stale_time = (datetime.now(UTC) - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
    await sqlite_backend._execute(
        """
        INSERT INTO entities (
            id, entity_type, platform, platform_entity_id, created_at, updated_at
        ) VALUES ('never-observed', 'Document', 'web', 'https://example.com/old', ?, ?)
        """,
        [stale_time, stale_time],
    )

    assert await sqlite_backend.expire_entities(90) == 1
    assert await sqlite_backend.get_entity_by_id("never-observed") is None


@pytest.mark.parametrize("entity_type", ["Task", "Video"])
async def test_sqlite_expiration_treats_task_and_video_as_observable(
    sqlite_backend: SQLiteBackend,
    entity_type: str,
) -> None:
    stale_time = (datetime.now(UTC) - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
    await sqlite_backend._execute(
        """
        INSERT INTO entities (
            id, entity_type, platform, platform_entity_id, observed_at, bookmarked
        ) VALUES ('stale-item', ?, 'example', 'item-1', ?, 0)
        """,
        [entity_type, stale_time],
    )
    await sqlite_backend._execute(
        """
        INSERT INTO entities (
            id, entity_type, platform, platform_entity_id, observed_at, bookmarked
        ) VALUES ('bookmarked-item', ?, 'example', 'item-2', ?, 1)
        """,
        [entity_type, stale_time],
    )

    assert await sqlite_backend.expire_entities(90) == 1
    assert await sqlite_backend.get_entity_by_id("stale-item") is None
    assert await sqlite_backend.get_entity_by_id("bookmarked-item") is not None


async def test_sqlite_expiration_keeps_persistent_entities(
    sqlite_backend: SQLiteBackend,
) -> None:
    stale_time = (datetime.now(UTC) - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
    await sqlite_backend._execute(
        """
        INSERT INTO entities (
            id, entity_type, platform, platform_entity_id, created_at, updated_at,
            retention_policy
        ) VALUES ('rss-feed', 'Folder', 'rss', 'feed/example', ?, ?, 'persistent')
        """,
        [stale_time, stale_time],
    )

    assert await sqlite_backend.expire_entities(90) == 0
    assert await sqlite_backend.get_entity_by_id("rss-feed") is not None


async def test_sqlite_expiration_cascades_owned_messages_and_removes_orphan_people(
    sqlite_backend: SQLiteBackend,
) -> None:
    conn = sqlite_backend._conn_or_raise()
    stale_time = (datetime.now(UTC) - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
    await conn.execute(
        """
        INSERT INTO entities (
            id, entity_type, platform, platform_entity_id, created_at, updated_at,
            observed_at, retention_policy
        ) VALUES ('channel', 'Channel', 'slack', 'T/C', ?, ?, ?, 'observed')
        """,
        [stale_time, stale_time, stale_time],
    )
    await conn.execute(
        """
        INSERT INTO entities (
            id, entity_type, platform, platform_entity_id, retention_policy,
            retention_parent_id
        ) VALUES ('message', 'Message', 'slack', 'T/C/1', 'owned', 'channel')
        """
    )
    await conn.execute(
        """
        INSERT INTO entities (
            id, entity_type, platform, platform_entity_id, retention_policy
        ) VALUES ('person', 'Person', 'canonical', 'person@example.com', 'connected')
        """
    )
    await conn.execute(
        """
        INSERT INTO edges (id, edge_type, source_entity_id, target_entity_id, platform)
        VALUES ('authored', 'authored', 'person', 'message', 'slack')
        """
    )

    assert await sqlite_backend.expire_entities(90) == 3
    assert await sqlite_backend._fetchval("SELECT count(*) FROM entities") == 0


async def test_sqlite_expiration_preserves_bookmarked_owned_child(
    sqlite_backend: SQLiteBackend,
) -> None:
    conn = sqlite_backend._conn_or_raise()
    stale_time = (datetime.now(UTC) - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
    await conn.execute(
        """
        INSERT INTO entities (
            id, entity_type, platform, platform_entity_id, created_at, updated_at,
            observed_at
        ) VALUES ('channel', 'Channel', 'discord', 'channel', ?, ?, ?)
        """,
        [stale_time, stale_time, stale_time],
    )
    await conn.execute(
        """
        INSERT INTO entities (
            id, entity_type, platform, platform_entity_id, retention_policy,
            retention_parent_id, bookmarked
        ) VALUES ('message', 'Message', 'discord', 'channel:message', 'owned', 'channel', 1)
        """
    )
    await conn.execute(
        """
        INSERT INTO entities (
            id, entity_type, platform, platform_entity_id, retention_policy,
            retention_parent_id
        ) VALUES ('unbookmarked-message', 'Message', 'discord', 'channel:other-message', 'owned', 'channel')
        """
    )

    assert await sqlite_backend.expire_entities(90) == 2
    message = await sqlite_backend.get_entity_by_id("message")
    assert message is not None
    assert message["retention_parent_id"] is None
    assert await sqlite_backend.get_entity_by_id("unbookmarked-message") is None

    await sqlite_backend.set_entity_bookmarked("message", False)
    assert await sqlite_backend.expire_entities(90) == 1
    assert await sqlite_backend.get_entity_by_id("message") is None


async def test_sqlite_set_entity_bookmarked_returns_updated_entity(
    sqlite_backend: SQLiteBackend,
) -> None:
    conn = sqlite_backend._conn_or_raise()
    entity_id = "bookmark-target"
    await conn.execute(
        """
        INSERT INTO entities (id, entity_type, platform, platform_entity_id, title)
        VALUES (?, 'Document', 'gdrive', 'file', 'File')
        """,
        [entity_id],
    )

    before = await sqlite_backend.get_entity_by_id(entity_id)
    entity = await sqlite_backend.set_entity_bookmarked(entity_id, True)

    assert entity["id"] == entity_id
    assert entity["bookmarked"] is True
    assert before is not None
    assert entity["observed_at"] is None
    assert entity["updated_at"] >= before["updated_at"]


async def test_record_observation_only_marks_observable_entities(
    sqlite_backend: SQLiteBackend,
) -> None:
    conn = sqlite_backend._conn_or_raise()
    await conn.execute(
        """
        INSERT INTO entities (id, entity_type, platform, platform_entity_id)
        VALUES ('channel', 'Channel', 'slack', 'T/C')
        """
    )
    await conn.execute(
        """
        INSERT INTO entities (
            id, entity_type, platform, platform_entity_id, retention_policy,
            retention_parent_id
        ) VALUES ('message', 'Message', 'slack', 'T/C/1', 'owned', 'channel')
        """
    )

    await sqlite_backend.record_observation("slack", "T/C", 1000)
    await sqlite_backend.record_observation("slack", "T/C/1", 1000)

    channel = await sqlite_backend.get_entity_by_id("channel")
    message = await sqlite_backend.get_entity_by_id("message")
    assert channel is not None and channel["observed_at"] is not None
    assert channel["cumulative_observation_duration_ms"] == 1000
    assert message is not None and message["observed_at"] is None
    assert message["cumulative_observation_duration_ms"] == 0


async def test_record_observation_once_is_idempotent(
    sqlite_backend: SQLiteBackend,
) -> None:
    conn = sqlite_backend._conn_or_raise()
    await conn.execute(
        """
        INSERT INTO entities (id, entity_type, platform, platform_entity_id)
        VALUES ('email', 'Email', 'gmail', 'thread-1')
        """
    )

    first = await sqlite_backend.record_observation_once(
        "gmail", "thread-1", "observation-1", "https://mail.google.com/thread-1", 3000
    )
    exists = await sqlite_backend.observation_exists("observation-1")
    missing = await sqlite_backend.observation_exists("observation-missing")
    duplicate = await sqlite_backend.record_observation_once(
        "gmail", "thread-1", "observation-1", "https://mail.google.com/thread-1", 3000
    )

    email = await sqlite_backend.get_entity_by_id("email")
    assert first is True
    assert exists is True
    assert missing is False
    assert duplicate is False
    assert email is not None
    assert email["cumulative_observation_duration_ms"] == 3000
    observations = await sqlite_backend._fetchall(
        "SELECT id FROM observations WHERE id = ?", ["observation-1"]
    )
    assert len(observations) == 1


async def test_sqlite_delete_entity_removes_entity_edges_and_fts(
    sqlite_backend: SQLiteBackend,
) -> None:
    conn = sqlite_backend._conn_or_raise()
    entity_id = "delete-target"
    other_id = "delete-neighbour"
    await conn.execute(
        """
        INSERT INTO entities (id, entity_type, platform, platform_entity_id, title)
        VALUES (?, 'Document', 'gdrive', 'file', 'File')
        """,
        [entity_id],
    )
    await conn.execute(
        """
        INSERT INTO entities (id, entity_type, platform, platform_entity_id, title)
        VALUES (?, 'Document', 'gdrive', 'other-file', 'Other file')
        """,
        [other_id],
    )
    await conn.execute(
        "INSERT INTO entities_fts (id, title, content) VALUES (?, ?, ?)",
        [entity_id, "File", "content"],
    )
    await conn.execute(
        """
        INSERT INTO edges (id, edge_type, source_entity_id, target_entity_id, platform)
        VALUES (?, 'references', ?, ?, 'cross')
        """,
        ["delete-edge", entity_id, other_id],
    )

    deleted = await sqlite_backend.delete_entity(entity_id)

    entity_count = await sqlite_backend._fetchval(
        "SELECT count(*) FROM entities WHERE id = ?",
        [entity_id],
    )
    other_count = await sqlite_backend._fetchval(
        "SELECT count(*) FROM entities WHERE id = ?",
        [other_id],
    )
    edge_count = await sqlite_backend._fetchval("SELECT count(*) FROM edges")
    fts_count = await sqlite_backend._fetchval(
        "SELECT count(*) FROM entities_fts WHERE id = ?",
        [entity_id],
    )

    assert deleted["id"] == entity_id
    assert entity_count == 0
    assert other_count == 1
    assert edge_count == 0
    assert fts_count == 0


async def test_sqlite_delete_entities_is_atomic_and_preserves_bookmarked_children(
    sqlite_backend: SQLiteBackend,
) -> None:
    conn = sqlite_backend._conn_or_raise()
    await conn.executemany(
        """
        INSERT INTO entities (
            id, entity_type, platform, platform_entity_id, title, bookmarked, retention_parent_id
        ) VALUES (?, 'Document', 'rss', ?, ?, ?, ?)
        """,
        [
            ["parent", "feed", "Feed", 0, None],
            ["other", "other", "Other", 0, None],
            ["bookmarked", "saved", "Saved", 1, "parent"],
        ],
    )
    await conn.executemany(
        "INSERT INTO entities_fts (id, title, content) VALUES (?, ?, ?)",
        [["parent", "Feed", "feed content"], ["other", "Other", "other content"]],
    )

    with pytest.raises(ValueError, match="not found"):
        await sqlite_backend.delete_entities(["parent", "missing"])

    assert await sqlite_backend.get_entity_by_id("parent") is not None
    assert await sqlite_backend._fetchval("SELECT count(*) FROM entities_fts WHERE id = ?", ["parent"]) == 1

    deleted = await sqlite_backend.delete_entities(["parent", "other"])

    assert [entity["id"] for entity in deleted] == ["parent", "other"]
    saved = await sqlite_backend.get_entity_by_id("bookmarked")
    assert saved is not None
    assert saved["retention_parent_id"] is None
    assert await sqlite_backend._fetchval("SELECT count(*) FROM entities_fts") == 0


async def test_sqlite_migration_adds_bookmarked_before_index(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
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
                created_at TEXT,
                updated_at TEXT,
                synced_at TEXT,
                last_accessed TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                cumulative_dwell_ms INTEGER NOT NULL DEFAULT 0,
                UNIQUE (platform, platform_entity_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    backend = SQLiteBackend(str(db_path))
    await backend.initialize()
    try:
        columns = await backend._fetchall("PRAGMA table_info(entities)")
        indexes = await backend._fetchall("PRAGMA index_list(entities)")
    finally:
        await backend.close()

    assert "bookmarked" in {row["name"] for row in columns}
    assert "idx_entities_bookmarked" in {row["name"] for row in indexes}
    observed_column = next(row for row in columns if row["name"] == "observed_at")
    assert observed_column["notnull"] == 0
    assert next(row for row in columns if row["name"] == "created_at")["notnull"] == 1
    assert next(row for row in columns if row["name"] == "updated_at")["notnull"] == 1
