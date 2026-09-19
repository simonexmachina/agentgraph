"""Connector and auth-provider status formatting shared by CLI and MCP."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import cast

from agentgraph.connectors.base import BaseConnector, EntityTypeDefinition
from agentgraph.core.storage import StorageBackend


def auth_provider_key(connector: BaseConnector) -> str:
    return getattr(connector, "auth_label", None) or connector.source


def connector_uses_auth(connector: BaseConnector) -> bool:
    return getattr(type(connector), "appears_in_auth_status", True)


def auth_provider_connectors(
    connectors: list[BaseConnector],
    *,
    include_non_auth: bool = False,
) -> dict[str, list[BaseConnector]]:
    grouped: dict[str, list[BaseConnector]] = {}
    for connector in connectors:
        if not include_non_auth and not connector_uses_auth(connector):
            continue
        grouped.setdefault(auth_provider_key(connector), []).append(connector)
    return grouped


def run_auth_provider_flow(
    connectors: list[BaseConnector],
    provider: str,
    *,
    account_id: str | None = None,
    add: bool = False,
    args: list[str] | None = None,
) -> None:
    """Dispatch an authentication flow through a provider's representative connector."""
    grouped = auth_provider_connectors(connectors)
    members = grouped.get(provider)
    if not members:
        available = ", ".join(sorted(grouped))
        raise ValueError(f"Unknown auth provider {provider!r}. Available: {available}")

    connector_cls = type(members[0])
    provider_args = args or []
    auth_hook = getattr(connector_cls, "run_auth_flow_with_args", None)
    if callable(auth_hook):
        auth_hook(provider_args, account_id=account_id, add=add)
        return

    auth_flow = connector_cls.run_auth_flow
    if "args" in inspect.signature(auth_flow).parameters:
        auth_flow(account_id=account_id, add=add, args=provider_args)
        return
    if provider_args:
        raise ValueError(
            f"{provider!r} does not accept auth options: {' '.join(provider_args)}"
        )
    auth_flow()


def _list_accounts(connector: BaseConnector) -> list[object]:
    list_accounts = getattr(type(connector), "list_accounts", None)
    if callable(list_accounts):
        return cast(list[object], list_accounts())
    return []


async def _verify_auth(
    connector: BaseConnector,
    account_id: str | None = None,
) -> tuple[str, str | None]:
    verify_auth = type(connector).verify_auth
    try:
        return await verify_auth(account_id)
    except TypeError:
        return await verify_auth()


def _aggregate_auth_status(statuses: list[tuple[str, str | None]]) -> tuple[str, str | None]:
    if any(status == "invalid" for status, _ in statuses):
        return next(item for item in statuses if item[0] == "invalid")
    if any(status == "ok" for status, _ in statuses):
        return ("ok", f"{len(statuses)} account(s)")
    return ("missing", None)


def _local_auth_status(
    connector: BaseConnector,
    accounts: list[object],
) -> tuple[str, str | None]:
    if accounts:
        return ("ok", f"{len(accounts)} account(s)")
    user = type(connector).get_authenticated_user()
    if user is not None:
        return ("ok", user)
    return ("missing", None)


