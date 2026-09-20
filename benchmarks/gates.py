"""Absolute viewer latency budgets for scheduled benchmark reports."""

from __future__ import annotations

import argparse
from pathlib import Path

from benchmarks.models import BenchmarkRun

VIEWER_BUDGETS_MS = {"api.viewer_nodes_20": 250.0, "api.viewer_nodes_20_with_url_load": 500.0}


def check_budgets(report: BenchmarkRun) -> list[str]:
    workloads = {workload.name: workload for workload in report.workloads}
    failures: list[str] = []
    for name, budget in VIEWER_BUDGETS_MS.items():
        workload = workloads.get(name)
        if workload is None:
            failures.append(f"Missing required workload: {name}")
        elif workload.summary.p95_ms > budget:
            failures.append(f"{name}: p95 {workload.summary.p95_ms:.1f}ms exceeds {budget:.1f}ms")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    report = BenchmarkRun.model_validate_json(args.report.read_text())
    failures = check_budgets(report)
    if failures:
        raise SystemExit("\n".join(failures))
    print("Viewer latency budgets passed")


if __name__ == "__main__":
    main()
