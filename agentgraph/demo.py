"""Create the isolated, fictional Atlas launch-demo graph."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from agentgraph.backends.sqlite.backend import SQLiteBackend
from agentgraph.connectors.base import EdgeRecord, EntityBatch, EntityRecord, PersonRecord

DEMO_DATABASE_NAME = "agentgraph.db"
DEMO_FIXTURE_METADATA_KEY = "_agentgraph_fixture"
DEMO_FIXTURE_METADATA_VALUE = "atlas-demo"
DEMO_CREATED_AT = "2026-08-14T08:00:00Z"
WEBHOOK_ARTICLE_URL = (
    "https://github.com/simonexmachina/agentgraph/blob/main/"
    "agentgraph/demo_fixtures/reliable-webhooks.md"
)
RETRY_GUIDE_URL = (
    "https://github.com/simonexmachina/agentgraph/blob/main/"
    "agentgraph/demo_fixtures/retry-guidance.md"
)


def utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def load_research_fixture(filename: str) -> str:
    return (
        files("agentgraph").joinpath("demo_fixtures", filename).read_text(encoding="utf-8").strip()
    )


def build_demo_batch() -> EntityBatch:
    """Return graph-shaped fixture data matching bundled connector output."""
    channel_id = "TDEMO/CATLAS"
    root_message_id = f"{channel_id}/1723284000.000001"
    reply_message_id = f"{channel_id}/1723285800.000002"
    decision_message_id = f"{channel_id}/1723287600.000003"
    drive_folder_id = "atlas-program-folder"
    drive_plan_id = "atlas-integration-plan"

    fixture_metadata: dict[str, str | int | float | bool | None] = {
        DEMO_FIXTURE_METADATA_KEY: DEMO_FIXTURE_METADATA_VALUE
    }
    entities = [
        EntityRecord(
            entity_type="Channel",
            platform="slack",
            platform_entity_id=channel_id,
            title="#project-atlas",
            source_created_at=utc(2026, 7, 1, 9),
            source_updated_at=utc(2026, 8, 10, 14),
            metadata={
                **fixture_metadata,
                "web_url": "https://app.slack.com/client/TDEMO/CATLAS",
                "workspace_name": "AgentGraph Demo",
            },
        ),
        EntityRecord(
            entity_type="Message",
            platform="slack",
            platform_entity_id=root_message_id,
            content=(
                "Maya confirmed that Atlas must synchronize changes within five minutes for "
                "the September 30 launch. I propose webhook delivery with an idempotent consumer. "
                f"The Reliable Webhooks article is useful background: {WEBHOOK_ARTICLE_URL} "
                f"The vendor retry guidance is here: {RETRY_GUIDE_URL}"
            ),
            source_created_at=utc(2026, 8, 10, 10),
            source_updated_at=utc(2026, 8, 10, 10),
            metadata={
                **fixture_metadata,
                "web_url": "https://app.slack.com/client/TDEMO/CATLAS/thread-1",
            },
            retention_policy="observed",
            retention_parent_platform_entity_id=channel_id,
        ),
        EntityRecord(
            entity_type="Message",
            platform="slack",
            platform_entity_id=reply_message_id,
            content=(
                "I agree on webhooks if retries use exponential backoff with jitter, events carry "
                "stable IDs, and exhausted deliveries go to a dead-letter queue with an alert."
            ),
            source_created_at=utc(2026, 8, 10, 10, 30),
            source_updated_at=utc(2026, 8, 10, 10, 30),
            metadata={
                **fixture_metadata,
                "web_url": "https://app.slack.com/client/TDEMO/CATLAS/thread-1-reply-1",
            },
            retention_policy="observed",
            retention_parent_platform_entity_id=channel_id,
        ),
        EntityRecord(
            entity_type="Message",
            platform="slack",
            platform_entity_id=decision_message_id,
            content=(
                "Decision: use webhooks, deduplicate by event ID, retain replay support, and adopt "
                "Priya's retry and dead-letter requirements. Alex owns the plan update by August 14."
            ),
            source_created_at=utc(2026, 8, 10, 11),
            source_updated_at=utc(2026, 8, 10, 11),
            metadata={
                **fixture_metadata,
                "web_url": "https://app.slack.com/client/TDEMO/CATLAS/thread-1-reply-2",
            },
            retention_policy="observed",
            retention_parent_platform_entity_id=channel_id,
        ),
        EntityRecord(
            entity_type="Email",
            platform="gmail",
            platform_entity_id="atlas-maya-thread",
            title="Atlas synchronization requirements and launch date",
            content=(
                "From: Maya Patel <maya@customer.example>\n"
                "To: Alex Chen <alex@agentgraph.demo>\n\n"
                "For the September 30 Atlas launch, customer-visible changes must appear in our "
                "system within five minutes. An hourly batch is not acceptable. Please confirm the "
                "retry behavior and send the revised integration plan before August 18."
            ),
            source_created_at=utc(2026, 8, 9, 15),
            source_updated_at=utc(2026, 8, 9, 15),
            metadata={
                **fixture_metadata,
                "web_url": "https://mail.google.com/mail/u/0/#all/atlas-maya-thread",
                "snippet": "For the September 30 Atlas launch...",
            },
        ),
        EntityRecord(
            entity_type="Folder",
            platform="gdrive",
            platform_entity_id=drive_folder_id,
            title="Atlas program",
            source_created_at=utc(2026, 7, 1, 8),
            source_updated_at=utc(2026, 8, 6, 9),
            metadata={
                **fixture_metadata,
                "web_url": "https://drive.google.com/drive/folders/atlas-program-folder",
            },
        ),
        EntityRecord(
            entity_type="Document",
            platform="gdrive",
            platform_entity_id=drive_plan_id,
            title="Atlas integration plan",
            content=(
                "Status: Draft. Last revised August 6.\n\n"
                "The integration will run an hourly batch export. Target delivery is October 15. "
                "Retries are manual and the plan does not define idempotency, replay, or a "
                "dead-letter queue."
            ),
            source_created_at=utc(2026, 8, 2, 9),
            source_updated_at=utc(2026, 8, 6, 16),
            metadata={
                **fixture_metadata,
                "web_url": "https://drive.google.com/file/d/atlas-integration-plan/view",
                "mime_type": "text/markdown",
            },
        ),
        EntityRecord(
            entity_type="Document",
            platform="web",
            platform_entity_id=WEBHOOK_ARTICLE_URL,
            title="Reliable Webhooks: Idempotency and Replay",
            content=load_research_fixture("reliable-webhooks.md"),
            source_created_at=utc(2026, 8, 4, 9),
            source_updated_at=utc(2026, 8, 4, 9),
            metadata={**fixture_metadata, "web_url": WEBHOOK_ARTICLE_URL, "author": "Nadia Okafor"},
        ),
        EntityRecord(
            entity_type="Document",
            platform="web",
            platform_entity_id=RETRY_GUIDE_URL,
            title="Atlas Platform Operations: Delivery Retry Guidance",
            content=load_research_fixture("retry-guidance.md"),
            source_created_at=utc(2026, 8, 8, 9),
            source_updated_at=utc(2026, 8, 8, 9),
            metadata={**fixture_metadata, "web_url": RETRY_GUIDE_URL, "revision": "3"},
        ),
    ]

    persons = [
        PersonRecord(
            platform="slack",
            platform_user_id="U_ALEX",
            platform_username="alex",
            canonical_email="alex@agentgraph.demo",
            display_name="Alex Chen",
            metadata=fixture_metadata,
        ),
        PersonRecord(
            platform="gdrive",
            platform_user_id="alex@agentgraph.demo",
            canonical_email="alex@agentgraph.demo",
            display_name="Alex Chen",
            metadata=fixture_metadata,
        ),
        PersonRecord(
            platform="slack",
            platform_user_id="U_PRIYA",
            platform_username="priya",
            canonical_email="priya@agentgraph.demo",
            display_name="Priya Raman",
            metadata=fixture_metadata,
        ),
        PersonRecord(
            platform="gmail",
            platform_user_id="maya@customer.example",
            canonical_email="maya@customer.example",
            display_name="Maya Patel",
            metadata=fixture_metadata,
        ),
    ]

    edges = [
        *[
            EdgeRecord(
                edge_type="posted_in",
                source_platform_entity_id=message_id,
                target_platform_entity_id=channel_id,
                platform="slack",
            )
            for message_id in (root_message_id, reply_message_id, decision_message_id)
        ],
        EdgeRecord(
            edge_type="authored",
            source_platform_user_id="U_ALEX",
            target_platform_entity_id=root_message_id,
            platform="slack",
        ),
        EdgeRecord(
            edge_type="authored",
            source_platform_user_id="U_PRIYA",
            target_platform_entity_id=reply_message_id,
            platform="slack",
        ),
        EdgeRecord(
            edge_type="authored",
            source_platform_user_id="U_ALEX",
            target_platform_entity_id=decision_message_id,
            platform="slack",
        ),
        EdgeRecord(
            edge_type="replied_to",
            source_platform_entity_id=reply_message_id,
            target_platform_entity_id=root_message_id,
            platform="slack",
        ),
        EdgeRecord(
            edge_type="replied_to",
            source_platform_entity_id=decision_message_id,
            target_platform_entity_id=root_message_id,
            platform="slack",
        ),
        EdgeRecord(
            edge_type="mentions",
            source_platform_entity_id=decision_message_id,
            target_platform_user_id="U_PRIYA",
            platform="slack",
        ),
        EdgeRecord(
            edge_type="references",
            source_platform_entity_id=root_message_id,
            target_platform_entity_id=WEBHOOK_ARTICLE_URL,
            platform="cross",
        ),
        EdgeRecord(
            edge_type="references",
            source_platform_entity_id=root_message_id,
            target_platform_entity_id=RETRY_GUIDE_URL,
            platform="cross",
        ),
        EdgeRecord(
            edge_type="authored",
            source_platform_user_id="maya@customer.example",
            target_platform_entity_id="atlas-maya-thread",
            platform="gmail",
        ),
        EdgeRecord(
            edge_type="participated_in",
            source_platform_user_id="alex@agentgraph.demo",
            target_platform_entity_id="atlas-maya-thread",
            platform="gmail",
        ),
        EdgeRecord(
            edge_type="contains",
            source_platform_entity_id=drive_folder_id,
            target_platform_entity_id=drive_plan_id,
            platform="gdrive",
        ),
        EdgeRecord(
            edge_type="authored",
            source_platform_user_id="alex@agentgraph.demo",
            target_platform_entity_id=drive_plan_id,
            platform="gdrive",
        ),
    ]
    return EntityBatch(entities=entities, persons=persons, edges=edges)


def apply_demo_timestamps(database_path: Path) -> None:
    observations = {
        ("slack", "TDEMO/CATLAS"): "2026-08-10T11:05:00Z",
        ("gmail", "atlas-maya-thread"): "2026-08-09T15:10:00Z",
        ("gdrive", "atlas-program-folder"): "2026-08-06T16:05:00Z",
        ("gdrive", "atlas-integration-plan"): "2026-08-06T16:05:00Z",
    }
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """
            UPDATE entities
            SET created_at = ?, updated_at = ?, synced_at = ?
            WHERE json_extract(metadata, ?) = ?
            """,
            [
                DEMO_CREATED_AT,
                DEMO_CREATED_AT,
                DEMO_CREATED_AT,
                f"$.{DEMO_FIXTURE_METADATA_KEY}",
                DEMO_FIXTURE_METADATA_VALUE,
            ],
        )
        for (platform, platform_entity_id), observed_at in observations.items():
            conn.execute(
                """
                UPDATE entities
                SET observed_at = ?, cumulative_observation_duration_ms = 3000
                WHERE platform = ?
                  AND platform_entity_id = ?
                  AND json_extract(metadata, ?) = ?
                """,
                [
                    observed_at,
                    platform,
                    platform_entity_id,
                    f"$.{DEMO_FIXTURE_METADATA_KEY}",
                    DEMO_FIXTURE_METADATA_VALUE,
                ],
            )


async def add_demo(config_dir: Path) -> dict[str, object]:
    database_path = config_dir / DEMO_DATABASE_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    batch = build_demo_batch()
    backend = SQLiteBackend(str(database_path), vector_mode="bm25-only")
    await backend.initialize()
    try:
        await backend.upsert_batch(batch, person_embeddings={}, entity_embeddings={})
    finally:
        await backend.close()
    apply_demo_timestamps(database_path)
    entity_count = len(batch.entities)
    person_count = len(
        {p.canonical_email or f"{p.platform}:{p.platform_user_id}" for p in batch.persons}
    )
    edge_count = len(batch.edges)
    return {
        "database": str(database_path),
        "entities": entity_count,
        "persons": person_count,
        "edges": edge_count,
        "webhook_article": WEBHOOK_ARTICLE_URL,
        "retry_guide": RETRY_GUIDE_URL,
    }


async def remove_demo(config_dir: Path) -> dict[str, object]:
    database_path = config_dir / DEMO_DATABASE_NAME
    if not database_path.exists():
        return {"database": str(database_path), "removed": 0}

    with sqlite3.connect(database_path) as conn:
        rows = conn.execute(
            """
            SELECT id FROM entities
            WHERE json_extract(metadata, ?) = ?
            """,
            [f"$.{DEMO_FIXTURE_METADATA_KEY}", DEMO_FIXTURE_METADATA_VALUE],
        ).fetchall()
    entity_ids = [str(row[0]) for row in rows]

    backend = SQLiteBackend(str(database_path), vector_mode="bm25-only")
    await backend.initialize()
    try:
        for entity_id in entity_ids:
            if await backend.get_entity_by_id(entity_id) is not None:
                await backend.delete_entity(entity_id)
    finally:
        await backend.close()
    return {"database": str(database_path), "removed": len(entity_ids)}
