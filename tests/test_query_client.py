"""Tests for transport selection."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from agentgraph import query_client
from agentgraph.query_client import (
    HttpQueryClient,
    InProcessQueryClient,
    resolve_query_client,
)

SOCKET = Path("/tmp/ag-test.sock")


def _uds_client(_settings: Any) -> HttpQueryClient:
    return HttpQueryClient("http://localhost", uds_path=SOCKET)


def _tcp_client(_settings: Any) -> HttpQueryClient:
    return HttpQueryClient("http://127.0.0.1:8765")


def _no_client(_settings: Any) -> HttpQueryClient | None:
    return None


def _reset_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    import agentgraph.config as cfg

    monkeypatch.setattr(cfg, "_settings", None)


@pytest.fixture
def no_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither transport is reachable."""
    monkeypatch.setattr(query_client, "_uds_client_if_live", _no_client)
    monkeypatch.setattr(query_client, "_tcp_client_if_live", _no_client)


@pytest.fixture
def uds_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(query_client, "_uds_client_if_live", _uds_client)
    monkeypatch.setattr(query_client, "_tcp_client_if_live", _tcp_client)


def test_in_process_transport_never_probes_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_settings: Any) -> HttpQueryClient | None:
        raise AssertionError("in-process must not probe the server")

    monkeypatch.setenv("AGENTGRAPH_QUERY_TRANSPORT", "in-process")
    monkeypatch.setattr(query_client, "_uds_client_if_live", fail)
    monkeypatch.setattr(query_client, "_tcp_client_if_live", fail)
    _reset_settings(monkeypatch)

    assert isinstance(resolve_query_client(), InProcessQueryClient)


def test_auto_falls_back_to_in_process_when_no_server(
    no_server: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTGRAPH_QUERY_TRANSPORT", "auto")
    _reset_settings(monkeypatch)

    assert isinstance(resolve_query_client(), InProcessQueryClient)


def test_server_transport_raises_instead_of_falling_back(
    no_server: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A misconfigured server must be visible, not silently slow."""
    monkeypatch.setenv("AGENTGRAPH_QUERY_TRANSPORT", "server")
    _reset_settings(monkeypatch)

    with pytest.raises(ConnectionError, match="agentgraph serve"):
        resolve_query_client()


def test_auto_prefers_the_socket_over_tcp(
    uds_server: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The socket crosses sandboxes that deny loopback TCP, so it wins."""
    monkeypatch.setenv("AGENTGRAPH_QUERY_TRANSPORT", "auto")
    _reset_settings(monkeypatch)

    client = resolve_query_client()

    assert isinstance(client, HttpQueryClient)
    assert str(SOCKET) in client.label


def test_auto_uses_tcp_when_the_socket_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTGRAPH_QUERY_TRANSPORT", "auto")
    monkeypatch.setattr(query_client, "_uds_client_if_live", _no_client)
    monkeypatch.setattr(query_client, "_tcp_client_if_live", _tcp_client)
    _reset_settings(monkeypatch)

    client = resolve_query_client()

    assert isinstance(client, HttpQueryClient)
    assert "8765" in client.label


def test_uds_probe_ignores_a_dead_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentgraph.server import uds

    def dead(_path: Path) -> bool:
        return False

    monkeypatch.setattr(uds, "socket_is_live", dead)

    assert query_client._uds_client_if_live(SimpleNamespace(server_uds_path=SOCKET)) is None


def test_uds_probe_skips_when_socket_is_disabled() -> None:
    assert query_client._uds_client_if_live(SimpleNamespace(server_uds_path=None)) is None


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (200, {"status": "ok", "routes": "resource3"}, True),
        # A server new enough to answer the probe but too old to serve the reshaped
        # search route: `auto` must fall back in-process rather than 404 every read.
        (200, {"status": "ok", "routes": "resource"}, False),
        (200, {"status": "ok"}, False),
        (404, {}, False),
        (500, {}, False),
    ],
    ids=["current", "older-routes", "no-marker", "404", "500"],
)
def test_is_available_requires_the_resource_routes(
    status: int, payload: dict[str, str], expected: bool
) -> None:
    """A server predating these routes 404s the probe, so it must not be selected."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/capabilities"
        return httpx.Response(status, json=payload)

    client = HttpQueryClient("http://t", probe_transport=httpx.MockTransport(handler))

    assert client.is_available(0.2) is expected


def test_socket_transports_skip_the_ssl_context() -> None:
    """httpx builds an SSL context eagerly, reading certifi's cacert.pem.

    Sandboxes commonly deny reading *.pem, which turned a plain-HTTP connection to a
    local socket into a PermissionError. These URLs are never https, so there is no
    TLS to verify.
    """
    seen: list[object] = []

    class Recorder:
        """Stands in for both the transport and the client that wraps it."""

        def __init__(self, **kwargs: Any) -> None:
            if "verify" in kwargs:
                seen.append(kwargs["verify"])

        def __enter__(self) -> Recorder:
            return self

        def __exit__(self, *_: object) -> bool:
            return False

        def get(self, _path: str) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok", "routes": "resource3"})

    client = HttpQueryClient("http://localhost", uds_path=Path("/tmp/ag.sock"))

    with patch("httpx.AsyncHTTPTransport", Recorder), patch("httpx.AsyncClient", Recorder):
        client._client()
    with patch("httpx.HTTPTransport", Recorder), patch("httpx.Client", Recorder):
        assert client.is_available(0.2) is True

    assert seen == [False, False], f"expected TLS skipped on both transports, got {seen}"


def test_is_available_is_false_when_nothing_is_listening() -> None:
    """An unused high port stands in for a server that is not running."""
    client = HttpQueryClient("http://127.0.0.1:1")

    assert client.is_available(0.2) is False


def test_only_in_process_client_needs_a_backend() -> None:
    assert InProcessQueryClient().needs_backend is True
    assert HttpQueryClient("http://127.0.0.1:8765").needs_backend is False


def test_download_path_is_absolutised_for_the_server() -> None:
    """The server writes the file, so a relative path must be resolved client-side."""
    sent: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(dict(request.url.params))
        return httpx.Response(200, json={"filename": "f", "bytes": 1, "path": "/x"})

    client = HttpQueryClient("http://t", transport=httpx.MockTransport(handler))

    asyncio.run(client.download("entity-1", "relative/out.pdf"))

    assert sent["output_path"].startswith("/")
    assert sent["output_path"].endswith("relative/out.pdf")


def test_download_without_a_path_sends_none() -> None:
    sent: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(dict(request.url.params))
        return httpx.Response(200, json={"filename": "f", "bytes": 1, "path": "/x"})

    client = HttpQueryClient("http://t", transport=httpx.MockTransport(handler))

    asyncio.run(client.download("entity-1", None))

    assert "output_path" not in sent
