"""GCP infrastructure discovery connector.

Discovers VPC networks, Compute VMs, GKE clusters (with workloads via
existing GKEConnector), and Load Balancers across a GCP project.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import config

logger = logging.getLogger(__name__)


@dataclass
class GCPInventory:
    project_id: str
    vpcs: List[Dict[str, Any]] = field(default_factory=list)
    instances: List[Dict[str, Any]] = field(default_factory=list)
    gke_clusters: List[Dict[str, Any]] = field(default_factory=list)
    load_balancers: List[Dict[str, Any]] = field(default_factory=list)


class GCPConnector:
    """Discover GCP infrastructure resources.

    Each resource type has its own discovery method so failures are
    isolated per resource. Call ``discover_all()`` to get everything.
    """

    def __init__(self) -> None:
        self._project_id: Optional[str] = None
        self._compute_client = None
        self._container_client = None
        self._credentials = None

    def _resolve_project(self, project_id: Optional[str] = None) -> str:
        if project_id:
            return project_id
        if self._project_id:
            return self._project_id
        env_project = getattr(config, "gcp_project_id", None) or config.azure_subscription_id
        if env_project:
            self._project_id = env_project
            return env_project
        try:
            from google.auth import default as google_auth_default
            _, inferred = google_auth_default()
            if inferred:
                self._project_id = inferred
                return inferred
        except Exception:
            pass
        return ""

    def _get_compute_client(self):
        if self._compute_client is None:
            from google.cloud import compute_v1
            self._compute_client = compute_v1
        return self._compute_client

    def _get_container_client(self):
        if self._container_client is None:
            from google.cloud import container_v1
            self._container_client = container_v1
        return self._container_client

    # ── VPC Networks ──────────────────────────────────────────────

    def discover_vpcs(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all VPC networks in the project.

        Returns a list of dicts with keys: name, routing_mode, auto_subnet,
        subnets (list of subnetwork self-links), self_link.
        """
        project = self._resolve_project(project_id)
        if not project:
            logger.warning("GCP project not configured; skipping VPC discovery")
            return []

        try:
            compute = self._get_compute_client()
            client = compute.NetworksClient()
            networks = []
            for network in client.list(project=project):
                routing_mode = "REGIONAL"
                if network.routing_config and network.routing_config.routing_mode == "GLOBAL":
                    routing_mode = "GLOBAL"
                subnets = list(network.subnetworks) if network.subnetworks else []
                networks.append({
                    "name": network.name,
                    "self_link": network.self_link,
                    "routing_mode": routing_mode,
                    "auto_subnet": network.auto_create_subnetworks if hasattr(network, "auto_create_subnetworks") else False,
                    "subnets": subnets,
                })
            logger.debug("Discovered %d VPC networks in project %s", len(networks), project)
            return networks
        except ImportError:
            logger.debug("google-cloud-compute not installed; skipping VPC discovery")
            return []
        except Exception as e:
            logger.warning("GCP VPC discovery failed for project %s: %s", project, e)
            return []

    # ── Compute VM Instances ──────────────────────────────────────

    def discover_instances(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all Compute VM instances across all zones.

        Uses ``aggregated_list()`` — a single paginated API call.

        Returns a list of dicts with keys: name, zone, machine_type, status,
        tags, network_interfaces, labels.
        """
        project = self._resolve_project(project_id)
        if not project:
            logger.warning("GCP project not configured; skipping instance discovery")
            return []

        try:
            compute = self._get_compute_client()
            client = compute.InstancesClient()
            instances = []
            agg_result = client.aggregated_list(project=project)
            for zone, zone_instances in agg_result:
                if zone_instances.instances:
                    for instance in zone_instances.instances:
                        nics = []
                        for nic in instance.network_interfaces:
                            nics.append({
                                "name": nic.name,
                                "network": nic.network,
                                "network_ip": nic.network_i_p,
                                "access_configs": [
                                    {"nat_ip": ac.nat_i_p, "name": ac.name}
                                    for ac in nic.access_configs
                                ] if nic.access_configs else [],
                            })
                        instances.append({
                            "name": instance.name,
                            "zone": zone.split("/")[-1] if "/" in zone else zone,
                            "machine_type": instance.machine_type.split("/")[-1] if instance.machine_type else "",
                            "status": instance.status,
                            "tags": list(instance.tags.items) if instance.tags and instance.tags.items else [],
                            "network_interfaces": nics,
                            "labels": dict(instance.labels) if instance.labels else {},
                        })
            logger.debug("Discovered %d VM instances in project %s", len(instances), project)
            return instances
        except ImportError:
            logger.debug("google-cloud-compute not installed; skipping instance discovery")
            return []
        except Exception as e:
            logger.warning("GCP instance discovery failed for project %s: %s", project, e)
            return []

    # ── GKE Clusters ──────────────────────────────────────────────

    def discover_gke_clusters(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all GKE clusters and enumerate workloads via GKEConnector.

        Returns a list of dicts with keys: name, location, kubernetes_version,
        node_count, status, workloads (from GKEConnector).
        """
        project = self._resolve_project(project_id)
        if not project:
            logger.warning("GCP project not configured; skipping GKE discovery")
            return []

        try:
            container = self._get_container_client()
            client = container.ClusterManagerClient()
            parent = f"projects/{project}/locations/-"
            clusters = []
            try:
                resp = client.list_clusters(parent=parent)
            except Exception:
                resp = client.list_clusters(project_id=project)

            for cluster in resp.clusters:
                cluster_info = {
                    "name": cluster.name,
                    "location": cluster.location,
                    "kubernetes_version": cluster.current_master_version,
                    "node_count": sum(
                        np.node_count for np in (cluster.node_pools or [])
                    ),
                    "status": cluster.status.name if hasattr(cluster.status, "name") else str(cluster.status),
                    "workloads": {},
                }

                # Enumerate workloads via existing GKEConnector
                try:
                    from .gke_connector import GKEConnector
                    gke = GKEConnector()
                    gke_inv = gke.discover_cluster(
                        cluster_name=cluster.name,
                        region=cluster.location,
                        project=project,
                    )
                    cluster_info["workloads"] = {
                        "deployments": [
                            {"name": d["name"], "namespace": d["namespace"], "replicas": d["replicas"]}
                            for d in gke_inv.deployments
                        ],
                        "pods": [
                            {"name": p["name"], "namespace": p["namespace"], "status": p["status"]}
                            for p in gke_inv.pods
                        ],
                        "services": [
                            {"name": s["name"], "namespace": s["namespace"], "type": s["type"]}
                            for s in gke_inv.services
                        ],
                        "ingresses": [
                            {"name": i["name"], "namespace": i["namespace"], "hosts": i["hosts"]}
                            for i in gke_inv.ingresses
                        ],
                    }
                except ImportError:
                    logger.debug("GKE Kubernetes SDK not installed; skipping workload enumeration")
                except Exception as e:
                    logger.warning("GKE workload enumeration failed for %s: %s", cluster.name, e)

                clusters.append(cluster_info)

            logger.debug("Discovered %d GKE clusters in project %s", len(clusters), project)
            return clusters
        except ImportError:
            logger.debug("google-cloud-container not installed; skipping GKE discovery")
            return []
        except Exception as e:
            logger.warning("GCP GKE cluster discovery failed for project %s: %s", project, e)
            return []

    # ── Load Balancers (Forwarding Rules) ─────────────────────────

    def discover_load_balancers(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all forwarding rules (regional and global).

        Discovers regions first, then lists regional rules and global rules.

        Returns a list of dicts with keys: name, region, ip_address, protocol,
        ports, target.
        """
        project = self._resolve_project(project_id)
        if not project:
            logger.warning("GCP project not configured; skipping LB discovery")
            return []

        try:
            compute = self._get_compute_client()
            forwarding_rules = []

            # — Regional forwarding rules —
            try:
                regions_client = compute.RegionsClient()
                forwarding_client = compute.ForwardingRulesClient()
                regions = list(regions_client.list(project=project))
                for region in regions:
                    region_name = region.name
                    try:
                        rules = forwarding_client.list(project=project, region=region_name)
                        for rule in rules:
                            ports = list(rule.port_range.split("-")) if rule.port_range else []
                            forwarding_rules.append({
                                "name": rule.name,
                                "region": region_name,
                                "ip_address": rule.i_p_address or "",
                                "protocol": rule.i_p_protocol or "",
                                "ports": ports,
                                "target": rule.target or "",
                            })
                    except Exception as e:
                        logger.warning("GCP forwarding rules list failed for region %s: %s", region_name, e)
            except Exception as e:
                logger.warning("GCP regions list failed: %s", e)

            # — Global forwarding rules —
            try:
                global_client = compute.GlobalForwardingRulesClient()
                global_rules = global_client.list(project=project)
                for rule in global_rules:
                    ports = list(rule.port_range.split("-")) if rule.port_range else []
                    forwarding_rules.append({
                        "name": rule.name,
                        "region": "global",
                        "ip_address": rule.i_p_address or "",
                        "protocol": rule.i_p_protocol or "",
                        "ports": ports,
                        "target": rule.target or "",
                    })
            except Exception as e:
                logger.warning("GCP global forwarding rules list failed: %s", e)

            logger.debug("Discovered %d load balancers in project %s", len(forwarding_rules), project)
            return forwarding_rules
        except ImportError:
            logger.debug("google-cloud-compute not installed; skipping LB discovery")
            return []
        except Exception as e:
            logger.warning("GCP load balancer discovery failed for project %s: %s", project, e)
            return []

    # ── Combined Discovery ────────────────────────────────────────

    def discover_all(self, project_id: Optional[str] = None) -> GCPInventory:
        """Run all resource discovery methods and return combined inventory.

        Each resource type is discovered independently so that a single
        resource failure does not prevent other types from being enumerated.
        """
        project = self._resolve_project(project_id)
        inv = GCPInventory(project_id=project)
        inv.vpcs = self.discover_vpcs(project_id=project)
        inv.instances = self.discover_instances(project_id=project)
        inv.gke_clusters = self.discover_gke_clusters(project_id=project)
        inv.load_balancers = self.discover_load_balancers(project_id=project)
        return inv
