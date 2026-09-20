"""URL routing through connector-owned resolvers."""

from __future__ import annotations

import re

from agentgraph.connectors.base import BaseConnector, SourceReference

# U+200B zero-width space, U+200C/D/E/F directional marks, U+00AD soft hyphen, U+FEFF BOM
_INVISIBLE_CHARS_RE = re.compile(
    "[­​‌‍‎‏﻿]+"
)


def normalise_url_for_matching(url: str) -> str:
    """Strip invisible characters and punctuation commonly wrapping URLs in prose."""
    return _INVISIBLE_CHARS_RE.sub("", url).rstrip(".,)>\"'")


def classify_url(url: str) -> SourceReference | None:
    """Return a SourceReference for a connector-owned URL, or None."""
    from agentgraph.connectors.registry import bootstrap, get_all_connectors

    normalised_url = normalise_url_for_matching(url)
    bootstrap()
    for connector in get_all_connectors():
        if type(connector).is_generic_url_fallback:
            continue
        ref = connector.resolve_url(normalised_url)
        if ref is not None:
            return ref
    return None


async def classify_observation_url(
    url: str,
    meta: dict[str, str] | None = None,
) -> SourceReference | None:
    """Resolve a browser-observed URL through connector-owned async resolvers."""
    from agentgraph.connectors.registry import bootstrap, get_all_connectors

    normalised_url = normalise_url_for_matching(url)
    bootstrap()
    connectors = sorted(
        get_all_connectors(),
        key=lambda c: (c.is_generic_url_fallback, c.resolve_url(normalised_url) is None, c.source),
    )
    for connector in connectors:
        ref = await connector.resolve_observation_url(normalised_url, meta=meta)
        if ref is not None:
            return ref
    return None


def stored_url_candidates(url: str) -> list[SourceReference]:
    """Prefer explicit ownership, then specific URL identities, then generic web identities."""
    from agentgraph.connectors.registry import get_all_connectors

    url = normalise_url_for_matching(url)
    explicit = classify_url(url)
    if explicit is not None:
        return [explicit]
    connectors = sorted(get_all_connectors(), key=lambda c: (c.is_generic_url_fallback, c.source))
    return [ref for c in connectors if (ref := c.url_entity_reference(url)) is not None]


async def find_stored_url_reference(connector: BaseConnector, url: str) -> SourceReference | None:
    """Validate a connector's identity candidate using the standard indexed lookup."""
    from agentgraph.core.context import get_backend

    ref = connector.url_entity_reference(normalise_url_for_matching(url))
    if ref is None:
        return None
    try:
        backend = get_backend()
    except RuntimeError:
        return None
    entity_id = await backend.find_entity_id(ref.source, ref.resource_id)
    return ref if entity_id is not None else None
