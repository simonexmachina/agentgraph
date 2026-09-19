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

`agentgraph onboard` may launch the Google setup flow when the Google connector is installed. AgentGraph's Google OAuth app is not verified yet, so Gmail and Google Drive auth can show Google's unverified/unsafe app warning. To continue, open the advanced option and explicitly allow access.

See [Google OAuth verification](/google-oauth-verification.html) for the current verification status and maintainer procedure.

## Example

```bash
agentgraph onboard
```
