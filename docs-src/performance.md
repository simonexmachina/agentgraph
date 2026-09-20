+++
title = "Performance Testing"
description = "Reproducible AgentGraph workload benchmarks for storage, API, retrieval, and the viewer."
nav_title = "Performance"
section = "Development"
order = 40
summary = "Run deterministic workloads, compare scenario-level results, and protect retrieval quality while tuning performance."
output = "performance.html"
source_path = "docs-src/performance.md"
+++

The performance suite uses deterministic generated corpora. It measures separate backend, API, and browser workloads so an improvement in one layer cannot hide a regression in another.

## Run server-side workloads

```bash
uv run python -m benchmarks.suite --entities 10000 --iterations 10 \
  --output .benchmarks/latest.json
```

The suite defaults to `sqlite-vec`, matching the production configuration. Use
`--vector-mode numpy` only to measure the fallback path explicitly.

The report records its corpus shape, vector mode, host/Python metadata, samples, percentiles, throughput, and retrieval outcomes. Use a local disposable directory; the generated databases are not application data.

The standard workloads include exact, sparse-semantic, and common-term hybrid search; filtered document retrieval; high-degree graph traversal; direct CLI operation paths; and representative viewer HTTP routes.

URL workloads check existing RSS/web identities, unknown URLs, and RSS observation
resolution. The viewer is measured both in isolation and while four clients repeatedly
check known and unknown browser URLs on the same storage backend. The measured request
is `/api/graph/nodes?limit=20&depth=1&view=graph&ordered=false&page=1&size=20`.
Two auxiliary URL entities supplement the generated corpus; every lookup checks its result.

Scheduled CI runs 1,000- and 10,000-entity corpora and fails if viewer p95 exceeds
250 ms isolated or 500 ms under concurrent URL load. Reports are retained even on
budget failure. Run the same gate locally with:

```bash
uv run python -m benchmarks.gates .benchmarks/latest.json
```

## Retrieval guardrails

Each retrieval workload may define:

- `expected_ids`: the relevance set used to calculate Recall@limit.
- `must_return_ids`: results that must remain visible for the query.

Latency wins do not pass review if a must-return result disappears or retrieval recall falls. Add a query fixture whenever search, ranking, filtering, or embedding behaviour changes.

## Compare reports

Use scenario-level p95 budgets, with a 15% default noise allowance, rather than a combined score. Compare only reports with the same corpus and vector mode:

```python
from benchmarks.compare import compare_runs
from benchmarks.models import BenchmarkRun

baseline = BenchmarkRun.model_validate_json(open(".benchmarks/baseline.json").read())
current = BenchmarkRun.model_validate_json(open(".benchmarks/latest.json").read())
for regression in compare_runs(baseline, current):
    print(regression.message)
```

Review the full table even when it does not cross a gate. A workload must not be removed merely because it became slow; it represents a user workflow that needs a deliberate budget decision.

## Browser workloads

The viewer suite measures initial render, search-to-result visibility, and list interaction through a real headless Chromium browser. It expects a separately running AgentGraph server loaded with the benchmark corpus:

```bash
cd benchmarks/frontend
npm install
npx playwright install chromium
AGENTGRAPH_BENCHMARK_URL=http://127.0.0.1:8765 npm run run
```

It writes JSON to `.benchmarks/frontend.json` by default. Run browser workloads on a fixed runner in scheduled CI, because browser and graphics timing are inherently more variable than direct storage timing.

## Adding a workload

Add deterministic corpus inputs, a named workload, and a test in `tests/test_benchmarks.py`. Specify the intended user workflow, scale, warm/cold state, and any retrieval-quality expectations. Keep external connectors and live embedding downloads out of the default workload path; measure those explicitly as separate end-to-end scenarios.
