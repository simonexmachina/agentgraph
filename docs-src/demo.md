+++
title = "Demo – trace a decision"
description = "Use the agentgraph skill and AgentGraph CLI to trace a decision across a fictional Gmail thread, Slack discussion, Drive plan, and research documents."
nav_title = "Demo"
section = "Start"
order = 30
summary = "Install the agentgraph skill, create a fictional graph, and let a coding agent investigate a decision by searching and traversing it with the AgentGraph CLI."
output = "demo.html"
source_path = "docs-src/demo.md"
+++

This demo investigates a technical decision from a fictional Gmail thread, Slack discussion, Drive plan, and two research documents. Everything is included in a static fixture, so the demo focuses on cross-source search, graph traversal, and evidence-backed reasoning.

## The question

> Before I reply to Maya, reconstruct the Atlas synchronization decision. What did she require, what did engineering agree, does the Drive plan match, and which research supports the decision? Flag contradictions and link every source.

Without AgentGraph, a coding agent would need separate integrations and authentication for each source. With the `agentgraph` skill installed, it can use the CLI to discover the relevant entities, follow people and thread relationships, compare source dates, and return an answer with links.

## 1. Install AgentGraph and the agentgraph skill

AgentGraph expects Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Install the published package with `uv` and install the Graph skill:

```bash
uv tool install 'agentgraph-server[all]'
mkdir -p "$HOME/agentgraph-tmp"
cd "$HOME/agentgraph-tmp"
printf 'AGENTGRAPH_CONFIG_DIR=%s\n' "$PWD" > .env
agentgraph install-skill --target project
```

## 2. Add the demo fixtures

We'll use the temporary demo directory to create an isolated database for this demo:

```bash
agentgraph demo add
```

The fixture contains a set of Gmail, Slack, Drive, research, people, and relationship records. It is self-contained and does not call those services.

## 3. Ask your coding agent

Open a coding-agent session in the `~/agentgraph-tmp` directory and give it this prompt:

> Use the agentgraph skill to answer this question: Before I reply to Maya, reconstruct the Atlas synchronization decision. What did she require, what did engineering agree, does this match the plan on Drive, and which research supports the decision? Flag contradictions and link every source.

The local `.env` selects the demo database, so launch the coding agent from this directory.
It should use the installed AgentGraph skill and commands such as `agentgraph search`,
`agentgraph get`, and `agentgraph traverse` to gather context from the different sources.

### Expected evidence

The answer should identify:

- Maya's five-minute synchronization requirement and September 30 deadline from Gmail;
- engineering's decision to use webhook delivery with idempotent consumers from Slack;
- the Drive plan's stale hourly-batch proposal and conflicting October 15 date;
- the Reliable Webhooks article's advice on idempotency and replay; and
- the vendor retry guide's exponential backoff, jitter, and dead-letter guidance.

Every claim should identify and link its source. The answer should distinguish facts stated in the sources from conclusions formed by comparing them.

## 4. Open the viewer

From `~/agentgraph-tmp`, start the local server in a separate terminal:

```bash
agentgraph serve
```

Then open [http://127.0.0.1:8765/viewer](http://127.0.0.1:8765/viewer). The viewer shows the same seeded Atlas entities and relationships that the coding agent investigated. See the [viewer overview](/viewer.html) for a visual tour.

## 5. Cleanup

Stop the server with Control-C, and remove only the demo fixtures:

```bash
agentgraph demo remove
```

To configure AgentGraph for your own sources, continue with [Install](/install.html).
