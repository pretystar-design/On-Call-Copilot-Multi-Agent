"""Application configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Optional

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
