"""Local JSON cache with TTL for infra topology data."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import config

logger = logging.getLogger(__name__)


def _cache_path(provider: str, key: str) -> Path:
    """Return the cache file path for a given provider and key.

    Example: ~/.oncall-copilot/topology/azure/<key>.json
    """
    cache_dir = Path(config.topology_cache_dir) / provider
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{key}.json"


def save_cache(provider: str, key: str, data: Dict[str, Any]) -> None:
    """Persist topology data to a local JSON file.

    Args:
        provider: Provider name (e.g. 'azure').
        key: Cache key, typically the subscription id or project id.
        data: The topology inventory dict to persist.
    """
    path = _cache_path(provider, key)
    try:
        with open(path, "w") as f:
            json.dump(
                {
                    "cached_at": time.time(),
                    "ttl": config.topology_cache_ttl,
                    "data": data,
                },
                f,
            )
        logger.debug("Topology cache written: %s", path)
    except OSError as e:
        logger.warning("Failed to write topology cache %s: %s", path, e)


def load_cache(provider: str, key: str) -> Optional[Dict[str, Any]]:
    """Load topology data from cache if it exists and is not stale.

    Args:
        provider: Provider name (e.g. 'azure').
        key: Cache key, typically the subscription id or project id.

    Returns:
        The cached data dict, or None if cache is missing/stale/corrupt.
    """
    path = _cache_path(provider, key)
    if not path.exists():
        logger.debug("No topology cache at %s", path)
        return None

    try:
        with open(path) as f:
            blob = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Corrupt topology cache %s: %s", path, e)
        return None

    cached_at = blob.get("cached_at", 0)
    ttl = blob.get("ttl", config.topology_cache_ttl)
    age = time.time() - cached_at

    if age <= ttl:
        logger.debug("Topology cache hit (age=%.0fs): %s", age, path)
        return blob.get("data")
    else:
        logger.debug("Topology cache expired (age=%.0fs > ttl=%ss): %s", age, ttl, path)
        return None


def load_stale_fallback(provider: str, key: str) -> Optional[Dict[str, Any]]:
    """Load topology data from cache even if stale. Used as fallback on fetch failure.

    Args:
        provider: Provider name (e.g. 'azure').
        key: Cache key, typically the subscription id or project id.

    Returns:
        The cached data dict (stale or not), or None if absent.
    """
    path = _cache_path(provider, key)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            blob = json.load(f)
        return blob.get("data")
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read stale cache %s: %s", path, e)
        return None


def clear_cache(provider: str, key: str) -> None:
    """Remove a cache file.

    Args:
        provider: Provider name (e.g. 'azure').
        key: Cache key, typically the subscription id or project id.
    """
    path = _cache_path(provider, key)
    if path.exists():
        path.unlink()
        logger.debug("Cleared topology cache: %s", path)
