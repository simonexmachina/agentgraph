"""Browser URL checks and viewer latency on the same SQLite read connection."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
from agentgraph_connector_rss import RssConnector
from agentgraph_connector_web import WebConnector

from agentgraph.backends.sqlite.backend import SQLiteBackend
from agentgraph.connectors.base import EntityBatch, EntityRecord
from agentgraph.server.router import classify_observation_url
from benchmarks.models import WorkloadResult
from benchmarks.runner import measure_workload


async def measure_url_workloads(
    backend: SQLiteBackend,
    client: httpx.AsyncClient,
    *,
    iterations: int,
    warmup_iterations: int,
) -> list[WorkloadResult]:
    url = "https://benchmark.invalid/article"
    await backend.upsert_batch(
        EntityBatch(
            entities=[
                EntityRecord(
                    entity_type="Document",
                    platform=platform,
                    platform_entity_id=url,
                    title="Known URL",
                    content="Article content",
                    metadata={"web_url": url},
                )
                for platform in ("rss", "web")
            ]
        ),
        {},
        {},
    )
    expected = await backend.find_entity_id("rss", url)
    connectors = [WebConnector(), RssConnector()]

    async def lookup(hit: bool) -> None:
        response = await client.post(
            "/api/extension/page", json={"url": url if hit else url + "/missing"}
        )
        response.raise_for_status()
        entity = response.json()["entity"]
        if hit:
            assert entity["id"] == expected and entity["platform"] == "rss"
        else:
            assert entity is None

    async def nodes() -> None:
        response = await client.get(
            "/api/graph/nodes",
            params={
                "limit": 20,
                "depth": 1,
                "view": "graph",
                "ordered": "false",
                "page": 1,
                "size": 20,
            },
        )
        response.raise_for_status()
        payload = response.json()
        assert len(payload["data"]) == min(20, payload["total"])
        assert payload["last_page"] == 1

    async def observation() -> None:
        ref = await classify_observation_url(url)
        assert ref is not None and ref.source == "rss" and ref.resource_id == url

    workloads: list[WorkloadResult] = []
    with (
        patch("agentgraph.connectors.registry.get_all_connectors", return_value=connectors),
        patch.object(
            connectors[1],
            "resolve_url",
            return_value=None,
        ),
    ):
        for name, operation in (
            ("api.url_hit", lambda: lookup(True)),
            ("api.url_miss", lambda: lookup(False)),
            ("api.viewer_nodes_20", nodes),
            ("operations.rss_observation_url", observation),
        ):
            workloads.append(
                await measure_workload(
                    name,
                    operation,
                    iterations=iterations,
                    warmup_iterations=warmup_iterations,
                    kind="api",
                )
            )

        ready = [asyncio.Event() for _ in range(4)]

        async def background(index: int) -> None:
            try:
                await lookup(True)
                ready[index].set()
                while True:
                    await lookup(False)
                    await lookup(True)
            finally:
                ready[index].set()

        workers = [asyncio.create_task(background(index)) for index in range(4)]
        try:
            await asyncio.gather(*(event.wait() for event in ready))
            workloads.append(
                await measure_workload(
                    "api.viewer_nodes_20_with_url_load",
                    nodes,
                    iterations=iterations,
                    warmup_iterations=warmup_iterations,
                    kind="api",
                )
            )
        finally:
            for worker in workers:
                worker.cancel()
            outcomes = await asyncio.gather(*workers, return_exceptions=True)
            for outcome in outcomes:
                if isinstance(outcome, BaseException) and not isinstance(
                    outcome, asyncio.CancelledError
                ):
                    raise outcome
    return workloads
