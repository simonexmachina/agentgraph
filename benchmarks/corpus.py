"""Deterministic corpus generation for AgentGraph benchmark workloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentgraph.backends.sqlite.backend import SQLiteBackend
from agentgraph.connectors.base import EdgeRecord, EntityBatch, EntityRecord
from benchmarks.models import CorpusSpec


@dataclass(frozen=True)
class SeededCorpus:
    """References required to exercise a seeded benchmark database."""

    spec: CorpusSpec
    hub_platform_id: str
    exact_platform_id: str
    exact_query: str
    semantic_query: str
    semantic_vector: list[float]
    semantic_expected_platform_ids: list[str]


def query_vector(cluster: int, dimensions: int) -> list[float]:
    """Return a stable unit vector whose nearest items are in ``cluster``."""
    vector = [0.0] * dimensions
    vector[cluster % dimensions] = 1.0
    return vector


def _fixture_content(index: int, cluster: int) -> str:
    """Generate repeatable short, medium, and long source-like body content."""
    word_count = (32, 192, 1_024)[index % 3]
    body = " ".join(f"context-{cluster}-{word % 17}" for word in range(word_count))
    return f"topic-{cluster} shared context semantic-cluster-{cluster} needle-{index:06d} {body}"


def build_seeded_corpus(spec: CorpusSpec) -> tuple[list[EntityBatch], SeededCorpus]:
    """Build deterministic entities and edges without connector or model dependencies."""
    batches: list[EntityBatch] = []
    base_time = datetime(2025, 1, 1, tzinfo=UTC)
    hub_platform_id = "hub-000000"
    exact_platform_id = "doc-000017"
    semantic_cluster = min(7, spec.cluster_count - 1)

    for start in range(0, spec.entity_count, spec.batch_size):
        entities: list[EntityRecord] = []
        edges: list[EdgeRecord] = []
        end = min(start + spec.batch_size, spec.entity_count)
        for index in range(start, end):
            cluster = index % spec.cluster_count
            entity_type = "Document" if index % 3 == 0 else "Message"
            platform_id = hub_platform_id if index == 0 else f"doc-{index:06d}"
            entities.append(
                EntityRecord(
                    entity_type=entity_type,
                    platform="benchmark",
                    platform_entity_id=platform_id,
                    title=f"Benchmark {entity_type} {index}",
                    content=_fixture_content(index, cluster),
                    source_created_at=base_time + timedelta(minutes=index),
                    source_updated_at=base_time + timedelta(minutes=index),
                    metadata={"cluster": cluster, "fixture": "benchmark"},
                )
            )
            if index and (index <= spec.high_degree_edges or index % 11 == 0):
                edges.append(
                    EdgeRecord(
                        edge_type="references",
                        source_platform_entity_id=hub_platform_id,
                        target_platform_entity_id=platform_id,
                        platform="benchmark",
                    )
                )
            elif index > 1:
                edges.append(
                    EdgeRecord(
                        edge_type="references",
                        source_platform_entity_id=f"doc-{index - 1:06d}",
                        target_platform_entity_id=platform_id,
                        platform="benchmark",
                    )
                )
        batches.append(EntityBatch(entities=entities, edges=edges))

    return batches, SeededCorpus(
        spec=spec,
        hub_platform_id=hub_platform_id,
        exact_platform_id=exact_platform_id,
        exact_query="needle-000017",
        semantic_query=f"unseen language for semantic cluster {semantic_cluster}",
        semantic_vector=query_vector(semantic_cluster, spec.embedding_dimensions),
        semantic_expected_platform_ids=[
            f"doc-{index:06d}"
            for index in range(spec.entity_count)
            if index % spec.cluster_count == semantic_cluster
        ],
    )


async def seed_sqlite_database(
    path: Path, spec: CorpusSpec, vector_mode: str = "numpy"
) -> SeededCorpus:
    """Create a disposable SQLite fixture database and return its workload handles."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    backend = SQLiteBackend(str(path), vector_mode=vector_mode)
    await backend.initialize()
    batches, seeded = build_seeded_corpus(spec)
    try:
        for batch in batches:
            embeddings: dict[str, list[float] | None] = {}
            for entity in batch.entities:
                if entity.is_stub:
                    continue
                cluster = entity.metadata.get("cluster")
                if not isinstance(cluster, int):
                    raise RuntimeError("Benchmark corpus entity is missing its integer cluster")
                embeddings[entity.platform_entity_id] = query_vector(
                    cluster, spec.embedding_dimensions
                )
            await backend.upsert_batch(batch, {}, embeddings)
    finally:
        await backend.close()
    return seeded
