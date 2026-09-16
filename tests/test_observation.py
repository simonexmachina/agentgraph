"""Tests for the /report-observation endpoint."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agentgraph.connectors.base import (
    BaseConnector,
    EntityBatch,
    EntityMetadataPatch,
    EntityRecord,
    SourceReference,
)
from agentgraph.core.context import set_backend
from agentgraph.server.app import viewer_url


@pytest.fixture
def client() -> TestClient:
    mock_backend = MagicMock()
    mock_backend.initialize = AsyncMock()
    mock_backend.close = AsyncMock()

    def backend_factory(*_: object) -> MagicMock:
        return mock_backend

    with (
        patch("agentgraph.backends.get_backend_class", return_value=backend_factory),
        patch("agentgraph.connectors.registry.bootstrap"),
        patch("agentgraph.server.app.setup_sync"),
        patch("agentgraph.server.app.AsyncIOScheduler"),
    ):
        from agentgraph.server.app import app

        return TestClient(app, raise_server_exceptions=True)


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root_redirects_to_viewer(client: TestClient) -> None:
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/viewer"


def test_viewer_url_uses_localhost_for_wildcard_hosts() -> None:
    assert viewer_url("0.0.0.0", 8765) == "http://127.0.0.1:8765/viewer"
    assert viewer_url("::", 8765) == "http://127.0.0.1:8765/viewer"


def test_viewer_url_brackets_ipv6_hosts() -> None:
    assert viewer_url("::1", 8765) == "http://[::1]:8765/viewer"


def test_report_observation_unrecognised(client: TestClient) -> None:
    resp = client.post(
        "/report-observation",
        json={
            "url": "https://example.com/unknown",
            "observation_duration_ms": 15000,
            "observation_id": "34b2ad4d-55e3-4599-bf35-1e258a704bcd",
            "observed": True,
        },
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "ignored"


def test_legacy_report_dwell_payload_is_accepted(client: TestClient) -> None:
    resp = client.post(
        "/report-dwell",
        json={
            "url": "https://example.com/unknown",
            "dwell_ms": 15_000,
            "observation_id": "34b2ad4d-55e3-4599-bf35-1e258a704bcd",
            "observed": True,
        },
    )

    assert resp.status_code == 202
    assert resp.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_meta_includes_dynamic_connector_patterns() -> None:
    from agentgraph.server.meta_api import get_meta

    connector = MagicMock()
    connector.source = "rss"
    connector.observation_url_patterns = AsyncMock(
        return_value=["https://example.com/articles/*", "https://example.com/articles/*"]
    )
    settings = MagicMock(observation_threshold_seconds=3)

    with (
        patch("agentgraph.connectors.registry.get_all_connectors", return_value=[connector]),
        patch("agentgraph.config.get_settings", return_value=settings),
    ):
        result = await get_meta()

    assert result["url_patterns"] == ["https://example.com/articles/*"]
    assert result["observation_threshold_ms"] == 3_000
    connector.observation_url_patterns.assert_awaited_once()


@pytest.mark.asyncio
async def test_meta_can_skip_dynamic_connector_patterns() -> None:
    from agentgraph.server.meta_api import get_meta

    connector = MagicMock()
    connector.source = "rss"
    connector.url_patterns = ["https://static.example.com/*"]
    connector.observation_url_patterns = AsyncMock()
    settings = MagicMock(observation_threshold_seconds=3)

    with (
        patch("agentgraph.connectors.registry.get_all_connectors", return_value=[connector]),
        patch("agentgraph.config.get_settings", return_value=settings),
    ):
        result = await get_meta(include_dynamic_url_patterns=False)

    assert result["entity_types"]
    assert result["platforms"] == ["rss"]
    assert result["url_patterns"] == ["https://static.example.com/*"]
    connector.observation_url_patterns.assert_not_awaited()


@pytest.mark.asyncio
async def test_meta_skips_slow_dynamic_connector_and_returns_remaining_patterns() -> None:
    from agentgraph.server.meta_api import get_meta

    slow_connector = MagicMock()
    slow_connector.source = "rss"

    async def slow_patterns() -> list[str]:
        await asyncio.sleep(3)
        return ["https://slow.example.com/*"]

    slow_connector.observation_url_patterns = slow_patterns
    fast_connector = MagicMock()
    fast_connector.source = "web"
    fast_connector.observation_url_patterns = AsyncMock(return_value=["http://localhost:3000/*"])
    settings = MagicMock(observation_threshold_seconds=3)

    with (
        patch(
            "agentgraph.connectors.registry.get_all_connectors",
            return_value=[slow_connector, fast_connector],
        ),
        patch("agentgraph.config.get_settings", return_value=settings),
        patch("agentgraph.server.meta_api._DYNAMIC_PATTERN_TIMEOUT_SECONDS", 0.01),
    ):
        result = await get_meta()

    assert result["url_patterns"] == ["http://localhost:3000/*"]


@pytest.mark.asyncio
async def test_rss_duration_uses_exact_observation_reference() -> None:
    from agentgraph.server.observation import record_observation

    backend = MagicMock()
    backend.observation_exists = AsyncMock(return_value=True)
    set_backend(backend)
    ref = SourceReference(
        source="rss",
        resource_type="document",
        resource_id="entry/known",
        fetch_meta={"web_url": "https://example.com/articles/known"},
    )
    with (
        patch(
            "agentgraph.server.observation.classify_observation_url",
            new=AsyncMock(return_value=ref),
        ),
    ):
        result = await record_observation(
            "https://example.com/articles/known",
            15_000,
            "observation-1",
            True,
        )

    assert result == {
        "status": "accepted",
        "source": "rss",
        "resource_type": "document",
        "observation_created": False,
    }
    backend.upsert_stub_entity.assert_not_called()


@pytest.mark.asyncio
async def test_duration_passes_metadata_to_observation_resolution() -> None:
    from agentgraph.server.observation import record_observation

    backend = MagicMock()
    backend.increment_observation_duration = AsyncMock()
    set_backend(backend)
    ref = SourceReference(source="gmail", resource_type="thread", resource_id="api-thread")
    meta = {"gmail_thread_id": "api-thread"}

    with (
        patch(
            "agentgraph.server.observation.classify_observation_url",
            new=AsyncMock(return_value=ref),
        ) as classify,
    ):
        result = await record_observation(
            "https://mail.google.com/mail/u/0/#inbox/opaque",
            15_000,
            "observation-2",
            False,
            meta,
        )

    assert result["status"] == "accepted"
    classify.assert_awaited_once_with(
        "https://mail.google.com/mail/u/0/#inbox/opaque",
        meta=meta,
    )
    backend.upsert_stub_entity.assert_not_called()
    backend.increment_observation_duration.assert_awaited_once_with("gmail", "api-thread", 15_000)


@pytest.mark.asyncio
async def test_new_observation_dispatches_once() -> None:
    from agentgraph.server.observation import record_observation

    backend = MagicMock()
    backend.observation_exists = AsyncMock(side_effect=[False, True])
    backend.get_entity_by_platform = AsyncMock(return_value={"id": "thread-entity"})
    backend.record_observation_once = AsyncMock(return_value=True)
    set_backend(backend)
    ref = SourceReference(source="gmail", resource_type="thread", resource_id="thread-1")
    # Already the connector's own form, so canonicalisation is a no-op here.
    url = "https://mail.google.com/mail/u/0/#all/thread-1"

    with (
        patch(
            "agentgraph.server.observation.classify_observation_url",
            new=AsyncMock(return_value=ref),
        ),
        patch(
            "agentgraph.server.observation._dispatch",
            new=AsyncMock(return_value={"entities": 1, "persons": 0, "edges": 0}),
        ) as dispatch,
    ):
        first = await record_observation(url, 3000, "observation-1", True)
        duplicate = await record_observation(url, 3000, "observation-1", True)
    assert first["observation_created"] is True
    assert first["fetch"] == {"entities": 1, "persons": 0, "edges": 0}
    assert duplicate["observation_created"] is False
    dispatch.assert_awaited_once_with("gmail", "thread", "thread-1", None)
    backend.record_observation_once.assert_awaited_once_with(
        "gmail",
        "thread-1",
        "observation-1",
        url,
        3000,
    )
    backend.upsert_stub_entity.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_duplicate_observation_awaits_one_fetch() -> None:
    from agentgraph.server.observation import record_observation

    backend = MagicMock()
    backend.observation_exists = AsyncMock(return_value=False)
    backend.get_entity_by_platform = AsyncMock(return_value={"id": "thread-entity"})
    backend.record_observation_once = AsyncMock(return_value=True)
    set_backend(backend)
    ref = SourceReference(source="gmail", resource_type="thread", resource_id="thread-1")
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()

    async def dispatch(*_: object, **__: object) -> dict[str, int]:
        fetch_started.set()
        await release_fetch.wait()
        return {"entities": 1, "persons": 0, "edges": 0}

    with (
        patch(
            "agentgraph.server.observation.classify_observation_url",
            new=AsyncMock(return_value=ref),
        ),
        patch("agentgraph.server.observation._dispatch", side_effect=dispatch) as mocked_dispatch,
    ):
        first_task = asyncio.create_task(
            record_observation("https://mail.google.com/thread-1", 3000, "observation-1", True)
        )
        await fetch_started.wait()
        duplicate_task = asyncio.create_task(
            record_observation("https://mail.google.com/thread-1", 3000, "observation-1", True)
        )
        await asyncio.sleep(0)
        release_fetch.set()
        first, duplicate = await asyncio.gather(first_task, duplicate_task)

    assert first["observation_created"] is True
    assert duplicate["observation_created"] is False
    assert mocked_dispatch.await_count == 1
    backend.record_observation_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_observation_does_not_create_or_mark_entity() -> None:
    from agentgraph.server.observation import ObservationFetchError, record_observation

    backend = MagicMock()
    backend.observation_exists = AsyncMock(return_value=False)
    backend.record_observation_once = AsyncMock()
    set_backend(backend)
    ref = SourceReference(source="gdrive", resource_type="folder", resource_id="folder-1")

    with (
        patch(
            "agentgraph.server.observation.classify_observation_url",
            new=AsyncMock(return_value=ref),
        ),
        patch(
            "agentgraph.server.observation._dispatch",
            new=AsyncMock(side_effect=ObservationFetchError("Drive unavailable")),
        ),
        pytest.raises(ObservationFetchError, match="Drive unavailable"),
    ):
        await record_observation(
            "https://drive.google.com/drive/folders/folder-1",
            3000,
            "observation-1",
            True,
        )

    backend.upsert_stub_entity.assert_not_called()
    backend.record_observation_once.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_observation_is_ignored_without_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from agentgraph.connectors.base import ResourceUnavailableError
    from agentgraph.server.observation import record_observation

    backend = MagicMock()
    backend.observation_exists = AsyncMock(return_value=False)
    backend.record_observation_once = AsyncMock()
    set_backend(backend)
    ref = SourceReference(source="slack", resource_type="channel", resource_id="T123/C99999")
    caplog.set_level(logging.INFO, logger="agentgraph.server.observation")

    with (
        patch(
            "agentgraph.server.observation.classify_observation_url",
            new=AsyncMock(return_value=ref),
        ),
        patch(
            "agentgraph.server.observation._dispatch",
            new=AsyncMock(
                side_effect=ResourceUnavailableError("Slack channel is unavailable to this account")
            ),
        ),
    ):
        result = await record_observation(
            "https://app.slack.com/client/T123/C99999",
            3000,
            "observation-1",
            True,
        )

    assert result == {"status": "ignored", "reason": "resource unavailable"}
    backend.record_observation_once.assert_not_awaited()
    assert "Ignoring observation for unavailable slack channel/T123/C99999" in caplog.text
    assert not [record for record in caplog.records if record.exc_info is not None]


@pytest.mark.asyncio
async def test_observation_rejects_fetch_without_persisted_target() -> None:
    from agentgraph.server.observation import ObservationFetchError, record_observation

    backend = MagicMock()
    backend.observation_exists = AsyncMock(return_value=False)
    backend.get_entity_by_platform = AsyncMock(return_value=None)
    backend.record_observation_once = AsyncMock()
    set_backend(backend)
    ref = SourceReference(source="gdrive", resource_type="folder", resource_id="folder-1")

    with (
        patch(
            "agentgraph.server.observation.classify_observation_url",
            new=AsyncMock(return_value=ref),
        ),
        patch(
            "agentgraph.server.observation._dispatch",
            new=AsyncMock(return_value={"entities": 0, "persons": 0, "edges": 0}),
        ),
        pytest.raises(ObservationFetchError, match="without persisting"),
    ):
        await record_observation(
            "https://drive.google.com/drive/folders/folder-1",
            3000,
            "observation-1",
            True,
        )

    backend.record_observation_once.assert_not_awaited()


@pytest.mark.asyncio
async def test_observation_dispatch_upserts_returned_batch() -> None:
    from agentgraph.server.observation import _dispatch  # pyright: ignore[reportPrivateUsage]

    batch = EntityBatch(
        entities=[
            EntityRecord(
                entity_type="Document",
                platform="rss",
                platform_entity_id="entry/known",
                content="Hydrated article",
            )
        ]
    )
    connector = MagicMock()
    connector.fetch = AsyncMock(return_value=batch)

    with (
        patch("agentgraph.connectors.registry.get_connector", return_value=connector),
        patch("agentgraph.graph.upsert.upsert_batch", new=AsyncMock()) as upsert_batch,
    ):
        result = await _dispatch(
            "rss",
            "document",
            "entry/known",
            {"web_url": "https://example.com/articles/known"},
        )

    connector.fetch.assert_awaited_once_with(
        resource_type="document",
        resource_id="entry/known",
        meta={"web_url": "https://example.com/articles/known"},
    )
    upsert_batch.assert_awaited_once_with(batch)
    assert result == {"entities": 1, "metadata_patches": 0, "persons": 0, "edges": 0}


@pytest.mark.asyncio
async def test_observation_dispatch_persists_metadata_patch_batch() -> None:
    from agentgraph.server.observation import _dispatch  # pyright: ignore[reportPrivateUsage]

    batch = EntityBatch(
        metadata_patches=[
            EntityMetadataPatch(
                platform="rss",
                platform_entity_id="entry/known",
                metadata={"http_etag": '"fresh"'},
            )
        ]
    )
    connector = MagicMock()
    connector.fetch = AsyncMock(return_value=batch)

    with (
        patch("agentgraph.connectors.registry.get_connector", return_value=connector),
        patch("agentgraph.graph.upsert.upsert_batch", new=AsyncMock()) as upsert_batch,
    ):
        result = await _dispatch("rss", "document", "entry/known")

    upsert_batch.assert_awaited_once_with(batch)
    assert result == {"entities": 0, "metadata_patches": 1, "persons": 0, "edges": 0}


def test_report_observation_returns_bad_gateway_for_connector_failure(client: TestClient) -> None:
    from agentgraph.server.observation import ObservationFetchError

    with patch(
        "agentgraph.server.observation.record_observation",
        new=AsyncMock(side_effect=ObservationFetchError("Connector fetch failed")),
    ):
        response = client.post(
            "/report-observation",
            json={
                "url": "https://drive.google.com/drive/folders/folder-1",
                "observation_duration_ms": 3000,
                "observation_id": "34b2ad4d-55e3-4599-bf35-1e258a704bcd",
                "observed": True,
            },
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "Connector fetch failed"}


def test_report_observation_returns_accepted_for_unavailable_resource(client: TestClient) -> None:
    with patch(
        "agentgraph.server.observation.record_observation",
        new=AsyncMock(return_value={"status": "ignored", "reason": "resource unavailable"}),
    ):
        response = client.post(
            "/report-observation",
            json={
                "url": "https://app.slack.com/client/T123/C99999",
                "observation_duration_ms": 3000,
                "observation_id": "34b2ad4d-55e3-4599-bf35-1e258a704bcd",
                "observed": True,
            },
        )

    assert response.status_code == 202
    assert response.json() == {"status": "ignored", "reason": "resource unavailable"}


_TITLED_PAGE_URL = "https://acme.atlassian.net/wiki/spaces/ENG/pages/884736/Q3+headcount+plan"
_CANONICAL_PAGE_URL = "https://acme.atlassian.net/wiki/spaces/ENG/pages/884736"


@pytest.mark.asyncio
async def test_new_observation_records_connector_url_not_browsed_url() -> None:
    from agentgraph.connectors.feed import ObservationMutation
    from agentgraph.server.observation import record_observation

    backend = MagicMock()
    backend.observation_exists = AsyncMock(return_value=False)
    backend.get_entity_by_platform = AsyncMock(return_value={"id": "page-entity"})
    backend.record_observation_once = AsyncMock(return_value=True)
    set_backend(backend)
    ref = SourceReference(
        source="twg",
        resource_type="document",
        resource_id="confluence/acme/884736",
        fetch_meta={"web_url": _CANONICAL_PAGE_URL},
    )

    with (
        patch(
            "agentgraph.server.observation.classify_observation_url",
            new=AsyncMock(return_value=ref),
        ),
        patch(
            "agentgraph.server.observation._dispatch",
            new=AsyncMock(return_value={"entities": 1, "persons": 0, "edges": 0}),
        ),
        patch("agentgraph.connectors.feed.notify_feed_connectors", new=AsyncMock()) as notify,
    ):
        result = await record_observation(_TITLED_PAGE_URL, 3000, "observation-1", True)

    assert result["observation_created"] is True
    backend.record_observation_once.assert_awaited_once_with(
        "twg",
        "confluence/acme/884736",
        "observation-1",
        _CANONICAL_PAGE_URL,
        3000,
    )
    await_args = notify.await_args
    assert await_args is not None
    event = await_args.args[0]
    assert isinstance(event, ObservationMutation)
    assert event.target.url == _CANONICAL_PAGE_URL


@pytest.mark.asyncio
async def test_duration_only_observation_publishes_connector_url() -> None:
    from agentgraph.server.observation import record_observation

    backend = MagicMock()
    backend.increment_observation_duration = AsyncMock()
    set_backend(backend)
    ref = SourceReference(
        source="twg",
        resource_type="document",
        resource_id="confluence/acme/884736",
        fetch_meta={"web_url": _CANONICAL_PAGE_URL},
    )

    with (
        patch(
            "agentgraph.server.observation.classify_observation_url",
            new=AsyncMock(return_value=ref),
        ),
        patch("agentgraph.connectors.feed.notify_feed_connectors", new=AsyncMock()) as notify,
    ):
        result = await record_observation(_TITLED_PAGE_URL, 3000, "observation-1", False)

    assert result["status"] == "accepted"
    await_args = notify.await_args
    assert await_args is not None
    assert await_args.args[0].target.url == _CANONICAL_PAGE_URL


@pytest.mark.asyncio
async def test_observation_falls_back_to_connector_entity_url() -> None:
    from agentgraph.server.observation import record_observation

    backend = MagicMock()
    backend.increment_observation_duration = AsyncMock()
    set_backend(backend)
    ref = SourceReference(
        source="twg",
        resource_type="document",
        resource_id="confluence/acme/884736",
    )
    id_only_url = "https://acme.atlassian.net/wiki/pages/viewpage.action?pageId=884736"
    connector = MagicMock()
    connector.entity_url = MagicMock(return_value=id_only_url)

    with (
        patch.object(
            type(connector),
            "entity_type_for_resource_type",
            new=BaseConnector.entity_type_for_resource_type,
            create=True,
        ),
        patch(
            "agentgraph.server.observation.classify_observation_url",
            new=AsyncMock(return_value=ref),
        ),
        patch("agentgraph.connectors.registry.get_connector", return_value=connector),
        patch("agentgraph.connectors.feed.notify_feed_connectors", new=AsyncMock()) as notify,
    ):
        await record_observation(_TITLED_PAGE_URL, 3000, "observation-1", False)

    connector.entity_url.assert_called_once_with("confluence/acme/884736")
    await_args = notify.await_args
    assert await_args is not None
    assert await_args.args[0].target.url == id_only_url


@pytest.mark.asyncio
async def test_observation_keeps_browsed_url_when_connector_cannot_canonicalise() -> None:
    from agentgraph.server.observation import record_observation

    browsed = "https://mail.google.com/mail/u/0/#inbox/opaque"
    backend = MagicMock()
    backend.increment_observation_duration = AsyncMock()
    set_backend(backend)
    ref = SourceReference(source="gmail", resource_type="thread", resource_id="thread-1")
    connector = MagicMock()
    connector.entity_url = MagicMock(return_value=None)

    with (
        patch.object(
            type(connector),
            "entity_type_for_resource_type",
            new=BaseConnector.entity_type_for_resource_type,
            create=True,
        ),
        patch(
            "agentgraph.server.observation.classify_observation_url",
            new=AsyncMock(return_value=ref),
        ),
        patch("agentgraph.connectors.registry.get_connector", return_value=connector),
        patch("agentgraph.connectors.feed.notify_feed_connectors", new=AsyncMock()) as notify,
    ):
        await record_observation(browsed, 3000, "observation-1", False)

    await_args = notify.await_args
    assert await_args is not None
    assert await_args.args[0].target.url == browsed
