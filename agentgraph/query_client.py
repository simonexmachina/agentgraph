"""Transport for CLI graph operations: in-process, or through the local server.

Reading through the server keeps the embedding model out of every CLI invocation and
lets connector-backed commands use the server's credentials and network access, which
a sandboxed CLI usually lacks. Reading in-process needs only the database file, so it
works where no server is reachable.

The seam is the *query* layer rather than the storage layer on purpose: search embeds
its query string before touching the backend, so an HTTP client placed below that
would still have to import the embedding model and would save nothing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

# One attempt, no retry: this is a liveness probe, and `auto` has a local fallback.
_PROBE_TIMEOUT_MULTIPLIER = 1.0

# `routes` markers from /api/capabilities this client knows how to talk to. Bumped
# whenever a route's shape changes incompatibly, so an older server is treated as
# unavailable instead of 404ing every read. See `server/graph_api.capabilities`.
_SUPPORTED_ROUTE_MARKERS = {"resource3"}

# Every URL here is plain http:// over a Unix socket or loopback, so TLS is never
# negotiated. httpx still builds an SSL context eagerly in its transport constructor,
# and the default reads certifi's cacert.pem — which agent sandboxes commonly deny,
# turning "connect to a local socket" into a PermissionError. Skipping the context
# avoids that read; it does not weaken anything, because there is no TLS to verify.
_NO_TLS = False


class QueryClient(Protocol):
    """Operations `cli_query` needs, in the shapes the in-process calls return."""

    #: Whether the caller must open a storage backend before using this client.
    needs_backend: bool
    #: Human-readable transport description, for logs and `agentgraph status`.
    label: str

    async def search(
        self,
        query: str | None,
        entity_types: list[str] | None,
        limit: int,
        min_score: float,
        platform: str | None,
        filters: dict[str, str] | None = None,
        since: str | None = None,
        authored_by_me: bool = False,
        has_attachments: bool = False,
        order_by: str | None = None,
        observed_since: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def get_entity(self, entity_id: str, resolve: bool) -> dict[str, Any] | None: ...

    async def edges(
        self,
        entity_id: str,
        edge_type: str | None,
        direction: str,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]: ...

    async def traverse(
        self,
        entity_id: str,
        max_depth: int,
        resolve: bool,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]: ...

    async def fetch(self, platform: str, resource_id: str) -> dict[str, Any]: ...

    async def fetch_entity(self, entity_id: str) -> dict[str, Any]: ...

    async def download(self, entity_id: str, output_path: str | None) -> dict[str, Any]: ...

    async def bookmark(self, target: str, bookmarked: bool) -> dict[str, Any]: ...

    async def delete(self, target: str) -> dict[str, Any]: ...

    async def delete_many(self, targets: list[str]) -> dict[str, Any]: ...

    async def unify_persons(
        self,
        canonical_id: str,
        duplicate_ids: list[str],
    ) -> dict[str, Any]: ...


class InProcessQueryClient:
    """Call the graph layer directly. Requires an initialised backend."""

    needs_backend = True
    label = "in-process"

    async def search(
        self,
        query: str | None,
        entity_types: list[str] | None,
        limit: int,
        min_score: float,
        platform: str | None,
        filters: dict[str, str] | None = None,
        since: str | None = None,
        authored_by_me: bool = False,
        has_attachments: bool = False,
        order_by: str | None = None,
        observed_since: str | None = None,
    ) -> list[dict[str, Any]]:
        from agentgraph.graph.operations import summarize_entities
        from agentgraph.graph.query import search_entities

        results = await search_entities(
            query,
            entity_types=entity_types,
            limit=limit,
            min_score=min_score,
            platform=platform,
            filters=filters,
            since=since,
            observed_since=observed_since,
            authored_by_me=authored_by_me,
            has_attachments=has_attachments,
            order_by=order_by,
        )
        return summarize_entities(results)

    async def get_entity(self, entity_id: str, resolve: bool) -> dict[str, Any] | None:
        from agentgraph.graph.operations import get_entity_details

        return await get_entity_details(entity_id, resolve=resolve)

    async def edges(
        self,
        entity_id: str,
        edge_type: str | None,
        direction: str,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        from agentgraph.graph.operations import get_entity_edges

        entity, edges = await get_entity_edges(
            entity_id,
            edge_type=edge_type,
            direction=direction,
        )
        return entity, edges

    async def traverse(
        self,
        entity_id: str,
        max_depth: int,
        resolve: bool,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        from agentgraph.graph.operations import traverse_entity

        entity, result = await traverse_entity(
            entity_id,
            max_depth=max_depth,
            resolve=resolve,
        )
        return entity, result

    async def fetch(self, platform: str, resource_id: str) -> dict[str, Any]:
        from agentgraph.graph.fetch import fetch_entity

        return await fetch_entity(platform, resource_id)

    async def fetch_entity(self, entity_id: str) -> dict[str, Any]:
        from agentgraph.graph.fetch import fetch_entity_by_id

        return await fetch_entity_by_id(entity_id)

    async def download(self, entity_id: str, output_path: str | None) -> dict[str, Any]:
        from agentgraph.graph.download import download_entity

        return await download_entity(entity_id, output_path)

    async def bookmark(self, target: str, bookmarked: bool) -> dict[str, Any]:
        from agentgraph.graph.bookmark import bookmark_target, set_entity_bookmark

        if bookmarked:
            return await bookmark_target(target)
        return await set_entity_bookmark(target, False)

    async def delete(self, target: str) -> dict[str, Any]:
        from agentgraph.graph.delete import delete_entity

        return await delete_entity(target)

    async def delete_many(self, targets: list[str]) -> dict[str, Any]:
        from agentgraph.graph.delete import delete_entities

        return await delete_entities(targets)

    async def unify_persons(
        self,
        canonical_id: str,
        duplicate_ids: list[str],
    ) -> dict[str, Any]:
        from agentgraph.graph.person import unify_persons

        return await unify_persons(canonical_id, duplicate_ids)


class HttpQueryClient:
    """Call the local server's resource routes over a Unix socket or TCP."""

    needs_backend = False

    def __init__(
        self,
        base_url: str,
        uds_path: Path | None = None,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        probe_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._uds_path = uds_path
        self._timeout = timeout
        # Tests inject an ASGI transport to exercise these routes without a server,
        # and a sync transport for the capability probe, which uses httpx.Client.
        self._transport = transport
        self._probe_transport = probe_transport
        self.label = f"server ({uds_path})" if uds_path else f"server ({base_url})"

    def _client(self) -> httpx.AsyncClient:
        import httpx

        transport = self._transport
        if transport is None and self._uds_path is not None:
            transport = httpx.AsyncHTTPTransport(uds=str(self._uds_path), verify=_NO_TLS)
        return httpx.AsyncClient(
            base_url=self._base_url,
            transport=transport,
            timeout=self._timeout,
        )

    def is_available(self, timeout: float) -> bool:
        """Return whether a server new enough to serve these routes is reachable.

        A plain connect is not enough: a server predating these routes accepts the
        connection and then 404s every call, so `auto` would hard-fail where it should
        fall back to in-process. The marker is checked too, not just the status: the
        server is launchd-managed and restarts independently of the CLI, so a CLI
        newer than its running server is routine and must fall back rather than 404.
        """
        import httpx

        transport = self._probe_transport
        if transport is None and self._uds_path is not None:
            transport = httpx.HTTPTransport(uds=str(self._uds_path), verify=_NO_TLS)
        try:
            with httpx.Client(
                base_url=self._base_url,
                transport=transport,
                timeout=timeout,
            ) as client:
                response = client.get("/api/capabilities")
                if response.status_code != 200:
                    return False
                payload: object = response.json()
                if not isinstance(payload, dict):
                    return False
                body = cast("dict[str, object]", payload)
                return body.get("routes") in _SUPPORTED_ROUTE_MARKERS
        except (httpx.HTTPError, OSError, ValueError):
            return False

    async def _request(
        self,
        method: str,
        path: str,
        missing_ok: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Issue one request, translating errors back into graph-layer exceptions.

        ``missing_ok`` turns a 404 into ``_MISSING`` so callers can mirror the
        in-process functions that return ``None`` for an absent entity.
        """
        import httpx

        try:
            async with self._client() as client:
                response = await client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            # The server went away, or was never there. Surface a builtin so callers
            # can react without importing httpx: the CLI prints the message, and the
            # long-lived MCP server re-resolves its transport and retries.
            raise ConnectionError(server_unavailable_message()) from exc
        if missing_ok and response.status_code == 404:
            return _MISSING
        if response.status_code == 400:
            # The route mapped a graph-layer exception to 400; re-raise the same class
            # so callers behave as they would in-process. Connector error hints branch
            # on the type, not just the message.
            message, error_type = _error_detail(response)
            raise _EXCEPTION_TYPES.get(error_type, RuntimeError)(message)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(_error_detail(response)[0]) from exc
        return response.json()

    async def _get(self, path: str, params: dict[str, Any], missing_ok: bool = False) -> Any:
        return await self._request("GET", path, missing_ok=missing_ok, params=_clean(params))

    async def _post(self, path: str, params: dict[str, Any], json: Any = None) -> Any:
        return await self._request("POST", path, params=_clean(params), json=json)

    async def search(
        self,
        query: str | None,
        entity_types: list[str] | None,
        limit: int,
        min_score: float,
        platform: str | None,
        filters: dict[str, str] | None = None,
        since: str | None = None,
        authored_by_me: bool = False,
        has_attachments: bool = False,
        order_by: str | None = None,
        observed_since: str | None = None,
    ) -> list[dict[str, Any]]:
        """POST because ``filters`` is an open-ended field/value mapping."""
        return cast(
            list[dict[str, Any]],
            await self._post(
                "/api/entities/search",
                {
                    "query": query,
                    "entity_types": entity_types,
                    "limit": limit,
                    "min_score": min_score,
                    "platform": platform,
                    "since": since,
                    "observed_since": observed_since,
                    "authored_by_me": authored_by_me,
                    "has_attachments": has_attachments,
                    "order_by": order_by,
                },
                json=filters or {},
            ),
        )

    async def get_entity(self, entity_id: str, resolve: bool) -> dict[str, Any] | None:
        payload = await self._get(
            f"/api/entities/{_ref(entity_id)}",
            {"resolve": resolve},
            missing_ok=True,
        )
        return None if isinstance(payload, _Missing) else cast(dict[str, Any], payload)

    async def edges(
        self,
        entity_id: str,
        edge_type: str | None,
        direction: str,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        payload = await self._get(
            f"/api/entities/{_ref(entity_id)}/edges",
            {"edge_type": edge_type, "direction": direction},
            missing_ok=True,
        )
        if isinstance(payload, _Missing):
            return None, []
        return payload["entity"], payload["edges"]

    async def traverse(
        self,
        entity_id: str,
        max_depth: int,
        resolve: bool,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        payload = await self._get(
            f"/api/graph/traverse/{_ref(entity_id)}",
            {"max_depth": max_depth, "resolve": resolve},
            missing_ok=True,
        )
        if isinstance(payload, _Missing):
            return None, {}
        return payload["entity"], payload["result"]

    async def fetch(self, platform: str, resource_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._post(
                "/api/fetches", {"platform": platform, "resource_id": resource_id}
            ),
        )

    async def fetch_entity(self, entity_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._post(f"/api/entities/{_ref(entity_id)}/fetch", {}),
        )

    async def download(self, entity_id: str, output_path: str | None) -> dict[str, Any]:
        # The server writes the file, so a relative path would land in its working
        # directory instead of the caller's.
        resolved = str(Path(output_path).expanduser().resolve()) if output_path else None
        return cast(
            dict[str, Any],
            await self._post(
                f"/api/entities/{_ref(entity_id)}/download", {"output_path": resolved}
            ),
        )

    async def bookmark(self, target: str, bookmarked: bool) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._post(
                f"/api/entities/{_ref(target)}/bookmark", {"bookmarked": bookmarked}
            ),
        )

    async def delete(self, target: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request("DELETE", f"/api/entities/{_ref(target)}"),
        )

    async def delete_many(self, targets: list[str]) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._post("/api/entities/delete", {}, json={"targets": targets}),
        )

    async def unify_persons(
        self,
        canonical_id: str,
        duplicate_ids: list[str],
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._post(
                "/api/persons/unify",
                {"canonical_id": canonical_id, "duplicate_ids": duplicate_ids},
            ),
        )


def _ref(value: str) -> str:
    """Encode an entity ref for a path segment.

    Refs can be a UUID, a ``platform/id`` path, or an http URL, so the separators are
    preserved and only the characters that would break the path are escaped.
    """
    from urllib.parse import quote

    return quote(value, safe="/:")


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    """Drop None values so the server applies its own defaults."""
    return {key: value for key, value in params.items() if value is not None}


class _Missing:
    """Distinguishes "the server said 404" from a body that is literally null."""


_MISSING = _Missing()

# Exception classes the routes report by name. Anything unrecognised becomes a
# RuntimeError, which is the safer default for an unexpected server-side failure.
_EXCEPTION_TYPES: dict[str, type[Exception]] = {
    "ValueError": ValueError,
    "RuntimeError": RuntimeError,
}


def _error_detail(response: httpx.Response) -> tuple[str, str]:
    """Return ``(message, error_type)`` from an error response.

    Routes send ``detail`` as ``{"message", "error_type"}``. A plain-string ``detail``
    is also accepted, so a server that predates that shape still yields a useful
    message rather than a repr.
    """
    try:
        payload: object = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}", ""
    if not isinstance(payload, dict):
        return str(payload), ""
    body = cast("dict[str, object]", payload)
    detail = body.get("detail")
    if isinstance(detail, dict):
        structured = cast("dict[str, object]", detail)
        message = structured.get("message")
        error_type = structured.get("error_type")
        return (
            str(message) if message is not None else str(structured),
            str(error_type) if error_type is not None else "",
        )
    if detail is not None:
        return str(detail), ""
    return str(body), ""


def _tcp_base_url(host: str, port: int) -> str:
    resolved = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    return f"http://{resolved}:{port}"


def server_unavailable_message() -> str:
    from agentgraph.config import get_settings

    settings = get_settings()
    target = settings.server_uds_path or _tcp_base_url(
        settings.server_host, settings.server_port
    )
    return (
        f"AgentGraph server is not available at {target}.\n"
        "Start it with: agentgraph serve\n"
        "Or set AGENTGRAPH_QUERY_TRANSPORT=in-process to read the database directly."
    )


def _uds_client_if_live(settings: Any) -> HttpQueryClient | None:
    from agentgraph.server import uds

    path = settings.server_uds_path
    if path is None or not uds.socket_is_live(path):
        return None
    # base_url is a placeholder: the transport dials the socket, not the host.
    client = HttpQueryClient("http://localhost", uds_path=path)
    timeout = settings.server_connect_timeout_seconds * _PROBE_TIMEOUT_MULTIPLIER
    return client if client.is_available(timeout) else None


def _tcp_client_if_live(settings: Any) -> HttpQueryClient | None:
    client = HttpQueryClient(_tcp_base_url(settings.server_host, settings.server_port))
    timeout = settings.server_connect_timeout_seconds * _PROBE_TIMEOUT_MULTIPLIER
    return client if client.is_available(timeout) else None


def resolve_query_client() -> QueryClient:
    """Pick a transport per ``query_transport``.

    ``auto`` prefers the socket, then TCP, then in-process. ``server`` refuses to fall
    back so a misconfigured server is visible rather than silently slow.
    """
    from agentgraph.config import get_settings

    settings = get_settings()
    transport = settings.query_transport

    if transport == "in-process":
        return InProcessQueryClient()

    client = _uds_client_if_live(settings) or _tcp_client_if_live(settings)
    if client is not None:
        logger.debug("Query transport: %s", client.label)
        return client

    if transport == "server":
        raise ConnectionError(server_unavailable_message())

    logger.debug("Query transport: in-process (no server reachable)")
    return InProcessQueryClient()
