# Copyright (c) Microsoft. All rights reserved.
# On-Call Copilot - Multi-Agent Hosted Agent

import logging
import os
import sys
from pathlib import Path

from agent_framework import Agent
from agent_framework_foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from agent_framework_orchestrations import ConcurrentBuilder
from agent_framework_openai import OpenAIChatClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from app.agents.comms import COMMS_INSTRUCTIONS
from app.agents.pir import PIR_INSTRUCTIONS
from app.agents.summary import SUMMARY_INSTRUCTIONS
from app.agents.triage import TRIAGE_INSTRUCTIONS
from app.rag import create_rag_tools
from app.skill_loader import get_context_for_agent
from app.infra_topology import tool_definitions as topology_tools
from app.infra_topology.agent import AzureTopologyAgent, GCPTopologyAgent
from app.mcp_tools import create_mcp_tools

load_dotenv(Path(__file__).resolve().parent / ".env")


def _presence(name: str) -> str:
    return "set" if os.environ.get(name) else "unset"


print(f"[oncall-copilot] Starting... Python {sys.version}", flush=True)
print(f"[oncall-copilot] AZURE_AI_PROJECT_ENDPOINT={_presence('AZURE_AI_PROJECT_ENDPOINT')}", flush=True)
print(f"[oncall-copilot] AZURE_MODEL_PROJECT_ENDPOINT={_presence('AZURE_MODEL_PROJECT_ENDPOINT')}", flush=True)
print(f"[oncall-copilot] AZURE_OPENAI_ENDPOINT={_presence('AZURE_OPENAI_ENDPOINT')}", flush=True)
print(f"[oncall-copilot] AZURE_OPENAI_API_KEY={_presence('AZURE_OPENAI_API_KEY')}", flush=True)
print(f"[oncall-copilot] AZURE_OPENAI_CHAT_DEPLOYMENT_NAME={_presence('AZURE_OPENAI_CHAT_DEPLOYMENT_NAME')}", flush=True)
print(f"[oncall-copilot] MCP_SERVERS={'set' if os.environ.get('MCP_SERVERS') else 'unset'}", flush=True)
print(f"[oncall-copilot] SKILLS_DIR={os.environ.get('SKILLS_DIR', 'skills/elastic/ (default)')}", flush=True)

_credential = DefaultAzureCredential()


def _fetch_topology_context() -> str:
    """Fetch Azure and GCP topology, return context strings for agent instructions.

    Returns an empty string if no topology is available, so incident
    analysis continues gracefully without infrastructure context.
    """
    parts = []
    try:
        agent = AzureTopologyAgent()
        result = agent.fetch_topology()
        summary = result.get("summary", "")
        if summary and summary != "No Azure topology available.":
            parts.append(f"## Azure Infrastructure Context\n{summary}")
    except Exception:
        logging.getLogger(__name__).debug("Azure topology fetch failed at startup")
    try:
        agent = GCPTopologyAgent()
        result = agent.fetch_topology()
        summary = result.get("summary", "")
        if summary and summary != "No GCP topology available.":
            parts.append(f"## GCP Infrastructure Context\n{summary}")
    except Exception:
        logging.getLogger(__name__).debug("GCP topology fetch failed at startup")
    if parts:
        return "\n\n" + "\n\n".join(parts) + "\n"
    return ""


