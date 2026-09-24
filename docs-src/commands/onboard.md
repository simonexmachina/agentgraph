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

<aside class="heads-up">
  <p class="heads-up-label">Heads up</p>
  <p>If you see a warning "Google hasn’t verified this app" it's caused by <a href="https://issuetracker.google.com/issues/499336447">this bug in GCP</a>, and you will need to choose <em>Advanced</em> and explicitly allow access.</p>
</aside>

## Example

```bash
agentgraph onboard
```
