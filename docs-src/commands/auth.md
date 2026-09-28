+++
title = "auth"
description = "CLI reference for agentgraph auth."
nav_title = "auth"
section = "Reference"
order = 23
summary = "`agentgraph auth` inspects, authenticates, and removes credential-backed connector platforms. Slack uses user OAuth PKCE by default with an explicit browser-session fallback."
output = "commands/auth.html"
source_path = "docs-src/commands/auth.md"
+++

## Synopsis

```bash
agentgraph auth [status] [--verify] [--json]
agentgraph auth PLATFORM [--account ACCOUNT_ID] [--add] [PROVIDER_OPTIONS]
agentgraph auth slack [--method oauth|browser] [--client-id CLIENT_ID] [--xoxc-token TOKEN] [--d-cookie VALUE]
agentgraph auth remove PLATFORM [--account ACCOUNT_ID] [--json]
```

## Notes

- `PLATFORM` is the auth label, such as `google`, `slack`, or `discord`
- omit `PLATFORM`, or pass `status`, to list provider and account authentication state;
  the default output is a table with one row per account; use `--json` for structured output
  and `--verify` only for a live provider credential check
- Google uses AgentGraph's packaged Desktop OAuth client
- Slack prompts between user OAuth with PKCE and browser-session credentials when `--method` is omitted
- OAuth asks whether you administer the target workspace; non-admins can enter an admin-provided Client ID or follow the app-request flow
- `--client-id` supplies an admin-provided Client ID and implies OAuth when `--method` is omitted
- `--method oauth` selects OAuth without the chooser; browser credential options imply `--method browser`
- `--xoxc-token` and `--d-cookie` are rejected with explicit `--method oauth`
- status account rows include `auth_method`
- `agentgraph auth remove PLATFORM` removes stored credentials for that provider; it does not delete indexed graph data
- `--account` selects an existing account to re-authenticate, or removes one stored
  account with `auth remove`
- RSS and generic web are not authentication providers; configure RSS with `agentgraph connector rss add`

## Examples

```bash
agentgraph auth status --verify --json
agentgraph auth google
agentgraph auth slack
agentgraph auth slack --add --client-id "$SLACK_CLIENT_ID"
agentgraph auth slack --method browser --xoxc-token "$XOXC" --d-cookie "$D_COOKIE"
agentgraph auth remove slack
agentgraph auth remove google --account user@example.com --json
agentgraph connector rss add https://example.com/feed.xml
```
