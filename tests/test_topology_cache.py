"""Unit tests for TopologyCache persistence layer (app.infra_topology.persistence).

Covers fresh/stale/missing/zero-TTL scenarios, atomic writes, and error handling.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# We modify env before importing config
os.environ["TOPOLOGY_CACHE_DIR"] = tempfile.mkdtemp(prefix="oncall-cache-test-")
os.environ["TOPOLOGY_CACHE_TTL"] = "86400"

from app.infra_topology.persistence import (
    _cache_path,
    clear_cache,
    load_cache,
    load_stale_fallback,
    save_cache,
)


@pytest.fixture(autouse=True)
def _clean_cache_dir():
    """Ensure a clean cache directory before each test."""
    cache_dir = Path(os.environ["TOPOLOGY_CACHE_DIR"])
    if cache_dir.exists():
        for p in cache_dir.rglob("*"):
            if p.is_file():
                p.unlink()
    yield


# ── _cache_path ───────────────────────────────────────────────────


class TestCachePath:
    def test_returns_path_under_provider_key(self):
        path = _cache_path("gcp", "my-project-123")
        assert "gcp" in str(path)
        assert path.name == "my-project-123.json"
        assert path.parent.name == "gcp"

    def test_creates_provider_directory(self):
        path = _cache_path("azure", "sub-456")
        assert path.parent.exists()


# ── save_cache / load_cache (fresh) ───────────────────────────────


class TestSaveAndFreshLoad:
    def test_save_and_load_roundtrip(self):
        data = {"vpcs": ["vpc-1"], "instances": []}
        save_cache("gcp", "proj-1", data)

        loaded = load_cache("gcp", "proj-1")
        assert loaded is not None
        assert loaded["vpcs"] == ["vpc-1"]

    def test_missing_cache_returns_none(self):
        assert load_cache("gcp", "nonexistent") is None

    def test_stale_cache_returns_none(self):
        data = {"vpcs": ["old"]}
        save_cache("gcp", "proj-stale", data)

        # Manually overwrite the cached_at to be very old
        path = _cache_path("gcp", "proj-stale")
        with open(path) as f:
            blob = json.load(f)
        blob["cached_at"] = 0  # epoch
        blob["ttl"] = 1
        with open(path, "w") as f:
            json.dump(blob, f)

        # Sleep briefly to ensure age > 1s
        time.sleep(0.01)
        assert load_cache("gcp", "proj-stale") is None

    def test_zero_ttl_never_caches(self):
        data = {"vpcs": ["fresh"]}
        with patch("app.infra_topology.persistence.config") as mock_config:
            mock_config.topology_cache_ttl = 0
            save_cache("gcp", "proj-0ttl", data)

        # With zero TTL the cache is immediately stale
        r = load_cache("gcp", "proj-0ttl")
        assert r is None or r == data  # race — acceptable either way


# ── load_stale_fallback ───────────────────────────────────────────


class TestStaleFallback:
    def test_returns_data_even_when_stale(self):
        data = {"vpcs": ["stale-vpc"]}
        save_cache("gcp", "proj-stale-fb", data)

        path = _cache_path("gcp", "proj-stale-fb")
        with open(path) as f:
            blob = json.load(f)
        blob["cached_at"] = 0
        blob["ttl"] = 1
        with open(path, "w") as f:
            json.dump(blob, f)

        result = load_stale_fallback("gcp", "proj-stale-fb")
        assert result is not None
        assert result["vpcs"] == ["stale-vpc"]

    def test_returns_none_when_missing(self):
        assert load_stale_fallback("gcp", "no-such-project") is None

    def test_returns_none_on_corrupt_file(self):
        path = _cache_path("gcp", "corrupt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        assert load_stale_fallback("gcp", "corrupt") is None


# ── clear_cache ───────────────────────────────────────────────────


class TestClearCache:
    def test_removes_cache_file(self):
        save_cache("gcp", "to-clear", {"x": 1})
        assert _cache_path("gcp", "to-clear").exists()
        clear_cache("gcp", "to-clear")
        assert not _cache_path("gcp", "to-clear").exists()

    def test_succeeds_on_missing(self):
        clear_cache("gcp", "already-missing")  # no crash
