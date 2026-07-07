"""Azure infrastructure discovery connector."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import config, get_azure_credential

logger = logging.getLogger(__name__)


@dataclass
class AzureSubscription:
    """Represents an Azure subscription with name and ID."""
    subscription_id: str
    subscription_name: str = ""
    tenant_id: str = ""
    state: str = ""


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

    def list_subscriptions(self) -> List[AzureSubscription]:
        """List all Azure subscriptions the current credential has access to.

        Uses SubscriptionClient from azure-mgmt-resource to enumerate all
        subscriptions accessible by the current DefaultAzureCredential.

        Returns:
            List of AzureSubscription dataclass instances sorted by name.
            Empty list if the SDK is not installed or the call fails.
        """
        try:
            from azure.mgmt.resource import SubscriptionClient

            credential = get_azure_credential()
            sub_client = SubscriptionClient(credential)
            subs: List[AzureSubscription] = []
            for sub in sub_client.subscriptions.list():
                subs.append(
                    AzureSubscription(
                        subscription_id=sub.subscription_id,
                        subscription_name=sub.display_name or "",
                        tenant_id=sub.tenant_id or "",
                        state=sub.state or "",
                    )
                )
            # Sort by name for consistent ordering
            subs.sort(key=lambda s: s.subscription_name.lower())
            logger.debug("Discovered %d Azure subscriptions", len(subs))
            return subs
        except ImportError:
            logger.debug(
                "Azure SDK not installed; cannot list subscriptions. "
                "Install azure-mgmt-resource."
            )
            return []
        except Exception as e:
            logger.warning("Azure subscription listing failed: %s", e)
            return []

    def resolve_subscription(
        self, identifier: str
    ) -> Optional[AzureSubscription]:
        """Resolve a subscription name or partial ID to a full subscription.

        First tries to match as a subscription ID (exact or prefix), then
        as a display name (case-insensitive). Returns the first match or None.

        Args:
            identifier: Subscription name, display name, or ID prefix to resolve.

        Returns:
            AzureSubscription if found, or None if no match.
        """
        subs = self.list_subscriptions()
        if not subs:
            return None

        identifier_lower = identifier.lower().strip()

        # 1. Try exact subscription ID match
        for sub in subs:
            if sub.subscription_id.lower() == identifier_lower:
                return sub

        # 2. Try exact display name match (case-insensitive)
        for sub in subs:
            if sub.subscription_name.lower() == identifier_lower:
                return sub

        # 3. Try prefix match on subscription ID
        for sub in subs:
            if sub.subscription_id.lower().startswith(identifier_lower):
                return sub

        # 4. Try partial match on display name
        for sub in subs:
            if identifier_lower in sub.subscription_name.lower():
                return sub

        logger.debug("No subscription matched identifier: %s", identifier)
        return None

    def discover_inventory(
        self,
        subscription_id: Optional[str] = None,
    ) -> AzureInventory:
        sub = subscription_id or self.subscription_id
        inv = AzureInventory(subscription_id=sub)

        try:
            from azure.mgmt.compute import ComputeManagementClient
            from azure.mgmt.containerservice import ContainerServiceClient
            from azure.mgmt.network import NetworkManagementClient
            from azure.mgmt.resource.resources import ResourceManagementClient

            credential = get_azure_credential()

            rg_client = ResourceManagementClient(credential, sub)
            for rg in rg_client.resource_groups.list():
                logger.info("Find Azure resource group: %s", rg.name)
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
                logger.info("Discovering resources in Azure resource group: %s", rg_name)
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
                        lb_dict = lb.as_dict() if hasattr(lb, "as_dict") else {}
                        props = lb_dict.get("properties", {}) or {}
                        frontend_configs = props.get("frontendIPConfigurations", []) or []
                        inv.load_balancers.append(
                            {
                                "name": lb_dict.get("name") or getattr(lb, "name", ""),
                                "location": lb_dict.get("location") or getattr(lb, "location", ""),
                                "resource_group": rg_name,
                                "frontend_ips": [
                                    fip.get("name") or ""
                                    for fip in frontend_configs
                                    if isinstance(fip, dict)
                                ],
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
                    list_clusters = getattr(aks_client.managed_clusters, "list_by_resource_group", None)
                    if callable(list_clusters):
                        cluster_iterable = list_clusters(rg_name)
                    else:
                        cluster_iterable = aks_client.managed_clusters.list(rg_name)
                    for aks in cluster_iterable:
                        aks_dict = aks.as_dict() if hasattr(aks, "as_dict") else {}
                        props = aks_dict.get("properties", {}) or {}
                        agent_pool_profiles = props.get("agentPoolProfiles", []) or []
                        node_count = 0
                        if agent_pool_profiles:
                            for pool in agent_pool_profiles:
                                if isinstance(pool, dict):
                                    count = pool.get("count") or 0
                                    if isinstance(count, int):
                                        node_count += count
                        inv.aks_clusters.append(
                            {
                                "name": aks_dict.get("name") or getattr(aks, "name", ""),
                                "location": aks_dict.get("location") or getattr(aks, "location", ""),
                                "resource_group": rg_name,
                                "kubernetes_version": props.get("kubernetesVersion", ""),
                                "node_count": node_count,
                                "tags": dict(getattr(aks, "tags", None) or {}),
                            }
                        )
                except Exception as e:
                    logger.warning("Azure AKS discovery failed for rg=%s: %s", rg_name, e)

        except ImportError:
            logger.debug("Azure SDK not installed, skipping Azure resource discovery")
        except Exception as e:
            logger.warning("Azure resource discovery failed: %s", e)

        return inv