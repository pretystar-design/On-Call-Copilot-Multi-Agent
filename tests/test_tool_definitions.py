"""Unit tests for @tool wrappers (app.infra_topology.tool_definitions).

Tests parameter mapping, return format, and error handling for all GCP and Azure tools.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("GCP_PROJECT_ID", "test-gcp-project")
os.environ.setdefault("AZURE_SUBSCRIPTION_ID", "test-azure-sub")
os.environ.setdefault("TOPOLOGY_CACHE_DIR", "/tmp/oncall-test-tools")

from app.infra_topology import tool_definitions as tools
from app.connectors.gcp_connector import GCPInventory


# ── GCP Tools ─────────────────────────────────────────────────────


class TestDiscoverGcpVpcs:
    def test_success_returns_vpcs_dict(self):
        with patch.object(tools.GCPConnector, "discover_vpcs", return_value=[{"name": "vpc-1"}]):
            result = tools.discover_gcp_vpcs()
            assert "vpcs" in result
            assert len(result["vpcs"]) == 1

    def test_import_error_returns_error_dict(self):
        with patch.object(tools.GCPConnector, "discover_vpcs", side_effect=ImportError):
            result = tools.discover_gcp_vpcs()
            assert "error" in result


class TestDiscoverGcpInstances:
    def test_success_returns_instances_dict(self):
        with patch.object(tools.GCPConnector, "discover_instances", return_value=[{"name": "vm-1"}]):
            result = tools.discover_gcp_instances()
            assert "instances" in result
            assert len(result["instances"]) == 1


class TestDiscoverGcpGkeClusters:
    def test_success_returns_clusters_dict(self):
        with patch.object(tools.GCPConnector, "discover_gke_clusters", return_value=[{"name": "gke-1"}]):
            result = tools.discover_gcp_gke_clusters()
            assert "gke_clusters" in result
            assert len(result["gke_clusters"]) == 1


class TestDiscoverGcpLoadBalancers:
    def test_success_returns_lb_dict(self):
        with patch.object(tools.GCPConnector, "discover_load_balancers", return_value=[{"name": "lb-1"}]):
            result = tools.discover_gcp_load_balancers()
            assert "load_balancers" in result
            assert len(result["load_balancers"]) == 1


class TestDiscoverAllGcp:
    def test_success_returns_combined_inventory(self):
        inv = GCPInventory(
            project_id="test",
            vpcs=[{"name": "vpc-1"}],
            instances=[{"name": "vm-1"}],
            gke_clusters=[{"name": "gke-1"}],
            load_balancers=[{"name": "lb-1"}],
        )
        with patch.object(tools.GCPConnector, "discover_all", return_value=inv):
            result = tools.discover_all_gcp()
            assert result["project_id"] == "test"
            assert len(result["vpcs"]) == 1
            assert len(result["instances"]) == 1
            assert len(result["gke_clusters"]) == 1
            assert len(result["load_balancers"]) == 1

    def test_discovery_failure_returns_error(self):
        with patch.object(tools.GCPConnector, "discover_all", side_effect=Exception("fail")):
            result = tools.discover_all_gcp()
            assert "error" in result


class TestGetGcpTopologySummary:
    def test_success_returns_counts(self):
        inv = GCPInventory(
            project_id="test",
            vpcs=[{"name": "vpc-1"}],
            instances=[{"name": "vm-1"}, {"name": "vm-2"}],
            gke_clusters=[],
            load_balancers=[{"name": "lb-1"}],
        )
        with patch.object(tools.GCPConnector, "discover_all", return_value=inv):
            result = tools.get_gcp_topology_summary()
            assert result["vpcs"] == 1
            assert result["instances"] == 2
            assert result["gke_clusters"] == 0
            assert result["load_balancers"] == 1


# ── Azure Tools ────────────────────────────────────────────────────


class TestAzureDiscoverInventory:
    def test_success_returns_inventory_dict(self):
        mock_inv = MagicMock()
        mock_inv.subscription_id = "sub-1"
        mock_inv.resource_groups = ["rg-1"]
        mock_inv.aks_clusters = []
        mock_inv.service_fabric = []
        mock_inv.vms = []
        mock_inv.vnets = []
        mock_inv.load_balancers = []

        with patch.object(tools.AzureConnector, "discover_inventory", return_value=mock_inv):
            result = tools.azure_discover_inventory()
            assert result["subscription_id"] == "sub-1"
            assert result["resource_groups"] == ["rg-1"]

    def test_import_error_returns_error_dict(self):
        with patch.object(tools.AzureConnector, "discover_inventory", side_effect=ImportError):
            result = tools.azure_discover_inventory()
            assert "error" in result


class TestAzureMapToTopology:
    def test_success_returns_topology_dict(self):
        mock_topology = MagicMock()
        mock_topology.to_dict.return_value = {"service_name": "test-svc", "workloads": []}

        inventory = {"subscription_id": "sub-1", "resource_groups": [], "aks_clusters": []}

        with patch("app.infra_topology.tool_definitions.map_azure_to_topology", return_value=mock_topology):
            result = tools.azure_map_to_topology(inventory)
            assert result["service_name"] == "test-svc"

    def test_failure_returns_error_dict(self):
        with patch("app.infra_topology.tool_definitions.map_azure_to_topology", side_effect=Exception("fail")):
            result = tools.azure_map_to_topology({})
            assert "error" in result


# ── Helper ──────────────────────────────────────────────────────────


class TestSafeList:
    def test_returns_list_as_is(self):
        assert tools._safe_list([1, 2, 3]) == [1, 2, 3]

    def test_returns_empty_for_none(self):
        assert tools._safe_list(None) == []

    def test_uses_default_when_not_list(self):
        assert tools._safe_list("not-a-list", default=["x"]) == ["x"]
