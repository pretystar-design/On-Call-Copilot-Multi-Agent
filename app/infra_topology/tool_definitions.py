"""Agent Framework @tool wrappers for infra topology discovery.

Azure and GCP tools let specialist agents invoke topology discovery on demand.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent_framework import tool

from ..connectors.azure_connector import AzureConnector
from ..connectors.gcp_connector import GCPConnector
from ..connectors.topology import map_azure_to_topology


@tool(
    name="azure_discover_inventory",
    description="Discover Azure infrastructure inventory: resource groups, AKS clusters, "
    "Service Fabric clusters, VMs, VNets, and load balancers in the configured subscription. "
    "Returns a structured JSON object. Call this when you need fresh Azure infrastructure data.",
)
def azure_discover_inventory(
    subscription_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Discover Azure inventory via the existing AzureConnector.

    Args:
        subscription_id: Override subscription ID. Uses config default if omitted.

    Returns:
        AzureInventory as a JSON dict, or an error dict with an "error" key.
    """
    try:
        connector = AzureConnector()
        inventory = connector.discover_inventory(subscription_id=subscription_id)
        return {
            "subscription_id": inventory.subscription_id,
            "resource_groups": inventory.resource_groups,
            "aks_clusters": inventory.aks_clusters,
            "service_fabric": inventory.service_fabric,
            "vms": inventory.vms,
            "vnets": inventory.vnets,
            "load_balancers": inventory.load_balancers,
        }
    except ImportError:
        return {"error": "Azure SDK not installed. Install azure-identity and azure-mgmt-* packages."}
    except Exception as e:
        return {"error": f"Azure inventory discovery failed: {e}"}


@tool(
    name="azure_map_to_topology",
    description="Convert an AzureInventory dict to a ServiceTopology. "
    "Use this after calling azure_discover_inventory to get a topology model. "
    "Accepts an inventory dict and optional service_hint string.",
)
def azure_map_to_topology(
    inventory: Dict[str, Any],
    service_hint: str = "",
) -> Dict[str, Any]:
    """Map an Azure inventory dict to a ServiceTopology.

    Args:
        inventory: AzureInventory as a dict (from azure_discover_inventory).
        service_hint: Optional hint for service name.

    Returns:
        ServiceTopology as a dict, or an error dict.
    """
    try:
        inv = _dict_to_namespace(inventory)
        topology = map_azure_to_topology(inv, service_hint=service_hint)
        return topology.to_dict()
    except Exception as e:
        return {"error": f"Failed to map Azure inventory to topology: {e}"}


def _dict_to_namespace(d: Dict[str, Any]) -> Any:
    """Convert a plain dict to a simple namespace for map_azure_to_topology compatibility."""
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


# ── GCP Tools ────────────────────────────────────────────────────


def _safe_list(result: Any, default: List = None) -> List:
    """Return a list or empty list from a GCPConnector result."""
    return result if isinstance(result, list) else (default or [])


@tool(
    name="discover_gcp_vpcs",
    description="Discover GCP VPC networks in the configured project. "
    "Returns a JSON list of VPC dicts with name, routing_mode, subnets, and self_link.",
)
def discover_gcp_vpcs(project_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        connector = GCPConnector()
        vpcs = connector.discover_vpcs(project_id=project_id)
        return {"vpcs": _safe_list(vpcs)}
    except ImportError:
        return {"error": "GCP SDK not installed. Install google-cloud-compute."}
    except Exception as e:
        return {"error": f"GCP VPC discovery failed: {e}"}


@tool(
    name="discover_gcp_instances",
    description="Discover GCP Compute VM instances across all zones. "
    "Returns a JSON list of instance dicts with name, zone, machine_type, status, IPs, and labels.",
)
def discover_gcp_instances(project_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        connector = GCPConnector()
        instances = connector.discover_instances(project_id=project_id)
        return {"instances": _safe_list(instances)}
    except ImportError:
        return {"error": "GCP SDK not installed. Install google-cloud-compute."}
    except Exception as e:
        return {"error": f"GCP instance discovery failed: {e}"}


@tool(
    name="discover_gcp_gke_clusters",
    description="Discover GKE clusters and their workloads (deployments, pods, services, ingresses). "
    "Returns a JSON list of cluster dicts with metadata and workload details.",
)
def discover_gcp_gke_clusters(project_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        connector = GCPConnector()
        clusters = connector.discover_gke_clusters(project_id=project_id)
        return {"gke_clusters": _safe_list(clusters)}
    except ImportError:
        return {"error": "GCP SDK not installed. Install google-cloud-compute and google-cloud-container."}
    except Exception as e:
        return {"error": f"GCP GKE cluster discovery failed: {e}"}


@tool(
    name="discover_gcp_load_balancers",
    description="Discover GCP Load Balancers (forwarding rules, regional and global). "
    "Returns a JSON list of load balancer dicts with region, IP, protocol, and target.",
)
def discover_gcp_load_balancers(project_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        connector = GCPConnector()
        lbs = connector.discover_load_balancers(project_id=project_id)
        return {"load_balancers": _safe_list(lbs)}
    except ImportError:
        return {"error": "GCP SDK not installed. Install google-cloud-compute."}
    except Exception as e:
        return {"error": f"GCP load balancer discovery failed: {e}"}


@tool(
    name="discover_all_gcp",
    description="Discover all GCP infrastructure (VPCs, VM instances, GKE clusters, Load Balancers) "
    "in a single call. Returns a combined inventory dict.",
)
def discover_all_gcp(project_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        connector = GCPConnector()
        inv = connector.discover_all(project_id=project_id)
        return {
            "project_id": inv.project_id,
            "vpcs": _safe_list(inv.vpcs),
            "instances": _safe_list(inv.instances),
            "gke_clusters": _safe_list(inv.gke_clusters),
            "load_balancers": _safe_list(inv.load_balancers),
        }
    except ImportError:
        return {"error": "GCP SDK not installed. Install google-cloud-compute and google-cloud-container."}
    except Exception as e:
        return {"error": f"GCP discovery failed: {e}"}


@tool(
    name="get_gcp_topology_summary",
    description="Get a lightweight count-based summary of GCP infrastructure. "
    "Returns JSON with vpcs, instances, gke_clusters, and load_balancers counts.",
)
def get_gcp_topology_summary(project_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        connector = GCPConnector()
        inv = connector.discover_all(project_id=project_id)
        return {
            "vpcs": len(_safe_list(inv.vpcs)),
            "instances": len(_safe_list(inv.instances)),
            "gke_clusters": len(_safe_list(inv.gke_clusters)),
            "load_balancers": len(_safe_list(inv.load_balancers)),
        }
    except ImportError:
        return {"error": "GCP SDK not installed."}
    except Exception as e:
        return {"error": f"GCP topology summary failed: {e}"}


# ── Toolset Bundling ──────────────────────────────────────────────

# Public list of all topology discovery tools for Agent Framework injection.
TOPOLOGY_TOOLS: List = [
    azure_discover_inventory,
    azure_map_to_topology,
    discover_gcp_vpcs,
    discover_gcp_instances,
    discover_gcp_gke_clusters,
    discover_gcp_load_balancers,
    discover_all_gcp,
    get_gcp_topology_summary,
]


def _all_tools() -> List:
    return list(TOPOLOGY_TOOLS)


class TopologyToolset:
    """Bundles all topology discovery tools for Agent Framework injection.

    Use ``TopologyToolset.get_tools()`` to get the complete tool list
    for the ``tools`` parameter of an ``Agent`` constructor.
    """

    @staticmethod
    def get_tools() -> List:
        return _all_tools()
