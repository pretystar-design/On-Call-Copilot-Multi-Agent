"""Service topology model and mapping rules from infra connectors to logical services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional



@dataclass
class WorkloadEndpoint:
    name: str
    kind: str
    namespace: str
    labels: Dict[str, str]
    identifiers: Dict[str, str]


@dataclass
class ServiceTopology:
    service_name: str
    workloads: List[WorkloadEndpoint] = field(default_factory=list)
    endpoints: List[str] = field(default_factory=list)
    identifiers: Dict[str, str] = field(default_factory=dict)
    labels: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service_name": self.service_name,
            "workloads": [
                {
                    "name": w.name,
                    "kind": w.kind,
                    "namespace": w.namespace,
                    "labels": w.labels,
                    "identifiers": w.identifiers,
                }
                for w in self.workloads
            ],
            "endpoints": self.endpoints,
            "identifiers": self.identifiers,
            "labels": self.labels,
        }


def map_azure_to_topology(inventory, service_hint: str = "") -> ServiceTopology:
    topology = ServiceTopology(service_name=service_hint)
    for aks in getattr(inventory, "aks_clusters", []):
        topology.workloads.append(
            WorkloadEndpoint(
                name=aks.get("name", ""),
                kind="aks",
                namespace="default",
                labels=aks.get("tags", {}),
                identifiers={
                    "cluster": aks.get("name", ""),
                    "location": aks.get("location", ""),
                    "rg": aks.get("resource_group", ""),
                },
            )
        )
    for vm in getattr(inventory, "vms", []):
        topology.workloads.append(
            WorkloadEndpoint(
                name=vm.get("name", ""),
                kind="vm",
                namespace=vm.get("resource_group", ""),
                labels=vm.get("tags", {}),
                identifiers={
                    "location": vm.get("location", ""),
                    "rg": vm.get("resource_group", ""),
                    "vm_size": vm.get("vm_size", ""),
                },
            )
        )
    for lb in getattr(inventory, "load_balancers", []):
        topology.endpoints.append(f"lb:{lb.get('name', '')}")
    if service_hint:
        topology.identifiers["service_hint"] = service_hint
    return topology


def map_gke_to_topology(inventory, service_hint: str = "") -> ServiceTopology:
    topology = ServiceTopology(service_name=service_hint)
    for dep in getattr(inventory, "deployments", []):
        topology.workloads.append(
            WorkloadEndpoint(
                name=dep.get("name", ""),
                kind="deployment",
                namespace=dep.get("namespace", ""),
                labels=dep.get("labels", {}),
                identifiers={"replicas": str(dep.get("replicas", 0))},
            )
        )
    for svc in getattr(inventory, "services", []):
        if svc.get("type") == "LoadBalancer":
            topology.endpoints.append(f"svc:{svc.get('name', '')}.{svc.get('namespace', '')}")
    for ing in getattr(inventory, "ingresses", []):
        for host in ing.get("hosts", []):
            topology.endpoints.append(f"https://{host}")
    if service_hint:
        topology.identifiers["service_hint"] = service_hint
    return topology


def map_gcp_to_topology(inventory, service_hint: str = "") -> List[ServiceTopology]:
    """Map a GCPInventory (or dict-like object) to a list of ServiceTopology entries.

    Creates per-GKE-cluster topology entries capturing VPCs, instances,
    GKE workloads, and load balancers.
    """
    result: List[ServiceTopology] = []
    top = ServiceTopology(service_name=service_hint)

    for vpc in _get(inventory, "vpcs", []):
        top.workloads.append(
            WorkloadEndpoint(
                name=vpc.get("name", ""),
                kind="gcp-vpc",
                namespace="gcp",
                labels={},
                identifiers={
                    "routing_mode": vpc.get("routing_mode", ""),
                    "self_link": vpc.get("self_link", ""),
                },
            )
        )

    for inst in _get(inventory, "instances", []):
        top.workloads.append(
            WorkloadEndpoint(
                name=inst.get("name", ""),
                kind="gcp-vm",
                namespace=inst.get("zone", ""),
                labels=inst.get("labels", {}),
                identifiers={
                    "zone": inst.get("zone", ""),
                    "machine_type": inst.get("machine_type", ""),
                    "status": inst.get("status", ""),
                },
            )
        )

    for cluster in _get(inventory, "gke_clusters", []):
        col = ServiceTopology(service_name=cluster.get("name", ""))
        for dep in _get(cluster, "workloads.deployments", _nested_get(cluster, ["workloads", "deployments"], [])):
            col.workloads.append(
                WorkloadEndpoint(
                    name=dep.get("name", ""),
                    kind="deployment",
                    namespace=dep.get("namespace", "default"),
                    labels={},
                    identifiers={"replicas": str(dep.get("replicas", 0))},
                )
            )
        col.identifiers["location"] = cluster.get("location", "")
        col.identifiers["kubernetes_version"] = cluster.get("kubernetes_version", "")
        result.append(col)

    for lb in _get(inventory, "load_balancers", []):
        top.endpoints.append(
            f"lb:{lb.get('name', '')} ({lb.get('protocol', '')} {lb.get('ip_address', '')})"
        )

    if service_hint:
        top.identifiers["service_hint"] = service_hint
    result.insert(0, top)
    return result


def _get(obj: Any, attr: str, default: Any = None) -> Any:
    """Safely get an attribute or dict key, compatible with dataclass + dict."""
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _nested_get(obj: Any, keys: List[str], default: Any = None) -> Any:
    """Safely traverse nested dicts/objects."""
    current = obj
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key, {})
        else:
            current = getattr(current, key, {})
    return current if current else default



