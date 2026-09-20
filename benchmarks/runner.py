"""Backend benchmark execution and JSON report generation."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from math import log2
from pathlib import Path
from time import perf_counter
from typing import Literal, cast

from agentgraph.backends.sqlite.backend import SQLiteBackend
from agentgraph.connectors.base import EntityBatch, EntityRecord
from agentgraph.perf import capture_timings
from benchmarks.corpus import (
    SeededCorpus,
    build_seeded_corpus,
    query_vector,
    seed_sqlite_database,
)
from benchmarks.models import (
    BenchmarkRun,
    CorpusSpec,
    QualityResult,
    WorkloadResult,
    summarize_samples,
)

Operation = Callable[[], Awaitable[object]]


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


async def measure_workload(
    name: str,
    operation: Operation,
    *,
    iterations: int,
    warmup_iterations: int,
    kind: Literal["backend", "api", "frontend"] = "backend",
    quality: Callable[[object], QualityResult] | None = None,
) -> WorkloadResult:
    for _ in range(warmup_iterations):
        await operation()
    samples_ms: list[float] = []
    phase_samples: dict[str, list[float]] = {}
    final_result: object = None
    for _ in range(iterations):
        start = perf_counter()
        with capture_timings() as timings:
            final_result = await operation()
        samples_ms.append((perf_counter() - start) * 1000)
        for phase, elapsed_ms in timings:
            phase_samples.setdefault(phase, []).append(elapsed_ms)
    total_seconds = sum(samples_ms) / 1000
    return WorkloadResult(
        name=name,
        kind=kind,
        warmup_iterations=warmup_iterations,
        summary=summarize_samples(samples_ms),
        operations_per_second=iterations / total_seconds if total_seconds else 0,
        phase_summaries={
            phase: summarize_samples(samples) for phase, samples in phase_samples.items()
        },
        quality=quality(final_result) if quality else None,
    )


def evaluate_search_quality(
    expected_ids: list[str], *, must_return_ids: list[str] | None = None
) -> Callable[[object], QualityResult]:
    def evaluate(result: object) -> QualityResult:
        rows = cast(list[object], result) if isinstance(result, list) else []
        returned_ids = [
            str(cast(dict[str, object], row)["platform_entity_id"])
            for row in rows
            if isinstance(row, dict)
            and isinstance(cast(dict[str, object], row).get("platform_entity_id"), str)
        ]
        required = must_return_ids or []
        relevant = set(expected_ids)
        recall = sum(result_id in relevant for result_id in returned_ids) / len(relevant)
        relevant_ranks = [
            rank for rank, result_id in enumerate(returned_ids, start=1) if result_id in relevant
        ]
        mrr = 1 / relevant_ranks[0] if relevant_ranks else 0.0
        dcg = sum(1 / log2(rank + 1) for rank in relevant_ranks)
        ideal_count = min(len(relevant), len(returned_ids))
        ideal_dcg = sum(1 / log2(rank + 1) for rank in range(1, ideal_count + 1))
        return QualityResult(
            expected_ids=expected_ids,
            must_return_ids=required,
            returned_ids=returned_ids,
            recall_at_limit=recall,
            mrr=mrr,
            ndcg_at_limit=dcg / ideal_dcg if ideal_dcg else 0.0,
            must_return_ids_present=all(result_id in returned_ids for result_id in required),
        )

    return evaluate


async def run_backend_suite(
    database_path: Path,
    spec: CorpusSpec,
    *,
    iterations: int = 10,
    warmup_iterations: int = 3,
    vector_mode: str = "numpy",
    seed: bool = True,
) -> BenchmarkRun:
    """Seed and execute the standard direct-storage workload suite."""
    seeded: SeededCorpus
    if seed:
        seeded = await seed_sqlite_database(database_path, spec, vector_mode)
    else:
        _, seeded = build_seeded_corpus(spec)

    backend = SQLiteBackend(str(database_path), vector_mode=vector_mode)
    await backend.initialize()
    try:
        exact_vector = query_vector(17 % spec.cluster_count, spec.embedding_dimensions)
        ingestion_sequence = 0

        async def ingest_batch() -> None:
            nonlocal ingestion_sequence
            start = ingestion_sequence * 25
            ingestion_sequence += 1
            entities = [
                EntityRecord(
                    entity_type="Document",
                    platform="benchmark-ingest",
                    platform_entity_id=f"ingest-{start + offset:08d}",
                    title=f"Ingest benchmark {start + offset}",
                    content=f"ingest topic-{offset % spec.cluster_count} fixture",
                    source_created_at=datetime.now(UTC),
                )
                for offset in range(25)
            ]
            embeddings: dict[str, list[float] | None] = {
                entity.platform_entity_id: query_vector(
                    offset % spec.cluster_count, spec.embedding_dimensions
                )
                for offset, entity in enumerate(entities)
            }
            await backend.upsert_batch(EntityBatch(entities=entities), {}, embeddings)

        hub = await backend.get_entity_by_platform("benchmark", seeded.hub_platform_id)
        if hub is None:
            raise RuntimeError("Seeded benchmark hub was not persisted")
        workloads = [
            await measure_workload(
                "ingestion.batch_25",
                ingest_batch,
                iterations=iterations,
                warmup_iterations=warmup_iterations,
            ),
            await measure_workload(
                "search.exact",
                lambda: backend.search_entities(exact_vector, seeded.exact_query, None, 10, 0.0),
                iterations=iterations,
                warmup_iterations=warmup_iterations,
                quality=evaluate_search_quality(
                    [seeded.exact_platform_id], must_return_ids=[seeded.exact_platform_id]
                ),
            ),
            await measure_workload(
                "search.semantic_sparse",
                lambda: backend.search_entities(
                    seeded.semantic_vector, seeded.semantic_query, None, 10, 0.0
                ),
                iterations=iterations,
                warmup_iterations=warmup_iterations,
                quality=evaluate_search_quality(seeded.semantic_expected_platform_ids),
            ),
            await measure_workload(
                "search.common_term",
                lambda: backend.search_entities(exact_vector, "shared context", None, 10, 0.0),
                iterations=iterations,
                warmup_iterations=warmup_iterations,
            ),
            await measure_workload(
                "retrieval.filtered_documents",
                lambda: backend.query_by_filter(
                    "Document", {"platform": "benchmark"}, 50, "last_accessed", None, None
                ),
                iterations=iterations,
                warmup_iterations=warmup_iterations,
            ),
            await measure_workload(
                "graph.high_degree_traversal",
                lambda: backend.traverse_graph(str(hub["id"]), 2),
                iterations=iterations,
                warmup_iterations=warmup_iterations,
            ),
        ]
    finally:
        await backend.close()
    return BenchmarkRun(
        git_sha=_git_sha(),
        corpus=spec,
        vector_mode=vector_mode,
        cold=False,
        workloads=workloads,
    )


def write_report(report: BenchmarkRun, output_path: Path) -> None:
    """Atomically write a portable, schema-versioned benchmark report."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(report.model_dump_json(indent=2) + "\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(output_path)


def main() -> None:
    """Run a local medium corpus benchmark via ``python -m benchmarks.runner``."""
    import argparse

    parser = argparse.ArgumentParser(description="Run AgentGraph backend benchmarks")
    parser.add_argument("--database", type=Path, default=Path(".benchmarks/medium.db"))
    parser.add_argument("--output", type=Path, default=Path(".benchmarks/latest.json"))
    parser.add_argument("--entities", type=int, default=10_000)
    parser.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args()
    report = asyncio.run(
        run_backend_suite(
            args.database,
            CorpusSpec(
                name=f"generated-{args.entities}", entity_count=args.entities, high_degree_edges=250
            ),
            iterations=args.iterations,
        )
    )
    write_report(report, args.output)


if __name__ == "__main__":
    main()