def create_workflow():
    """Create 4 specialist agents and wire them into a concurrent workflow."""

    model = os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME", "model-router")

    # Detect auth mode:
    #   1. Standard Azure OpenAI (AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY)
    #      — avoids the Foundry AIProjectClient and client.agents.create_version entirely.
    #   2. Foundry project endpoint (AZURE_MODEL_PROJECT_ENDPOINT / AZURE_AI_PROJECT_ENDPOINT)
    #      — uses FoundryChatClient with DefaultAzureCredential.
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    azure_api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    azure_api_version = os.environ.get("AZURE_OPENAI_API_VERSION")
    foundry_endpoint = os.environ.get("AZURE_MODEL_PROJECT_ENDPOINT") or os.environ.get("AZURE_AI_PROJECT_ENDPOINT")

    if azure_endpoint and azure_api_key and not foundry_endpoint:
        print("[oncall-copilot] Using OpenAIChatClient (standard Azure OpenAI endpoint + API key)", flush=True)
        print(f"[oncall-copilot] AZURE_OPENAI_API_VERSION={azure_api_version or 'default (preview)'}", flush=True)
        chat_client = OpenAIChatClient(
            model=model,
            azure_endpoint=azure_endpoint,
            api_key=azure_api_key,
            api_version=azure_api_version,
        )
    elif foundry_endpoint:
        print("[oncall-copilot] Using FoundryChatClient (Foundry project endpoint)", flush=True)
        chat_client = FoundryChatClient(
            project_endpoint=foundry_endpoint,
            model=model,
            credential=_credential,
        )
    else:
        raise RuntimeError(
            "No Azure credentials found. Set either:\n"
            "  - AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY (standard Azure OpenAI)\n"
            "  - AZURE_MODEL_PROJECT_ENDPOINT or AZURE_AI_PROJECT_ENDPOINT (Foundry project)\n"
            "  - MOCK_MODE=true (for local testing without Azure)"
        )

    topology_context = _fetch_topology_context()

    # Pre-load combined skills context for each agent type
    skills_triage = get_context_for_agent("triage")
    skills_summary = get_context_for_agent("summary")
    skills_comms = get_context_for_agent("comms")
    skills_pir = get_context_for_agent("pir")
    if skills_triage:
        print("[oncall-copilot] Agent Skills (Elastic + Microsoft) loaded for triage agent", flush=True)

    _rag_guidance = (
        "\n\nYou have access to a `search_knowledge_base` tool that can retrieve "
        "runbooks, post-incident reviews, and operational playbooks from the "
        "knowledge base. Use this tool whenever the incident references:\n"
        "- specific remediation steps (e.g. failover, drain, rotation)\n"
        "- configuration best practices for a service you are unfamiliar with\n"
        "- historical incident patterns that might inform the current situation\n"
        "- runbook excerpts mentioned in the incident payload\n\n"
        "When you use the search tool, cite the source document title and "
        "category in your output so the reader can verify the reference."
    )

    def _with_context(base: str, skills_context: str = "") -> str:
        """Combine topology context, skills context, and RAG guidance with base instructions."""
        parts = [base]
        if topology_context:
            parts.append(topology_context)
        if skills_context:
            parts.append(skills_context)
        if _rag_tools:
            parts.append(_rag_guidance)
        return "\n\n".join(parts)

    try:
        _mcp_tools = create_mcp_tools()
        if _mcp_tools:
            print(f"[oncall-copilot] {len(_mcp_tools)} MCP server(s) enabled", flush=True)
    except Exception:
        logging.getLogger(__name__).debug("MCP tool creation failed — continuing without MCP")
        _mcp_tools = []

    _rag_tools = create_rag_tools()
    if _rag_tools:
        print(f"[oncall-copilot] RAG: {len(_rag_tools)} knowledge-base search tool(s) enabled", flush=True)
    else:
        print("[oncall-copilot] RAG: disabled", flush=True)

    _tools = [
        *topology_tools.TOPOLOGY_TOOLS,
        *_mcp_tools,
        *_rag_tools,
    ]

    triage = Agent(
        client=chat_client,
        instructions=_with_context(TRIAGE_INSTRUCTIONS, skills_triage),
        name="triage-agent",
        tools=_tools,
    )
    summary = Agent(
        client=chat_client,
        instructions=_with_context(SUMMARY_INSTRUCTIONS, skills_summary),
        name="summary-agent",
        tools=_tools,
    )
    comms = Agent(
        client=chat_client,
        instructions=_with_context(COMMS_INSTRUCTIONS, skills_comms),
        name="comms-agent",
        tools=_tools,
    )
    pir = Agent(
        client=chat_client,
        instructions=_with_context(PIR_INSTRUCTIONS, skills_pir),
        name="pir-agent",
        tools=_tools,
    )

    return ConcurrentBuilder(
        participants=[triage, summary, comms, pir],
        intermediate_outputs=False,
    ).build()


def main():
    print("[oncall-copilot] Building workflow...", flush=True)
    workflow_agent = create_workflow().as_agent(
        name="oncall-copilot",
        description="Runs triage, summary, comms, and PIR agents for incident response.",
    )
    print("[oncall-copilot] Starting server on port 8088...", flush=True)
    ResponsesHostServer(workflow_agent).run(port=8088)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[oncall-copilot] FATAL: {e}", flush=True)
        import traceback

        traceback.print_exc()
        sys.exit(1)
