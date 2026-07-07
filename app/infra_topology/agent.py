"""Infrastructure topology discovery agents — precondition for incident analysis.

One agent per cloud provider (Azure, GCP). Each follows the same pattern:
cache-first, then fall back to cloud API, with graceful degradation.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from ..connectors.azure_connector import AzureConnector
from ..connectors.gcp_connector import GCPConnector
from ..connectors.topology import map_azure_to_topology, map_gcp_to_topology
from ..config import config
from .persistence import load_cache, load_stale_fallback, save_cache

logger = logging.getLogger(__name__)

AZURE_PROVIDER = "azure"
GCP_PROVIDER = "gcp"


class AzureTopologyAgent:
    """Orchestrates Azure infrastructure topology discovery.

    Called once per incident as a precondition step before the four
    specialist agents run. Uses cached data when available and fresh,
    falls back to stale cache when fetch fails, and gracefully handles
    missing SDKs or auth errors.
    """

    def __init__(self) -> None:
        self._connector: Optional[AzureConnector] = None
        self._initialized = False

    def _ensure_connector(self) -> AzureConnector:
        if not self._initialized:
            self._connector = AzureConnector()
            self._initialized = True
        return self._connector  # type: ignore[return-value]

    def fetch_topology(self) -> Dict[str, Any]:
        """Fetch Azure topology, returning a structured result dict.

        The result always contains at minimum a ``topology`` key (may be
        empty) and an ``inventory`` key.

        Returns:
            dict with keys:
                - ``topology``: list of ServiceTopology dicts
                - ``inventory``: AzureInventory dataclass (or None)
                - ``summary``: human-readable summary string
                - ``error``: optional error description
        """
        sub_id = config.azure_subscription_id
        if not sub_id:
            logger.info("AZURE_SUBSCRIPTION_ID not set; skipping topology discovery")
            return self._empty_result("AZURE_SUBSCRIPTION_ID not configured")

        # Try fresh cache first
        cached = load_cache(AZURE_PROVIDER, sub_id)
        if cached is not None:
            return self._from_cache(cached)

        # No fresh cache — call Azure APIs
        try:
            connector = self._ensure_connector()
            inventory = connector.discover_inventory(subscription_id=sub_id)
        except ImportError:
            msg = "Azure SDK not installed; skipping topology discovery"
            logger.debug(msg)
            stale = load_stale_fallback(AZURE_PROVIDER, sub_id)
            if stale:
                logger.warning("Using stale Azure topology cache as fallback")
                return self._from_cache(stale)
            return self._empty_result(msg)
        except Exception as e:
            msg = f"Azure topology discovery failed: {e}"
            logger.warning(msg)
            stale = load_stale_fallback(AZURE_PROVIDER, sub_id)
            if stale:
                logger.warning("Using stale Azure topology cache as fallback after error")
                return self._from_cache(stale)
            return self._empty_result(msg)

        # Map inventory to topology and cache
        topology = map_azure_to_topology(inventory)
        inventory_dict = _inventory_to_dict(inventory)
        save_cache(AZURE_PROVIDER, sub_id, inventory_dict)

        return {
            "topology": [topology.to_dict()] if topology.workloads or topology.endpoints else [],
            "inventory": inventory_dict,
            "summary": _build_summary(inventory, topology),
            "error": None,
        }

    @staticmethod
    def _empty_result(error: str) -> Dict[str, Any]:
        return {
            "topology": [],
            "inventory": None,
            "summary": "No Azure topology available.",
            "error": error,
        }

    @staticmethod
    def _from_cache(cached: Dict[str, Any]) -> Dict[str, Any]:
        inventory = cached
        topology = map_azure_to_topology(_dict_to_inventory(inventory))
        return {
            "topology": [topology.to_dict()] if topology.workloads or topology.endpoints else [],
            "inventory": inventory,
            "summary": _build_summary_from_dict(inventory, topology),
            "error": None,
        }


class GCPTopologyAgent:
    """Orchestrates GCP infrastructure topology discovery.

    Called once per incident as a precondition step. Uses cached data
    when available and fresh, falls back to stale cache when fetch fails,
    and gracefully handles missing SDKs or auth errors.
    """

    def __init__(self) -> None:
        self._connector: Optional[GCPConnector] = None
        self._initialized = False

    def _ensure_connector(self) -> GCPConnector:
        if not self._initialized:
            self._connector = GCPConnector()
            self._initialized = True
        return self._connector  # type: ignore[return-value]

    def fetch_topology(self) -> Dict[str, Any]:
        """Fetch GCP topology, returning a structured result dict.

        Returns:
            dict with keys:
                - ``topology``: list of ServiceTopology dicts
                - ``inventory``: GCPInventory dict (or None)
                - ``summary``: human-readable summary string
                - ``error``: optional error description
        """
        project = config.gcp_project_id
        if not project:
            try:
                connector = self._ensure_connector()
                project = connector._resolve_project()
            except Exception:
                pass
        if not project:
            logger.info("GCP_PROJECT_ID not set; skipping topology discovery")
            return self._empty_result("GCP_PROJECT_ID not configured")

        # Try fresh cache first
        cached = load_cache(GCP_PROVIDER, project)
        if cached is not None:
            return self._from_cache(cached)

        # No fresh cache — call GCP APIs
        try:
            connector = self._ensure_connector()
            inventory = connector.discover_all(project_id=project)
        except ImportError:
            msg = "GCP SDK not installed; skipping topology discovery"
            logger.debug(msg)
            stale = load_stale_fallback(GCP_PROVIDER, project)
            if stale:
                logger.warning("Using stale GCP topology cache as fallback")
                return self._from_cache(stale)
            return self._empty_result(msg)
        except Exception as e:
            msg = f"GCP topology discovery failed: {e}"
            logger.warning(msg)
            stale = load_stale_fallback(GCP_PROVIDER, project)
            if stale:
                logger.warning("Using stale GCP topology cache as fallback after error")
                return self._from_cache(stale)
            return self._empty_result(msg)

        # Map inventory to topology and cache
        topologies = map_gcp_to_topology(inventory)
        inventory_dict = _gcp_inventory_to_dict(inventory)
        save_cache(GCP_PROVIDER, project, inventory_dict)

        return {
            "topology": [t.to_dict() for t in topologies],
            "inventory": inventory_dict,
            "summary": _build_gcp_summary(inventory),
            "error": None,
        }

    @staticmethod
    def _empty_result(error: str) -> Dict[str, Any]:
        return {
            "topology": [],
            "inventory": None,
            "summary": "No GCP topology available.",
            "error": error,
        }

    @staticmethod
    def _from_cache(cached: Dict[str, Any]) -> Dict[str, Any]:
        inventory_dict = cached
        inventory = _gcp_dict_to_inventory(inventory_dict)
        topologies = map_gcp_to_topology(inventory)
        return {
            "topology": [t.to_dict() for t in topologies],
            "inventory": inventory_dict,
            "summary": _build_gcp_summary_from_dict(inventory_dict),
            "error": None,
        }


# ── GCP helpers ──────────────────────────────────────────────────


def _gcp_inventory_to_dict(inv: Any) -> Dict[str, Any]:
    return {
        "project_id": inv.project_id,
        "vpcs": inv.vpcs,
        "instances": inv.instances,
        "gke_clusters": inv.gke_clusters,
        "load_balancers": inv.load_balancers,
    }


def _gcp_dict_to_inventory(d: Dict[str, Any]) -> Any:
    from types import SimpleNamespace
    return SimpleNamespace(
        project_id=d.get("project_id", ""),
        vpcs=d.get("vpcs", []),
        instances=d.get("instances", []),
        gke_clusters=d.get("gke_clusters", []),
        load_balancers=d.get("load_balancers", []),
    )


def _build_gcp_summary(inv: Any) -> str:
    parts = [f"GCP project: {inv.project_id}"]
    if inv.vpcs:
        names = ", ".join(v.get("name", "?") for v in inv.vpcs)
        parts.append(f"VPCs: {len(inv.vpcs)} ({names})")
    if inv.instances:
        parts.append(f"VM instances: {len(inv.instances)}")
    if inv.gke_clusters:
        names = ", ".join(c.get("name", "?") for c in inv.gke_clusters)
        parts.append(f"GKE clusters: {len(inv.gke_clusters)} ({names})")
    if inv.load_balancers:
        parts.append(f"Load Balancers: {len(inv.load_balancers)}")
    return "; ".join(parts) if parts else "GCP topology: no resources discovered."


def _build_gcp_summary_from_dict(inv_dict: Dict[str, Any]) -> str:
    vpcs = inv_dict.get("vpcs", [])
    instances = inv_dict.get("instances", [])
    clusters = inv_dict.get("gke_clusters", [])
    lbs = inv_dict.get("load_balancers", [])
    parts = [f"GCP project: {inv_dict.get('project_id', '')} (cached)"]
    if vpcs:
        names = ", ".join(v.get("name", "?") for v in vpcs)
        parts.append(f"VPCs: {len(vpcs)} ({names})")
    if instances:
        parts.append(f"VM instances: {len(instances)}")
    if clusters:
        names = ", ".join(c.get("name", "?") for c in clusters)
        parts.append(f"GKE clusters: {len(clusters)} ({names})")
    if lbs:
        parts.append(f"Load Balancers: {len(lbs)}")
    return "; ".join(parts) if parts else "GCP topology: no resources discovered (cached)."


# ── Azure helpers ────────────────────────────────────────────────


def _inventory_to_dict(inv: Any) -> Dict[str, Any]:
    """Convert AzureInventory dataclass to a serializable dict."""
    return {
        "subscription_id": inv.subscription_id,
        "resource_groups": inv.resource_groups,
        "aks_clusters": inv.aks_clusters,
        "service_fabric": inv.service_fabric,
        "vms": inv.vms,
        "vnets": inv.vnets,
        "load_balancers": inv.load_balancers,
    }


def _dict_to_inventory(d: Dict[str, Any]) -> Any:
    """Convert a dict back to an AzureInventory-like object (simple namespace)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        subscription_id=d.get("subscription_id", ""),
        resource_groups=d.get("resource_groups", []),
        aks_clusters=d.get("aks_clusters", []),
        service_fabric=d.get("service_fabric", []),
        vms=d.get("vms", []),
        vnets=d.get("vnets", []),
        load_balancers=d.get("load_balancers", []),
    )


