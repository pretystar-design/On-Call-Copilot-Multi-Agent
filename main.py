# Copyright (c) Microsoft. All rights reserved.
# On-Call Copilot - Multi-Agent Hosted Agent

import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path

import uvicorn
from agent_framework import Agent
from agent_framework_foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from agent_framework_orchestrations import ConcurrentBuilder
from agent_framework_openai import OpenAIChatClient
from dotenv import load_dotenv

from app.config import get_azure_credential

from app.agents.comms import COMMS_INSTRUCTIONS
from app.agents.pir import PIR_INSTRUCTIONS
from app.agents.summary import SUMMARY_INSTRUCTIONS
from app.agents.triage import TRIAGE_INSTRUCTIONS
from app.rag import create_rag_tools
from app.skill_loader import get_context_for_agent
from app.infra_topology import tool_definitions as topology_tools
from app.infra_topology.agent import AzureTopologyAgent, GCPTopologyAgent
from app.mcp_tools import create_mcp_tools
from app.a2a_remote import create_remote_a2a_tools

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

_credential = get_azure_credential()


_RAG_GUIDANCE = (
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


def _build_chat_client():
    """Build the chat client based on environment configuration.

    Supports two auth modes:
      1. Standard Azure OpenAI (AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY)
      2. Foundry project endpoint (AZURE_MODEL_PROJECT_ENDPOINT / AZURE_AI_PROJECT_ENDPOINT)
    """
    model = os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME", "model-router")
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    azure_api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    azure_api_version = os.environ.get("AZURE_OPENAI_API_VERSION")
    foundry_endpoint = os.environ.get("AZURE_MODEL_PROJECT_ENDPOINT") or os.environ.get(
        "AZURE_AI_PROJECT_ENDPOINT"
    )

    if azure_endpoint and azure_api_key and not foundry_endpoint:
        print("[oncall-copilot] Using OpenAIChatClient (standard Azure OpenAI endpoint + API key)", flush=True)
        print(f"[oncall-copilot] AZURE_OPENAI_API_VERSION={azure_api_version or 'default (preview)'}", flush=True)
        return OpenAIChatClient(
            model=model,
            azure_endpoint=azure_endpoint,
            api_key=azure_api_key,
            api_version=azure_api_version,
        )
    elif foundry_endpoint:
        print("[oncall-copilot] Using FoundryChatClient (Foundry project endpoint)", flush=True)
        return FoundryChatClient(
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


def _build_tools():
    """Build the shared tool set (topology + MCP + RAG) for specialist agents."""
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

    _remote_a2a_tools = create_remote_a2a_tools()
    if _remote_a2a_tools:
        print(f"[oncall-copilot] A2A: {len(_remote_a2a_tools)} remote agent tool(s) enabled", flush=True)

    return [
        *topology_tools.TOPOLOGY_TOOLS,
        *_mcp_tools,
        *_rag_tools,
        *_remote_a2a_tools,
    ]


def _with_context(
    base: str,
    topology_context: str,
    skills_context: str = "",
    has_rag_tools: bool = False,
) -> str:
    """Combine topology context, skills context, and RAG guidance with base instructions."""
    parts = [base]
    if topology_context:
        parts.append(topology_context)
    if skills_context:
        parts.append(skills_context)
    if has_rag_tools:
        parts.append(_RAG_GUIDANCE)
    return "\n\n".join(parts)


def create_specialist_agents(chat_client):
    """Create 4 specialist agent instances with full context and tools.

    Returns:
        tuple: (triage_agent, summary_agent, comms_agent, pir_agent, tools)
    """
    topology_context = _fetch_topology_context()

    skills_triage = get_context_for_agent("triage")
    skills_summary = get_context_for_agent("summary")
    skills_comms = get_context_for_agent("comms")
    skills_pir = get_context_for_agent("pir")
    if skills_triage:
        print("[oncall-copilot] Agent Skills (Elastic + Microsoft) loaded for triage agent", flush=True)

    tools = _build_tools()
    has_rag = any(
        getattr(t, "name", "") == "search_knowledge_base"
        or getattr(t, "function", {}).get("name", "") == "search_knowledge_base"
        for t in tools
    )

    triage = Agent(
        client=chat_client,
        instructions=_with_context(
            TRIAGE_INSTRUCTIONS, topology_context, skills_triage, has_rag
        ),
        name="triage-agent",
        tools=tools,
    )
    summary = Agent(
        client=chat_client,
        instructions=_with_context(
            SUMMARY_INSTRUCTIONS, topology_context, skills_summary, has_rag
        ),
        name="summary-agent",
        tools=tools,
    )
    comms = Agent(
        client=chat_client,
        instructions=_with_context(
            COMMS_INSTRUCTIONS, topology_context, skills_comms, has_rag
        ),
        name="comms-agent",
        tools=tools,
    )
    pir = Agent(
        client=chat_client,
        instructions=_with_context(
            PIR_INSTRUCTIONS, topology_context, skills_pir, has_rag
        ),
        name="pir-agent",
        tools=tools,
    )

    return triage, summary, comms, pir, tools


def create_workflow():
    """Create 4 specialist agents and wire them into a concurrent workflow."""
    chat_client = _build_chat_client()
    triage, summary, comms, pir, _tools = create_specialist_agents(chat_client)

    return ConcurrentBuilder(
        participants=[triage, summary, comms, pir],
        intermediate_outputs=False,
    ).build()


def _run_a2a_server(chat_client, triage, summary, comms, pir, tools):
    """Start the A2A protocol server on a background thread."""
    from app.a2a_server import run_a2a_server

    a2a_port = int(os.environ.get("A2A_PORT", "8089"))
    a2a_enabled = os.environ.get("A2A_ENABLED", "true").lower() in ("true", "1", "yes")

    if not a2a_enabled:
        print("[oncall-copilot] A2A server disabled (A2A_ENABLED != true)", flush=True)
        return

    print(f"[oncall-copilot] Starting A2A server on port {a2a_port}...", flush=True)
    run_a2a_server(
        chat_client=chat_client,
        specialists=(triage, summary, comms, pir),
        tools=tools,
        port=a2a_port,
    )


def main():
    print("[oncall-copilot] Building workflow...", flush=True)

    chat_client = _build_chat_client()
    triage, summary, comms, pir, tools = create_specialist_agents(chat_client)

    workflow_agent = ConcurrentBuilder(
        participants=[triage, summary, comms, pir],
        intermediate_outputs=False,
    ).build().as_agent(
        name="oncall-copilot",
        description="Runs triage, summary, comms, and PIR agents for incident response.",
    )

    # Start A2A server on a background thread (if enabled)
    _run_a2a_server(chat_client, triage, summary, comms, pir, tools)

    print("[oncall-copilot] Starting Responses API server on port 8088...", flush=True)
    ResponsesHostServer(workflow_agent).run(port=8088)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[oncall-copilot] FATAL: {e}", flush=True)
        import traceback

        traceback.print_exc()
        sys.exit(1)
