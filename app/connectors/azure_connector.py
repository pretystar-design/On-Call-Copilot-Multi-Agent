"""Azure infrastructure discovery connector."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import config

logger = logging.getLogger(__name__)


@dataclass
class AzureInventory:
    subscription_id: str
    resource_groups: List[Dict[str, str]] = field(default_factory=list)
    aks_clusters: List[Dict[str, Any]] = field(default_factory=list)
    service_fabric: List[Dict[str, Any]] = field(default_factory=list)
    vms: List[Dict[str, Any]] = field(default_factory=list)
    vnets: List[Dict[str, Any]] = field(default_factory=list)
    load_balancers: List[Dict[str, Any]] = field(default_factory=list)


class AzureConnector:
    def __init__(self) -> None:
        self.subscription_id = getattr(config, "azure_subscription_id", "")
        self.tenant_id = getattr(config, "azure_tenant_id", "")
        self.client_id = getattr(config, "azure_client_id", "")
        self.client_secret = getattr(config, "azure_client_secret", "")

    def discover_inventory(
        self,
        subscription_id: Optional[str] = None,
    ) -> AzureInventory:
        sub = subscription_id or self.subscription_id
        inv = AzureInventory(subscription_id=sub)

        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.compute import ComputeManagementClient
            from azure.mgmt.containerservice import ContainerServiceClient
            from azure.mgmt.network import NetworkManagementClient
            from azure.mgmt.resource.resources import ResourceManagementClient

            credential = DefaultAzureCredential()

            rg_client = ResourceManagementClient(credential, sub)
            for rg in rg_client.resource_groups.list():
                inv.resource_groups.append(
                    {
                        "name": rg.name,
                        "location": rg.location,
                        "tags": dict(rg.tags or {}),
                    }
                )

            compute_client = ComputeManagementClient(credential, sub)
            network_client = NetworkManagementClient(credential, sub)
            aks_client = ContainerServiceClient(credential, sub)

            for rg in inv.resource_groups:
                rg_name = rg["name"]
                try:
                    for vm in compute_client.virtual_machines.list(rg_name):
                        inv.vms.append(
                            {
                                "name": vm.name,
                                "location": vm.location,
                                "resource_group": rg_name,
                                "vm_size": getattr(vm.hardware_profile, "vm_size", ""),
                                "tags": dict(vm.tags or {}),
                            }
                        )
                except Exception as e:
                    logger.warning("Azure VM discovery failed for rg=%s: %s", rg_name, e)
                try:
                    for lb in network_client.load_balancers.list(rg_name):
                        inv.load_balancers.append(
                            {
                                "name": lb.name,
                                "location": lb.location,
                                "resource_group": rg_name,
                                "frontend_ips": [fip.name for fip in lb.frontend_ip_configs],
                            }
                        )
                except Exception as e:
                    logger.warning("Azure load balancer discovery failed for rg=%s: %s", rg_name, e)
                try:
                    for nic in network_client.network_interfaces.list(rg_name):
                        inv.vnets.append(
                            {
                                "name": nic.name,
                                "location": nic.location,
                                "resource_group": rg_name,
                                "subnet": "",
                            }
                        )
                except Exception as e:
                    logger.warning("Azure VNet discovery failed for rg=%s: %s", rg_name, e)
                try:
                    for aks in aks_client.managed_clusters.list(rg_name):
                        inv.aks_clusters.append(
                            {
                                "name": aks.name,
                                "location": aks.location,
                                "resource_group": rg_name,
                                "kubernetes_version": aks.kubernetes_version,
                                "node_count": aks.agent_pool_profiles[0].count
                                if aks.agent_pool_profiles
                                else 0,
                                "tags": dict(aks.tags or {}),
                            }
                        )
                except Exception as e:
                    logger.warning("Azure AKS discovery failed for rg=%s: %s", rg_name, e)

        except ImportError:
            logger.debug("Azure SDK not installed, skipping Azure resource discovery")
        except Exception as e:
            logger.warning("Azure resource discovery failed: %s", e)

        return inv
