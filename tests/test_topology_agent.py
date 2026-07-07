"""Unit tests for topology agents (app.infra_topology.agent).

Tests GCPTopologyAgent and AzureTopologyAgent with mock connectors.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("GCP_PROJECT_ID", "test-gcp-project")
os.environ.setdefault("AZURE_SUBSCRIPTION_ID", "test-azure-sub")
os.environ.setdefault("TOPOLOGY_CACHE_DIR", "/tmp/oncall-test-agent")

from app.config import config
from app.infra_topology.agent import GCPTopologyAgent, AzureTopologyAgent
from app.infra_topology import persistence  # noqa: F811


# ── GCPTopologyAgent ──────────────────────────────────────────────


class TestGCPTopologyAgent:
    def _make_agent(self):
        """Return a GCPTopologyAgent with load_cache mocked to return None."""
        agent = GCPTopologyAgent()
        return agent

    def test_empty_result_when_no_project(self):
        agent = self._make_agent()
        with patch.object(config, "gcp_project_id", ""):
            # No env var, no config value → no project
            result = agent.fetch_topology()
        assert result["summary"] == "No GCP topology available."
        assert result["error"] is not None

    def test_import_error_returns_empty(self):
        agent = self._make_agent()
        connector = MagicMock()
        connector._resolve_project.return_value = "test-gcp-project"
        connector.discover_all.side_effect = ImportError("no gcp sdk")
        with (
            patch.object(config, "gcp_project_id", "test-gcp-project"),
            patch.object(agent, "_ensure_connector", return_value=connector),
            patch("app.infra_topology.agent.load_cache", return_value=None),
            patch("app.infra_topology.agent.load_stale_fallback", return_value=None),
        ):
            result = agent.fetch_topology()
        assert result["summary"] == "No GCP topology available."
        assert "not installed" in (result.get("error") or "")

    def test_api_error_returns_empty(self):
        agent = self._make_agent()
        connector = MagicMock()
        connector.discover_all.side_effect = Exception("API error")
        with (
            patch.object(config, "gcp_project_id", "test-gcp-project"),
            patch.object(agent, "_ensure_connector", return_value=connector),
            patch("app.infra_topology.agent.load_cache", return_value=None),
            patch("app.infra_topology.agent.load_stale_fallback", return_value=None),
        ):
            result = agent.fetch_topology()
        assert "API error" in (result.get("error") or "")

    def test_successful_discovery_returns_topology(self):
        mock_inv = MagicMock()
        mock_inv.project_id = "test-gcp-project"
        mock_inv.vpcs = [{"name": "vpc-1"}]
        mock_inv.instances = []
        mock_inv.gke_clusters = []
        mock_inv.load_balancers = []

        agent = self._make_agent()
        connector = MagicMock()
        connector.discover_all.return_value = mock_inv

        with (
            patch.object(config, "gcp_project_id", "test-gcp-project"),
            patch.object(agent, "_ensure_connector", return_value=connector),
            patch("app.infra_topology.agent.load_cache", return_value=None),
            patch("app.infra_topology.agent.map_gcp_to_topology", return_value=[]),
            patch("app.infra_topology.agent.save_cache"),
        ):
            result = agent.fetch_topology()
        assert result["topology"] == []
        assert result["inventory"] is not None
        assert "test-gcp-project" in result["summary"]

    def test_falls_back_to_stale_cache_on_error(self):
        stale_data = {
            "project_id": "test-gcp-project",
            "vpcs": [{"name": "vpc-stale"}],
            "instances": [],
            "gke_clusters": [],
            "load_balancers": [],
        }

        agent = self._make_agent()
        connector = MagicMock()
        connector.discover_all.side_effect = Exception("API error")

        with (
            patch.object(config, "gcp_project_id", "test-gcp-project"),
            patch.object(agent, "_ensure_connector", return_value=connector),
            patch("app.infra_topology.agent.load_cache", return_value=None),
            patch("app.infra_topology.agent.load_stale_fallback", return_value=stale_data),
            patch("app.infra_topology.agent.map_gcp_to_topology", return_value=[]),
        ):
            result = agent.fetch_topology()
        assert "vpc-stale" in result["summary"]


# ── AzureTopologyAgent ────────────────────────────────────────────


class TestAzureTopologyAgent:
    def test_empty_result_when_no_subscription(self):
        agent = AzureTopologyAgent()
        with patch.object(config, "azure_subscription_id", ""):
            result = agent.fetch_topology()
        assert result["summary"] == "No Azure topology available."
        assert result["error"] is not None

    def test_import_error_returns_empty(self):
        agent = AzureTopologyAgent()
        connector = MagicMock()
        connector.discover_inventory.side_effect = ImportError("no azure sdk")
        with (
            patch.object(config, "azure_subscription_id", "test-azure-sub"),
            patch.object(agent, "_ensure_connector", return_value=connector),
            patch("app.infra_topology.agent.load_cache", return_value=None),
            patch("app.infra_topology.agent.load_stale_fallback", return_value=None),
        ):
            result = agent.fetch_topology()
        assert "not installed" in (result.get("error") or "")

    def test_api_error_returns_empty(self):
        agent = AzureTopologyAgent()
        connector = MagicMock()
        connector.discover_inventory.side_effect = Exception("API error")
        with (
            patch.object(config, "azure_subscription_id", "test-azure-sub"),
            patch.object(agent, "_ensure_connector", return_value=connector),
            patch("app.infra_topology.agent.load_cache", return_value=None),
            patch("app.infra_topology.agent.load_stale_fallback", return_value=None),
        ):
            result = agent.fetch_topology()
        assert "API error" in (result.get("error") or "")

    def test_successful_discovery_returns_topology(self):
        mock_inv = MagicMock()
        mock_inv.subscription_id = "test-azure-sub"
        mock_inv.resource_groups = ["rg-1"]
        mock_inv.aks_clusters = []
        mock_inv.service_fabric = []
        mock_inv.vms = []
        mock_inv.vnets = []
        mock_inv.load_balancers = []

        mock_topology = MagicMock()
        mock_topology.workloads = []
        mock_topology.endpoints = []

        agent = AzureTopologyAgent()
        connector = MagicMock()
        connector.discover_inventory.return_value = mock_inv

        with (
            patch.object(config, "azure_subscription_id", "test-azure-sub"),
            patch.object(agent, "_ensure_connector", return_value=connector),
            patch("app.infra_topology.agent.load_cache", return_value=None),
            patch("app.infra_topology.agent.map_azure_to_topology", return_value=mock_topology),
            patch("app.infra_topology.agent.save_cache"),
        ):
            result = agent.fetch_topology()
        assert "test-azure-sub" in result["summary"]
