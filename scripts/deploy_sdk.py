"""
deploy_sdk.py - Deploy the On-Call Copilot as a Foundry Hosted Agent using the Python SDK.

Ref: https://learn.microsoft.com/azure/ai-foundry/agents/how-to/deploy-hosted-agent

Prerequisites:
    1. azure-ai-projects >= 2.1.0
  2. Container image pushed to Azure Container Registry
  3. Project managed identity has Container Registry Repository Reader on ACR
  4. Account-level capability host with enablePublicHostingEnvironment=true

Usage:
    # Set required environment variables
    export AZURE_AI_PROJECT_ENDPOINT="https://<account>.services.ai.azure.com/api/projects/<project>"
    export ACR_IMAGE="myregistry.azurecr.io/oncall-copilot:v1"
    export MODEL_ROUTER_DEPLOYMENT="model-router"

    python scripts/deploy_sdk.py
    python scripts/deploy_sdk.py --delete          # clean up

    # Region validation (the region is determined by the Foundry project endpoint):
    # --region only validates against the supported list; it is NOT passed as an API parameter.
    python scripts/deploy_sdk.py --region eastus
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    ContainerConfiguration,
    HostedAgentDefinition,
    ProtocolVersionRecord,
)
from azure.identity import AzureCliCredential
from dotenv import load_dotenv

AGENT_NAME = "oncall-copilot"
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)

# Supported regions for Foundry Hosted Agents (as of July 2026)
# Ref: https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents#region-availability
SUPPORTED_REGIONS: set[str] = {
    "australiaeast", "brazilsouth", "canadacentral", "canadaeast",
    "eastus", "eastus2",
    "francecentral",
    "germanywestcentral",
    "italynorth",
    "japaneast",
    "koreacentral",
    "northcentralus", "norwayeast",
    "polandcentral",
    "southafricanorth", "southcentralus", "southeastasia", "southindia", "spaincentral", "swedencentral", "switzerlandnorth",
    "uaenorth", "uksouth",
    "westus", "westus3",
}


def get_config() -> dict:
    endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
    model_project_endpoint = os.environ.get("AZURE_MODEL_PROJECT_ENDPOINT", endpoint)
    openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    image = os.environ.get("ACR_IMAGE", "")
    model = os.environ.get("MODEL_ROUTER_DEPLOYMENT", "model-router")
    region = os.environ.get("AZURE_REGION", "")

    if not endpoint:
        print("ERROR: AZURE_AI_PROJECT_ENDPOINT env var is required.")
        sys.exit(1)
    if not image:
        print("ERROR: ACR_IMAGE env var is required (e.g. myregistry.azurecr.io/oncall-copilot:v1).")
        sys.exit(1)
    if not openai_endpoint:
        print("ERROR: AZURE_OPENAI_ENDPOINT env var is required.")
        sys.exit(1)

    return {
        "endpoint": endpoint,
        "model_project_endpoint": model_project_endpoint,
        "openai_endpoint": openai_endpoint,
        "image": image,
        "model": model,
        "region": region,
    }


def deploy(cfg: dict) -> None:
    """Create a new hosted agent version using the Python SDK."""
    client = AIProjectClient(
        endpoint=cfg["endpoint"],
        credential=AzureCliCredential(),
        allow_preview=True,
    )

    print(f"Creating hosted agent version: {AGENT_NAME}")
    print(f"  Image:    {cfg['image']}")
    print(f"  Endpoint: {cfg['endpoint']}")
    print(f"  Model:    {cfg['model']}")
    print()

    definition = HostedAgentDefinition(
        protocol_versions=[
            ProtocolVersionRecord(
                protocol="responses",
                version="1.0.0",
            )
        ],
        cpu="1",
        memory="2Gi",
        container_configuration=ContainerConfiguration(image=cfg["image"]),
        environment_variables={
            "AZURE_AI_PROJECT_ENDPOINT": cfg["endpoint"],
            "AZURE_MODEL_PROJECT_ENDPOINT": cfg["model_project_endpoint"],
            "AZURE_OPENAI_ENDPOINT": cfg["openai_endpoint"],
            "AZURE_OPENAI_CHAT_DEPLOYMENT_NAME": cfg["model"],
            "MODEL_ROUTER_DEPLOYMENT": cfg["model"],
            "LOG_LEVEL": "INFO",
        },
    )

    agent = client.agents.create_version(
        agent_name=AGENT_NAME,
        definition=definition,
        description="On-Call Copilot multi-agent incident response workflow.",
    )

    print(f"Agent deployed successfully!")
    print(f"  Name:    {AGENT_NAME}")
    print(f"  ID:      {agent.id}")
    print()
    print("Verify with:")
    print(f"  az cognitiveservices agent show --account-name <account> --project-name <project> --name {AGENT_NAME}")
    print()
    print("Test with (after deployment completes):")
    print(f'  az rest --method POST --url "<project-endpoint>/responses?api-version=2025-03-01-preview" --body \'{{\"model\":\"{AGENT_NAME}\",\"input\":\"test\"}}\' --resource "https://cognitiveservices.azure.com"')


def delete(cfg: dict) -> None:
    """Delete the latest hosted agent version."""
    client = AIProjectClient(
        endpoint=cfg["endpoint"],
        credential=AzureCliCredential(),
        allow_preview=True,
    )

    # List and delete the latest version
    print(f"Deleting hosted agent: {AGENT_NAME}")
    try:
        client.agents.delete_version(agent_name=AGENT_NAME, agent_version="latest")
        print("Agent version deleted.")
    except Exception as exc:
        print(f"Delete failed: {exc}")
        sys.exit(1)


def validate_region(region: str) -> None:
    """Check the given region is a supported Foundry Hosted Agents region, or exit."""
    region_normalized = region.lower().replace(" ", "")
    if region_normalized not in SUPPORTED_REGIONS:
        print(f"ERROR: Unsupported region '{region}' for Foundry Hosted Agents.", file=sys.stderr)
        print(file=sys.stderr)
        print("Supported regions:", file=sys.stderr)
        for r in sorted(SUPPORTED_REGIONS):
            print(f"  - {r}", file=sys.stderr)
        print(file=sys.stderr)
        print("The region is determined by your Foundry project endpoint location.", file=sys.stderr)
        print("Create the Foundry project in one of the supported regions above.", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Deploy On-Call Copilot to Foundry Agent Service")
    parser.add_argument("--delete", action="store_true", help="Delete the agent instead of deploying")
    parser.add_argument("--region", default="", help="Validate the region is supported for Foundry Hosted Agents (e.g. eastus). Also read from AZURE_REGION env var. The region is determined by the Foundry project endpoint, not passed as an API parameter.")
    args = parser.parse_args()

    cfg = get_config()

    # CLI --region takes precedence over env var
    if args.region:
        cfg["region"] = args.region

    if cfg["region"]:
        validate_region(cfg["region"])

    if args.delete:
        delete(cfg)
    else:
        deploy(cfg)


if __name__ == "__main__":
    main()
