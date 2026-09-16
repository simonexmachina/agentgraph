"""Shared metadata endpoint for the local viewer and browser extension."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["metadata"])

_DYNAMIC_PATTERN_TIMEOUT_SECONDS = 4.0
logger = logging.getLogger(__name__)


@router.get("/meta")
async def get_meta(include_dynamic_url_patterns: bool = True) -> dict[str, Any]:
    """Return registered connector sources, URL patterns, and known entity types."""
    from agentgraph.config import get_settings
    from agentgraph.connectors.registry import get_all_connectors, get_entity_type_names

    connectors = get_all_connectors()
    seen_patterns: list[str] = []
    seen_set: set[str] = set()
    for connector in connectors:
        if include_dynamic_url_patterns:
            timeout = getattr(
                connector,
                "observation_url_patterns_timeout_seconds",
                _DYNAMIC_PATTERN_TIMEOUT_SECONDS,
            )
            if not isinstance(timeout, int | float) and timeout is not None:
                timeout = _DYNAMIC_PATTERN_TIMEOUT_SECONDS
            try:
                patterns = (
                    await connector.observation_url_patterns()
                    if timeout is None
                    else await asyncio.wait_for(connector.observation_url_patterns(), timeout=timeout)
                )
            except TimeoutError:
                logger.warning(
                    "Timed out loading observation URL patterns for connector %s",
                    connector.source,
                )
                continue
        else:
            patterns = connector.url_patterns
        for pattern in patterns:
            if pattern not in seen_set:
                seen_patterns.append(pattern)
                seen_set.add(pattern)

    return {
        "entity_types": get_entity_type_names(connectors),
        "platforms": sorted({connector.source for connector in connectors}),
        "url_patterns": seen_patterns,
        "observation_threshold_ms": get_settings().observation_threshold_seconds * 1000,
    }
