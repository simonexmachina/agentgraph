+++
title = "onboard"
description = "CLI reference for agentgraph onboard."
nav_title = "onboard"
section = "Reference"
order = 22
summary = "`agentgraph onboard` walks installed connectors with onboarding flows and launches each setup flow in sequence."
output = "commands/onboard.html"
source_path = "docs-src/commands/onboard.md"
+++

## Synopsis

```bash
agentgraph onboard
```

## Use it for

- first-time setup
- re-running auth for several platforms in one pass

Connectors without an interactive setup flow, such as generic Web, are skipped. RSS setup runs last.

## Google auth warning

The Google auth flow for AgentGraph will show an unverified/unsafe app warning because of [this bug in GCP](https://issuetracker.google.com/issues/499336447). To continue, choose _Advanced_ and  and explicitly allow access.

## Example

```bash
agentgraph onboard
```
