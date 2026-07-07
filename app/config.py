"""Application configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    """Central config populated from environment variables with sensible defaults."""

    # Azure
    azure_subscription_id: str = field(
        default_factory=lambda: os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    )
    azure_tenant_id: str = field(
        default_factory=lambda: os.environ.get("AZURE_TENANT_ID", "")
    )
    azure_client_id: str = field(
        default_factory=lambda: os.environ.get("AZURE_CLIENT_ID", "")
    )
    azure_client_secret: str = field(
        default_factory=lambda: os.environ.get("AZURE_CLIENT_SECRET", "")
    )
    
    # Azure subscription list (populated programmatically by subscription resolver)
    azure_subscriptions: List[str] = field(default_factory=list)

    # GCP
    gcp_project_id: str = field(
        default_factory=lambda: os.environ.get("GCP_PROJECT_ID", "")
    )

    # Topology cache
    topology_cache_dir: str = field(
        default_factory=lambda: os.environ.get(
            "TOPOLOGY_CACHE_DIR",
            os.path.expanduser("~/.oncall-copilot/topology"),
        )
    )
    topology_cache_ttl: int = field(
        default_factory=lambda: int(os.environ.get("TOPOLOGY_CACHE_TTL", "86400"))
    )


config = Config()


def get_azure_credential():
    """Create an Azure credential using service principal secrets when available."""
    from azure.identity import ClientSecretCredential, DefaultAzureCredential

    tenant_id = os.environ.get("AZURE_TENANT_ID") or getattr(config, "azure_tenant_id", "")
    client_id = os.environ.get("AZURE_CLIENT_ID") or getattr(config, "azure_client_id", "")
    client_secret = os.environ.get("AZURE_CLIENT_SECRET") or getattr(config, "azure_client_secret", "")

    if tenant_id and client_id and client_secret:
        return ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )

    return DefaultAzureCredential()
