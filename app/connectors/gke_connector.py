"""GKE infrastructure discovery connector."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class GKEInventory:
    cluster_name: str
    region: str
    deployments: List[Dict[str, Any]] = field(default_factory=list)
    pods: List[Dict[str, Any]] = field(default_factory=list)
    services: List[Dict[str, Any]] = field(default_factory=list)
    ingresses: List[Dict[str, Any]] = field(default_factory=list)


class GKEConnector:
    def discover_cluster(
        self,
        cluster_name: str,
        region: str,
        project: Optional[str] = None,
    ) -> GKEInventory:
        inv = GKEInventory(cluster_name=cluster_name, region=region)
        try:
            from google.cloud import container_v1
            from google.auth import default as google_auth_default

            credentials, proj = google_auth_default()
            project = project or proj
            client = container_v1.ClusterManagerClient(credentials=credentials)
            cluster = client.get_cluster(project, region, cluster_name)
            kubeconfig = self._build_kubeconfig(cluster)
            return self._discover_from_kubeconfig(kubeconfig, inv)
        except ImportError:
            logger.debug("GCP SDK not installed, skipping GKE discovery")
            return inv
        except Exception as e:
            logger.warning("GKE cluster discovery failed for %s/%s: %s", cluster_name, region, e)
            return inv

    def _build_kubeconfig(self, cluster) -> Dict[str, Any]:
        host = f"https://{cluster.endpoint}"
        token = ""
        ca_cert = cluster.master_auth.cluster_ca_certificate if cluster.master_auth else ""
        return {"host": host, "token": token, "ca_cert": ca_cert}

    def _discover_from_kubeconfig(
        self, kubeconfig: Dict[str, Any], inv: GKEInventory
    ) -> GKEInventory:
        try:
            from kubernetes import client, config as k8s_config
            from kubernetes.client import CoreV1Api, AppsV1Api, NetworkingV1Api

            k8s_config.load_kube_config()
            v1 = CoreV1Api()
            apps_v1 = AppsV1Api()
            net_v1 = NetworkingV1Api()
            for ns in v1.list_namespace().items:
                ns_name = ns.metadata.name
                for dep in apps_v1.list_namespaced_deployment(ns_name).items:
                    inv.deployments.append(
                        {
                            "name": dep.metadata.name,
                            "namespace": ns_name,
                            "labels": dict(dep.metadata.labels or {}),
                            "replicas": dep.spec.replicas or 0,
                        }
                    )
                for pod in v1.list_namespaced_pod(ns_name).items:
                    inv.pods.append(
                        {
                            "name": pod.metadata.name,
                            "namespace": ns_name,
                            "status": pod.status.phase,
                            "node": pod.spec.node_name,
                            "labels": dict(pod.metadata.labels or {}),
                        }
                    )
                for svc in v1.list_namespaced_service(ns_name).items:
                    inv.services.append(
                        {
                            "name": svc.metadata.name,
                            "namespace": ns_name,
                            "type": svc.spec.type,
                            "ports": [p.port for p in svc.spec.ports],
                            "selectors": dict(svc.spec.selector or {}),
                        }
                    )
                for ing in net_v1.list_namespaced_ingress(ns_name).items:
                    hosts = [r.host for r in (ing.spec.rules or [])]
                    backends = []
                    if ing.spec.backend:
                        backends = [ing.spec.backend.service_name]
                    inv.ingresses.append(
                        {
                            "name": ing.metadata.name,
                            "namespace": ns_name,
                            "hosts": hosts,
                            "backends": backends,
                        }
                    )
        except ImportError:
            logger.debug("Kubernetes Python client not installed, skipping k8s resource discovery")
        except Exception as e:
            logger.warning("GKE kubeconfig resource discovery failed: %s", e)
        return inv