def _build_summary(inv: Any, topology: Any) -> str:
    """Build a human-readable summary of discovered Azure topology."""
    parts = [f"Azure subscription: {inv.subscription_id}"]
    if inv.aks_clusters:
        names = ", ".join(c.get("name", "?") for c in inv.aks_clusters)
        parts.append(f"AKS clusters: {len(inv.aks_clusters)} ({names})")
    if inv.service_fabric:
        names = ", ".join(c.get("name", "?") for c in inv.service_fabric)
        parts.append(f"Service Fabric clusters: {len(inv.service_fabric)} ({names})")
    if inv.vms:
        parts.append(f"VMs: {len(inv.vms)}")
    if inv.vnets:
        parts.append(f"VNets: {len(inv.vnets)}")
    if inv.load_balancers:
        parts.append(f"Load Balancers: {len(inv.load_balancers)}")
    if topology.workloads:
        parts.append(f"Mapped workloads: {len(topology.workloads)}")
    return "; ".join(parts) if parts else "Azure topology: no resources discovered."


def _build_summary_from_dict(inv_dict: Dict[str, Any], topology: Any) -> str:
    """Build summary from a cached inventory dict."""
    aks = inv_dict.get("aks_clusters", [])
    sf = inv_dict.get("service_fabric", [])
    vms = inv_dict.get("vms", [])
    vnets = inv_dict.get("vnets", [])
    lbs = inv_dict.get("load_balancers", [])

    parts = [f"Azure subscription: {inv_dict.get('subscription_id', '')} (cached)"]
    if aks:
        names = ", ".join(c.get("name", "?") for c in aks)
        parts.append(f"AKS clusters: {len(aks)} ({names})")
    if sf:
        names = ", ".join(c.get("name", "?") for c in sf)
        parts.append(f"Service Fabric clusters: {len(sf)} ({names})")
    if vms:
        parts.append(f"VMs: {len(vms)}")
    if vnets:
        parts.append(f"VNets: {len(vnets)}")
    if lbs:
        parts.append(f"Load Balancers: {len(lbs)}")
    if topology.workloads:
        parts.append(f"Mapped workloads: {len(topology.workloads)}")
    return "; ".join(parts) if parts else "Azure topology: no resources discovered (cached)."
