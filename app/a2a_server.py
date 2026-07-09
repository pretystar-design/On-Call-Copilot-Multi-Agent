"""
A2A (Agent-to-Agent) Protocol Server for On-Call Copilot.

Exposes individual specialist agents (triage, summary, comms, PIR),
the chat agent, and the orchestrated concurrent workflow as A2A-compliant
JSON-RPC endpoints on a single Starlette server.

Endpoints (all served on port 8089, configurable via A2A_PORT):
  Agent             RPC Endpoint                      AgentCard
  ──────────────────────────────────────────────────────────────────
  Orchestrator*     POST /                            GET /.well-known/agent-card.json
  Triage            POST /a2a/triage/                 GET /a2a/triage/.well-known/agent-card.json
  Summary           POST /a2a/summary/                GET /a2a/summary/.well-known/agent-card.json
  Comms             POST /a2a/comms/                  GET /a2a/comms/.well-known/agent-card.json
  PIR               POST /a2a/pir/                    GET /a2a/pir/.well-known/agent-card.json
  Chat              POST /a2a/chat/                   GET /a2a/chat/.well-known/agent-card.json

  (*) Orchestrator runs all 4 specialist agents concurrently via ConcurrentBuilder,
      merging their outputs into a single structured response.

Architecture (from design.md):
  - Runs on a separate port (default 8089) alongside the main ResponsesHostServer (8088)
  - Started on a daemon background thread in main()
  - Uses A2AExecutor + DefaultRequestHandler + InMemoryTaskStore per agent
  - All agents share the same chat_client and tool set
  - Streaming is enabled for all endpoints
"""

from __future__ import annotations

import logging
import os
import threading

import uvicorn
from agent_framework import Agent
from agent_framework.a2a import A2AExecutor
from agent_framework_orchestrations import ConcurrentBuilder
from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication
from a2a.server.request_handlers.default_request_handler import (
    DefaultRequestHandler,
)
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AgentCard helpers
# ---------------------------------------------------------------------------

_CAPABILITIES = AgentCapabilities(streaming=True, push_notifications=False)


def _skill(id: str, name: str, description: str, tags: list[str]) -> AgentSkill:
    return AgentSkill(id=id, name=name, description=description, tags=tags, examples=[])


def _make_card(
    name: str,
    description: str,
    url: str,
    skills: list[AgentSkill],
) -> AgentCard:
    return AgentCard(
        name=name,
        description=description,
        url=url,
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=_CAPABILITIES,
        skills=skills,
    )


def _triage_card(url: str) -> AgentCard:
    return _make_card(
        "Triage Agent",
        "Root cause analysis for SRE incidents. Produces suspected root causes with confidence scores, immediate remediation actions, and runbook alignment.",
        url,
        skills=[
            _skill("triage", "Incident Triage", "Analyze incidents for root cause and remediation.", ["incident", "triage", "sre"]),
        ],
    )


def _summary_card(url: str) -> AgentCard:
    return _make_card(
        "Summary Agent",
        "Concise incident narrative. Produces a 2-4 sentence factual summary and current status label.",
        url,
        skills=[
            _skill("summary", "Incident Summary", "Summarize incidents into concise narratives.", ["incident", "summary", "sre"]),
        ],
    )


def _comms_card(url: str) -> AgentCard:
    return _make_card(
        "Comms Agent",
        "Audience-appropriate incident communications. Generates Slack updates and stakeholder summaries.",
        url,
        skills=[
            _skill("comms", "Incident Communications", "Generate Slack and stakeholder updates.", ["incident", "comms", "slack", "sre"]),
        ],
    )


def _pir_card(url: str) -> AgentCard:
    return _make_card(
        "PIR Agent",
        "Post-incident report generation. Timeline, customer impact, and prevention actions.",
        url,
        skills=[
            _skill("pir", "Post-Incident Report", "Construct post-incident reports.", ["incident", "pir", "postmortem", "sre"]),
        ],
    )


def _chat_card(url: str) -> AgentCard:
    return _make_card(
        "On-Call Chat Assistant",
        "Conversational SRE assistant for infrastructure, runbooks, and operational questions.",
        url,
        skills=[
            _skill("chat", "SRE Chat", "Answer operational and infrastructure questions.", ["sre", "chat", "infrastructure", "azure", "gcp"]),
        ],
    )


def _orchestrate_card(url: str) -> AgentCard:
    return _make_card(
        "On-Call Copilot Orchestrate",
        "Full incident analysis pipeline. Runs triage, summary, comms, and PIR concurrently.",
        url,
        skills=[
            _skill("orchestrate", "Incident Orchestration", "Orchestrate all specialist agents for comprehensive analysis.", ["incident", "orchestration", "multi-agent", "sre"]),
        ],
    )


# ---------------------------------------------------------------------------
# Route helpers
# ---------------------------------------------------------------------------

AgentRoute = tuple[str, str]  # (name, path_prefix)


