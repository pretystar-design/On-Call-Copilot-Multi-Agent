"""Unit tests for GCPConnector (app.connectors.gcp_connector).

Tests each resource type, combined discovery, and SDK-not-installed handling.
Uses monkeypatching to avoid real GCP API calls.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("GCP_PROJECT_ID", "test-gcp-project")
os.environ.setdefault("TOPOLOGY_CACHE_DIR", "/tmp/oncall-test-gcp-connector")

from app.config import config
from app.connectors.gcp_connector import GCPConnector, GCPInventory


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def connector():
    return GCPConnector()


# ── _resolve_project ──────────────────────────────────────────────


class TestResolveProject:
    def test_uses_explicit_project_id(self, connector):
        assert connector._resolve_project("explicit-project") == "explicit-project"

    def test_falls_back_to_env_var(self, connector):
        assert connector._resolve_project() == "test-gcp-project"

    def test_empty_when_neither_set(self, connector):
        connector._project_id = None
        with patch.object(config, "gcp_project_id", ""):
            result = connector._resolve_project()
            assert result == ""


# ── discover_vpcs ─────────────────────────────────────────────────


class TestDiscoverVpcs:
    def test_returns_empty_when_no_project(self, connector):
        with patch.object(connector, "_resolve_project", return_value=""):
            assert connector.discover_vpcs() == []

    def test_import_error_returns_empty(self, connector):
        with patch.object(connector, "_get_compute_client", side_effect=ImportError):
            assert connector.discover_vpcs() == []

    def test_discover_vpcs_success(self, connector):
        mock_network = MagicMock()
        mock_network.name = "default"
        mock_network.self_link = "https://compute.googleapis.com/.../default"
        mock_network.routing_config.routing_mode = "GLOBAL"
        mock_network.subnetworks = ["subnet-1", "subnet-2"]
        mock_network.auto_create_subnetworks = True

        mock_client = MagicMock()
        mock_client.list.return_value = [mock_network]

        mock_compute = MagicMock()
        mock_compute.NetworksClient.return_value = mock_client
        connector._compute_client = mock_compute

        result = connector.discover_vpcs()
        assert len(result) == 1
        assert result[0]["name"] == "default"
        assert result[0]["routing_mode"] == "GLOBAL"
        assert len(result[0]["subnets"]) == 2

    def test_discover_vpcs_api_failure(self, connector):
        mock_client = MagicMock()
        mock_client.list.side_effect = Exception("API error")

        mock_compute = MagicMock()
        mock_compute.NetworksClient.return_value = mock_client
        connector._compute_client = mock_compute

        assert connector.discover_vpcs() == []


# ── discover_instances ────────────────────────────────────────────


class TestDiscoverInstances:
    def test_returns_empty_when_no_project(self, connector):
        with patch.object(connector, "_resolve_project", return_value=""):
            assert connector.discover_instances() == []

    def test_import_error_returns_empty(self, connector):
        with patch.object(connector, "_get_compute_client", side_effect=ImportError):
            assert connector.discover_instances() == []

    def test_discover_instances_success(self, connector):
        mock_nic = MagicMock()
        mock_nic.name = "nic-0"
        mock_nic.network = "default"
        mock_nic.network_i_p = "10.0.0.1"
        mock_nic.access_configs = []

        mock_instance = MagicMock()
        mock_instance.name = "vm-1"
        mock_instance.machine_type = (
            "https://compute.googleapis.com/.../zones/us-central1-a/machineTypes/e2-medium"
        )
        mock_instance.status = "RUNNING"
        mock_instance.tags.items = ["http-server"]
        mock_instance.labels = {"env": "prod"}
        mock_instance.network_interfaces = [mock_nic]

        mock_zone_instances = MagicMock()
        mock_zone_instances.instances = [mock_instance]

        mock_agg = MagicMock()
        mock_agg.__iter__.return_value = [("zones/us-central1-a", mock_zone_instances)]

        mock_client = MagicMock()
        mock_client.aggregated_list.return_value = mock_agg

        mock_compute = MagicMock()
        mock_compute.InstancesClient.return_value = mock_client
        connector._compute_client = mock_compute

        result = connector.discover_instances()
        assert len(result) == 1
        assert result[0]["name"] == "vm-1"
        assert result[0]["zone"] == "us-central1-a"
        assert result[0]["machine_type"] == "e2-medium"
        assert result[0]["status"] == "RUNNING"

    def test_discover_instances_api_failure(self, connector):
        mock_client = MagicMock()
        mock_client.aggregated_list.side_effect = Exception("API error")

        mock_compute = MagicMock()
        mock_compute.InstancesClient.return_value = mock_client
        connector._compute_client = mock_compute

        assert connector.discover_instances() == []


# ── discover_gke_clusters ─────────────────────────────────────────


class TestDiscoverGkeClusters:
    def test_returns_empty_when_no_project(self, connector):
        with patch.object(connector, "_resolve_project", return_value=""):
            assert connector.discover_gke_clusters() == []

    def test_import_error_returns_empty(self, connector):
        with patch.object(connector, "_get_container_client", side_effect=ImportError):
            assert connector.discover_gke_clusters() == []

    def test_discover_gke_success(self, connector):
        mock_cluster = MagicMock()
        mock_cluster.name = "cluster-1"
        mock_cluster.location = "us-central1"
        mock_cluster.current_master_version = "1.28"
        mock_cluster.status.name = "RUNNING"

        mock_node_pool = MagicMock()
        mock_node_pool.node_count = 3
        mock_cluster.node_pools = [mock_node_pool]

        mock_response = MagicMock()
        mock_response.clusters = [mock_cluster]

        mock_client = MagicMock()
        mock_client.list_clusters.return_value = mock_response

        mock_container = MagicMock()
        mock_container.ClusterManagerClient.return_value = mock_client
        connector._container_client = mock_container

        with patch("app.connectors.gke_connector.GKEConnector", side_effect=ImportError):
            result = connector.discover_gke_clusters()

        assert len(result) == 1
        assert result[0]["name"] == "cluster-1"
        assert result[0]["node_count"] == 3
        assert result[0]["workloads"] == {}


# ── discover_load_balancers ───────────────────────────────────────


class TestDiscoverLoadBalancers:
    def test_returns_empty_when_no_project(self, connector):
        with patch.object(connector, "_resolve_project", return_value=""):
            assert connector.discover_load_balancers() == []

    def test_import_error_returns_empty(self, connector):
        with patch.object(connector, "_get_compute_client", side_effect=ImportError):
            assert connector.discover_load_balancers() == []

    def test_discover_lb_success(self, connector):
        mock_region = MagicMock()
        mock_region.name = "us-central1"

        mock_rule = MagicMock()
        mock_rule.name = "lb-1"
        mock_rule.i_p_address = "34.1.2.3"
        mock_rule.i_p_protocol = "TCP"
        mock_rule.port_range = "80-80"
        mock_rule.target = "http-lb-target"

        mock_regions_client = MagicMock()
        mock_regions_client.list.return_value = [mock_region]

        mock_fwd_client = MagicMock()
        mock_fwd_client.list.return_value = [mock_rule]

        mock_global_client = MagicMock()
        mock_global_client.list.return_value = []

        mock_compute = MagicMock()
        mock_compute.RegionsClient.return_value = mock_regions_client
        mock_compute.ForwardingRulesClient.return_value = mock_fwd_client
        mock_compute.GlobalForwardingRulesClient.return_value = mock_global_client
        connector._compute_client = mock_compute

        result = connector.discover_load_balancers()
        assert len(result) == 1
        assert result[0]["name"] == "lb-1"
        assert result[0]["region"] == "us-central1"
        assert result[0]["ip_address"] == "34.1.2.3"
        assert result[0]["ports"] == ["80", "80"]


# ── discover_all (combined) ───────────────────────────────────────


class TestDiscoverAll:
    def test_discover_all_returns_gcp_inventory(self, connector):
        with (
            patch.object(connector, "discover_vpcs", return_value=[{"name": "vpc-1"}]),
            patch.object(connector, "discover_instances", return_value=[{"name": "vm-1"}]),
            patch.object(connector, "discover_gke_clusters", return_value=[{"name": "gke-1"}]),
            patch.object(connector, "discover_load_balancers", return_value=[{"name": "lb-1"}]),
        ):
            inv = connector.discover_all()
            assert isinstance(inv, GCPInventory)
            assert len(inv.vpcs) == 1
            assert len(inv.instances) == 1
            assert len(inv.gke_clusters) == 1
            assert len(inv.load_balancers) == 1

    def test_discover_all_empty_on_no_project(self, connector):
        with patch.object(connector, "_resolve_project", return_value=""):
            inv = connector.discover_all()
            assert isinstance(inv, GCPInventory)
            assert inv.project_id == ""
