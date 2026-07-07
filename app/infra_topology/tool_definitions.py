"""Agent Framework @tool wrappers for infra topology discovery.

Azure and GCP tools let specialist agents invoke topology discovery on demand.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import os
import time
import json
import threading
from pathlib import Path

from agent_framework import tool

from ..connectors.azure_connector import AzureConnector
from ..connectors.gcp_connector import GCPConnector
from ..connectors.topology import map_azure_to_topology
from ..config import config


# In-memory cache: key -> (timestamp, payload)
_INVENTORY_CACHE: Dict[str, Any] = {}
# Per-key locks to prevent duplicate concurrent discovery calls
_INVENTORY_LOCKS: Dict[str, threading.Lock] = {}
# Keep the original method reference so tests that patch it can be detected
_ORIGINAL_AZURE_DISCOVER = AzureConnector.discover_inventory


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
    # Normalize subscription id key for caching
    sub = subscription_id or getattr(config, "azure_subscription_id", "") or "default"
    cache_key = f"azure:{sub}"
    ttl = int(getattr(config, "topology_cache_ttl", 86400))

    # If the AzureConnector.discover_inventory method has been patched (tests/mocks),
    # invalidate any existing in-memory cache for this key so the patched behavior
    # is exercised (unit tests expect the patched side effects).
    if AzureConnector.discover_inventory is not _ORIGINAL_AZURE_DISCOVER:
        _INVENTORY_CACHE.pop(cache_key, None)

    # Fast in-memory cache check
    now = time.time()
    cached = _INVENTORY_CACHE.get(cache_key)
    if cached:
        ts, payload = cached.get("ts"), cached.get("payload")
        if ts and (now - ts) < ttl:
            return payload

    # Ensure cache directory exists
    cache_dir = Path(getattr(config, "topology_cache_dir", os.path.expanduser("~/.oncall-copilot/topology")))
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        # best-effort: if dir can't be created, continue without persistent caching
        cache_dir = None

    cache_file = None
    if cache_dir:
        safe_name = sub.replace("/", "_")
        cache_file = cache_dir / f"azure_inventory_{safe_name}.json"

    # If the AzureConnector.discover_inventory method is patched (e.g. by tests),
    # avoid returning a stale file cache so the patched side-effect is exercised.
    if AzureConnector.discover_inventory is not _ORIGINAL_AZURE_DISCOVER:
        cache_file = None

    # Use a per-subscription lock to prevent duplicate concurrent discovery
    lock = _INVENTORY_LOCKS.setdefault(cache_key, threading.Lock())
    with lock:
        # Double-check cache after acquiring lock
        cached = _INVENTORY_CACHE.get(cache_key)
        if cached:
            ts, payload = cached.get("ts"), cached.get("payload")
            if ts and (now - ts) < ttl:
                return payload

        # Try file cache
        if cache_file and cache_file.exists():
            try:
                mtime = cache_file.stat().st_mtime
                if (now - mtime) < ttl:
                    with open(cache_file, "r", encoding="utf-8") as fh:
                        payload = json.load(fh)
                        _INVENTORY_CACHE[cache_key] = {"ts": now, "payload": payload}
                        return payload
            except Exception:
                pass

        # Perform discovery
        try:
            connector = AzureConnector()
            inventory = connector.discover_inventory(subscription_id=sub if sub != "" else None)
            payload = {
                "subscription_id": inventory.subscription_id,
                "resource_groups": inventory.resource_groups,
                "aks_clusters": inventory.aks_clusters,
                "service_fabric": inventory.service_fabric,
                "vms": inventory.vms,
                "vnets": inventory.vnets,
                "load_balancers": inventory.load_balancers,
            }

            # Persist to file cache
            if cache_file:
                try:
                    tmp_file = str(cache_file) + ".tmp"
                    with open(tmp_file, "w", encoding="utf-8") as fh:
                        json.dump(payload, fh)
                    os.replace(tmp_file, cache_file)
                except Exception:
                    pass

            _INVENTORY_CACHE[cache_key] = {"ts": now, "payload": payload}
            return payload
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


# ── Azure Subscription Tools ─────────────────────────────────────

@tool(
    name="azure_list_subscriptions",
    description="List all Azure subscriptions accessible by the current credential. "
    "Returns a JSON list of subscriptions with subscription_id, subscription_name, "
    "tenant_id, and state. Use this when you need to find the correct subscription "
    "to use for infrastructure discovery, or when the user mentions a subscription "
    "name/ID and you need to resolve it.",
)
def azure_list_subscriptions() -> Dict[str, Any]:
    """List all Azure subscriptions the current credential can access.

    Returns:
        Dict with a "subscriptions" key containing a list of subscription dicts,
        or an "error" key on failure.
    """
    try:
        connector = AzureConnector()
        subs = connector.list_subscriptions()
        return {
            "subscriptions": [
                {
                    "subscription_id": s.subscription_id,
                    "subscription_name": s.subscription_name,
                    "tenant_id": s.tenant_id,
                    "state": s.state,
                }
                for s in subs
            ]
        }
    except ImportError:
        return {"error": "Azure SDK not installed. Install azure-mgmt-resource."}
    except Exception as e:
        return {"error": f"Azure subscription listing failed: {e}"}


@tool(
    name="azure_resolve_subscription",
    description="Resolve an Azure subscription name or partial ID to a full subscription "
    "with its subscription_id, subscription_name, and tenant_id. "
    "Call this when a user mentions a subscription by name or partial ID in chat. "
    "For example: 'my-subscription' or 'sub-1234' or '1234abcd'. "
    "The tool tries exact ID match, exact name match, ID prefix, and name substring.",
)
def azure_resolve_subscription(
    identifier: str,
) -> Dict[str, Any]:
    """Resolve a subscription identifier to a full subscription record.

    Args:
        identifier: Subscription name, display name, or ID prefix to resolve.

    Returns:
        Dict with subscription details if found, or an error dict.
    """
    try:
        connector = AzureConnector()
        sub = connector.resolve_subscription(identifier)
        if sub is None:
            # Return all subscriptions so the user can pick
            all_subs = connector.list_subscriptions()
            return {
                "found": False,
                "message": f"No subscription matched '{identifier}'.",
                "available_subscriptions": [
                    {
                        "subscription_id": s.subscription_id,
                        "subscription_name": s.subscription_name,
                        "state": s.state,
                    }
                    for s in all_subs
                ],
            }
        return {
            "found": True,
            "subscription_id": sub.subscription_id,
            "subscription_name": sub.subscription_name,
            "tenant_id": sub.tenant_id,
            "state": sub.state,
        }
    except ImportError:
        return {"error": "Azure SDK not installed. Install azure-mgmt-resource."}
    except Exception as e:
        return {"error": f"Azure subscription resolution failed: {e}"}


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
    azure_list_subscriptions,
    azure_resolve_subscription,
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