def _add_agent_routes(
    app: Starlette,
    name: str,
    agent: Agent,
    card: AgentCard,
    path_prefix: str,
    stream: bool = True,
) -> None:
    """Create and add A2A routes for a single agent to the Starlette app.

    Args:
        app: The Starlette app to add routes to.
        name: Agent name (for logging).
        agent: The Agent Framework Agent instance.
        card: The A2A AgentCard for this agent.
        path_prefix: URL prefix for this agent's endpoints (e.g. '/a2a/triage').
        stream: Whether to enable streaming responses.
    """
    executor = A2AExecutor(agent, stream=stream)
    task_store = InMemoryTaskStore()
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
    )

    agent_app = A2AStarletteApplication(
        agent_card=card,
        http_handler=handler,
    )

    # Build routes with agent-specific paths
    rpc_url = f"{path_prefix}/"
    card_url = f"{path_prefix}/.well-known/agent-card.json"

    routes = agent_app.routes(rpc_url=rpc_url, agent_card_url=card_url)
    for route in routes:
        app.routes.append(route)

    logger.debug("A2A route added: %s -> %s (card: %s)", name, rpc_url, card_url)


# ---------------------------------------------------------------------------
# Build the Starlette app
# ---------------------------------------------------------------------------


def build_a2a_app(
    chat_client,
    specialists: tuple[Agent, Agent, Agent, Agent],
    tools: list,
    base_url: str = "http://localhost:8089",
) -> Starlette:
    """Build the unified A2A Starlette application with all agent endpoints.

    Args:
        chat_client: Shared chat client for all agents.
        specialists: Tuple of (triage, summary, comms, pir) Agent instances.
        tools: Shared tool list for all agents.
        base_url: Base URL used in AgentCard URLs.

    Returns:
        Configured Starlette app ready for uvicorn.
    """
    triage, summary, comms, pir = specialists

    # Chat agent
    from app.chat_agent import create_chat_agent

    chat_agent = create_chat_agent(chat_client)

    # Orchestrate agent (wraps ConcurrentBuilder as a single agent)
    orchestrate_agent = ConcurrentBuilder(
        participants=[triage, summary, comms, pir],
        intermediate_outputs=False,
    ).build().as_agent(
        name="oncall-copilot-orchestrate",
        description="Runs triage, summary, comms, and PIR agents for incident response.",
    )

    # Create Starlette app
    app = Starlette()

    # Health check
    async def health(request):
        return JSONResponse({
            "status": "ok",
            "protocol": "a2a",
            "agents": ["triage", "summary", "comms", "pir", "chat", "orchestrate"],
        })

    app.routes.append(Route("/health", health, methods=["GET"]))

    # Register agents
    agents_config = [
        ("orchestrate", orchestrate_agent, _orchestrate_card(base_url), ""),
        ("triage", triage, _triage_card(base_url), "/a2a/triage"),
        ("summary", summary, _summary_card(base_url), "/a2a/summary"),
        ("comms", comms, _comms_card(base_url), "/a2a/comms"),
        ("pir", pir, _pir_card(base_url), "/a2a/pir"),
        ("chat", chat_agent, _chat_card(base_url), "/a2a/chat"),
    ]

    for name, agent, card, path_prefix in agents_config:
        _add_agent_routes(app, name, agent, card, path_prefix, stream=True)

    return app


# ---------------------------------------------------------------------------
# Server runner
# ---------------------------------------------------------------------------


def run_a2a_server(
    chat_client,
    specialists: tuple[Agent, Agent, Agent, Agent],
    tools: list,
    port: int = 8089,
) -> None:
    """Start the A2A protocol server on a background daemon thread.

    Args:
        chat_client: Shared chat client for all agents.
        specialists: Tuple of (triage, summary, comms, pir) Agent instances.
        tools: Shared tool list for all agents.
        port: Port to listen on.
    """
    base_url = os.environ.get("A2A_BASE_URL", f"http://localhost:{port}")
    app = build_a2a_app(chat_client, specialists, tools, base_url=base_url)

    def _serve():
        print(f"[a2a-server] A2A protocol listening on port {port}", flush=True)
        print(f"[a2a-server] Agent card : {base_url}/.well-known/agent-card.json", flush=True)
        print(f"[a2a-server] Triage     : {base_url}/a2a/triage/", flush=True)
        print(f"[a2a-server] Summary    : {base_url}/a2a/summary/", flush=True)
        print(f"[a2a-server] Comms      : {base_url}/a2a/comms/", flush=True)
        print(f"[a2a-server] PIR        : {base_url}/a2a/pir/", flush=True)
        print(f"[a2a-server] Chat       : {base_url}/a2a/chat/", flush=True)
        print(f"[a2a-server] Orchestrate: {base_url}/", flush=True)
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

    thread = threading.Thread(target=_serve, daemon=True, name="a2a-server")
    thread.start()