async def auth_provider_status_items(
    connectors: list[BaseConnector],
    *,
    verify: bool = False,
    include_non_auth: bool = False,
) -> list[dict[str, object]]:
    """Build JSON-serialisable auth-provider status rows."""
    items: list[dict[str, object]] = []
    for provider, members in auth_provider_connectors(
        connectors,
        include_non_auth=include_non_auth,
    ).items():
        representative = members[0]
        accounts = _list_accounts(representative)
        account_rows: list[dict[str, object]] = []
        statuses: list[tuple[str, str | None]] = []
        for account in accounts:
            account_id = getattr(account, "account_id", None)
            status, detail = (
                await _verify_auth(representative, account_id)
                if verify
                else ("ok", getattr(account, "label", None))
            )
            account_rows.append({
                "account_id": getattr(account, "account_id", None),
                "label": getattr(account, "label", None),
                "user_id": getattr(account, "user_id", None),
                "workspace_id": getattr(account, "workspace_id", None),
                "email": getattr(account, "email", None),
                "auth_method": getattr(account, "auth_method", None),
                "auth_status": status,
                "auth_detail": detail,
            })
            statuses.append((status, detail))
        auth_status, auth_detail = (
            _aggregate_auth_status(statuses)
            if statuses
            else await _verify_auth(representative)
            if verify
            else _local_auth_status(representative, accounts)
        )
        connector_sources = [connector.source for connector in members]
        items.append({
            "provider": provider,
            "description": f"Shared auth for {', '.join(connector_sources)}"
            if len(connector_sources) > 1
            else type(representative).auth_description or provider,
            "connectors": connector_sources,
            "shared": len(connector_sources) > 1,
            "auth_status": auth_status,
            "auth_detail": auth_detail,
            "auth_verified": verify,
            "accounts": account_rows,
        })
    return items


async def connector_status_items(
    connectors: list[BaseConnector],
    backend: StorageBackend,
    *,
    verify: bool = False,
) -> list[dict[str, object]]:
    """Build JSON-serialisable connector status rows."""
    provider_items = await auth_provider_status_items(connectors, verify=verify)
    provider_by_key = {str(item["provider"]): item for item in provider_items}

    poll_delegators: dict[str, list[str]] = {}
    for connector in connectors:
        for delegated_source in type(connector).poll_delegates:
            poll_delegators.setdefault(delegated_source, []).append(connector.source)

    last_synced_by_platform = await backend.get_sources_last_synced_at(
        [connector.source for connector in connectors]
    )

    items: list[dict[str, object]] = []
    for connector in connectors:
        uses_auth = connector_uses_auth(connector)
        provider = auth_provider_key(connector) if uses_auth else None
        provider_item = provider_by_key[str(provider)] if provider is not None else None
        interval = connector.poll_interval
        polls = interval is not None
        polled_by = sorted(poll_delegators.get(connector.source, []))
        poll_delegates = list(type(connector).poll_delegates)
        last_synced_at = last_synced_by_platform.get(connector.source)
        entity_types = cast(
            tuple[EntityTypeDefinition, ...],
            getattr(type(connector), "entity_types", ()),
        )
        items.append({
            "source": connector.source,
            "description": type(connector).auth_description,
            "entity_types": [definition.model_dump() for definition in entity_types],
            "auth_provider": provider,
            "shared_auth": bool(provider_item["shared"]) if provider_item is not None else False,
            "auth_status": provider_item["auth_status"] if provider_item is not None else None,
            "auth_detail": provider_item["auth_detail"] if provider_item is not None else None,
            "auth_verified": provider_item["auth_verified"] if provider_item is not None else False,
            "account_count": len(cast(list[dict[str, object]], provider_item["accounts"]))
            if provider_item is not None
            else 0,
            "url_patterns": type(connector).url_patterns,
            "polls": polls,
            "poll_interval_seconds": int(interval.total_seconds()) if interval is not None else None,
            "poll_delegates": poll_delegates,
            "polled_by": polled_by,
            "sync": _sync_label(polls, interval, polled_by, poll_delegates),
            "last_synced_at": last_synced_at.isoformat() if last_synced_at is not None else None,
            "last_sync": _last_sync_label(last_synced_at),
        })
    return items


def _sync_label(
    polls: bool,
    interval: timedelta | None,
    polled_by: list[str],
    poll_delegates: list[str],
) -> str:
    if polls:
        assert interval is not None
        label = f"polling every {_format_interval(int(interval.total_seconds()))}"
        if poll_delegates:
            label = f"{label} for {', '.join(poll_delegates)}"
        return label
    if polled_by:
        return f"via {', '.join(polled_by)} poll"
    return "on-demand"


def _last_sync_label(last_synced_at: datetime | None) -> str:
    if last_synced_at is None:
        return "never"
    return last_synced_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def _format_interval(seconds: int) -> str:
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours}h"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes}m"
    return f"{seconds}s"
