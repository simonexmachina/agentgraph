"""Tests for deterministic benchmark fixtures and report contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.api import run_api_suite
from benchmarks.compare import compare_runs
from benchmarks.corpus import build_seeded_corpus
from benchmarks.models import CorpusSpec, summarize_samples
from benchmarks.runner import run_backend_suite, write_report
from benchmarks.suite import run_suite


def test_corpus_is_deterministic_and_contains_a_high_degree_hub() -> None:
    spec = CorpusSpec(
        name="tiny", entity_count=20, cluster_count=4, high_degree_edges=8, batch_size=10
    )
    first_batches, first = build_seeded_corpus(spec)
    second_batches, second = build_seeded_corpus(spec)

    assert first == second
    assert first_batches == second_batches
    assert sum(len(batch.entities) for batch in first_batches) == 20
    assert sum(len(batch.edges) for batch in first_batches) >= 8


def test_latency_summary_uses_nearest_rank_percentiles() -> None:
    summary = summarize_samples([1.0, 2.0, 3.0, 4.0, 5.0])

    assert summary.p50_ms == 3.0
    assert summary.p95_ms == 5.0
    assert summary.p99_ms == 5.0


@pytest.mark.integration
async def test_backend_suite_writes_quality_checked_report(tmp_path: Path) -> None:
    spec = CorpusSpec(
        name="tiny", entity_count=30, cluster_count=5, high_degree_edges=10, batch_size=10
    )
    report = await run_backend_suite(
        tmp_path / "benchmark.db", spec, iterations=2, warmup_iterations=0
    )
    output = tmp_path / "report.json"
    write_report(report, output)

    assert {workload.name for workload in report.workloads} == {
        "ingestion.batch_25",
        "search.exact",
        "search.semantic_sparse",
        "search.common_term",
        "retrieval.filtered_documents",
        "graph.high_degree_traversal",
    }
    exact = next(workload for workload in report.workloads if workload.name == "search.exact")
    assert exact.quality is not None
    assert exact.quality.must_return_ids_present
    assert exact.quality.mrr == 1.0
    assert exact.quality.ndcg_at_limit == 1.0
    assert "sqlite.search.fts" in exact.phase_summaries
    assert "sqlite.search.hydrate" in exact.phase_summaries
    semantic = next(
        workload for workload in report.workloads if workload.name == "search.semantic_sparse"
    )
    assert semantic.quality is not None
    assert semantic.quality.recall_at_limit > 0
    assert '"schema_version": 1' in output.read_text()


@pytest.mark.integration
async def test_api_suite_exercises_direct_operations_and_viewer_routes(tmp_path: Path) -> None:
    report = await run_api_suite(
        tmp_path / "benchmark-api.db",
        CorpusSpec(
            name="tiny", entity_count=30, cluster_count=5, high_degree_edges=10, batch_size=10
        ),
        iterations=2,
        warmup_iterations=0,
    )

    assert {workload.name for workload in report.workloads} == {
        "operations.search.exact",
        "api.viewer_nodes",
        "operations.graph_traversal",
        "api.url_hit",
        "api.url_miss",
        "api.viewer_nodes_20",
        "operations.rss_observation_url",
        "api.viewer_nodes_20_with_url_load",
    }
    exact = next(
        workload for workload in report.workloads if workload.name == "operations.search.exact"
    )
    assert exact.quality is not None
    assert exact.quality.must_return_ids_present


def test_viewer_latency_gates_reject_slow_or_missing_workloads() -> None:
    from benchmarks.gates import VIEWER_BUDGETS_MS, check_budgets
    from benchmarks.models import BenchmarkRun, WorkloadResult

    report = BenchmarkRun(
        corpus=CorpusSpec(name="tiny", entity_count=30),
        vector_mode="sqlite-vec",
        cold=False,
        workloads=[],
    )
    assert len(check_budgets(report)) == 2
    for name, budget in VIEWER_BUDGETS_MS.items():
        report.workloads.append(
            WorkloadResult(
                name=name,
                kind="api",
                warmup_iterations=0,
                summary=summarize_samples([budget]),
                operations_per_second=1,
            )
        )
    assert check_budgets(report) == []
    report.workloads[1].summary.p95_ms = 501
    assert len(check_budgets(report)) == 1


@pytest.mark.integration
async def test_combined_suite_keeps_its_requested_vector_mode(tmp_path: Path) -> None:
    report = await run_suite(
        tmp_path,
        CorpusSpec(
            name="tiny", entity_count=30, cluster_count=5, high_degree_edges=10, batch_size=10
        ),
        iterations=1,
        include_api=False,
        vector_mode="bm25-only",
    )

    assert report.vector_mode == "bm25-only"


def test_compare_runs_flags_latency_and_required_result_regressions() -> None:
    spec = CorpusSpec(name="tiny", entity_count=1)
    baseline = run = None
    from benchmarks.models import BenchmarkRun, QualityResult, WorkloadResult

    baseline = BenchmarkRun(
        corpus=spec,
        vector_mode="numpy",
        cold=False,
        workloads=[
            WorkloadResult(
                name="search.exact",
                kind="backend",
                warmup_iterations=0,
                summary=summarize_samples([10.0]),
                operations_per_second=100,
                quality=QualityResult(
                    expected_ids=["a"],
                    must_return_ids=["a"],
                    returned_ids=["a"],
                    recall_at_limit=1.0,
                    mrr=1.0,
                    ndcg_at_limit=1.0,
                    must_return_ids_present=True,
                ),
            )
        ],
    )
    run = baseline.model_copy(deep=True)
    run.workloads[0].summary.p95_ms = 20.0
    run.workloads[0].quality = QualityResult(
        expected_ids=["a"],
        must_return_ids=["a"],
        returned_ids=[],
        recall_at_limit=0.0,
        mrr=0.0,
        ndcg_at_limit=0.0,
        must_return_ids_present=False,
    )

    assert {regression.metric for regression in compare_runs(baseline, run)} == {
        "p95_ms",
        "recall_at_limit",
        "ndcg_at_limit",
        "must_return_ids_present",
    }
