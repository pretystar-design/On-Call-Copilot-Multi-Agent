"""
Remote A2A agent tool integration.

Loads remote A2A agent configurations from the ``A2A_REMOTE_AGENTS``
environment variable and exposes each as a callable ``FunctionTool``
that specialist agents (triage, summary, comms, PIR) and the chat
agent can invoke to delegate sub-tasks to specialised external agents.

Supports both standard A2A endpoints and platform-specific paths
(e.g. Elastic Agent Builder uses ``{baseUrl}.json`` for AgentCards).

Usage::

    from app.a2a_remote import create_remote_a2a_tools
    tools = create_remote_a2a_tools()
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent_framework._tools import FunctionTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class A2ARemoteAgentConfig(BaseModel):
    """Configuration for a single remote A2A agent.

    Parsed from the ``A2A_REMOTE_AGENTS`` JSON array.
    """

    name: str = Field(description="Agent identifier (kebab-case). Used as call_<name>_agent.")
    url: str = Field(description="Base URL of the remote A2A agent endpoint.")
    description: str = Field(description="Purpose of the remote agent, shown in the tool description.")
    auth_token: str | None = Field(
        default=None,
        description="Optional bearer token for authenticated endpoints.",
    )
    timeout_seconds: int = Field(
        default=60,
        description="HTTP timeout in seconds for calls to this agent.",
        ge=1,
        le=600,
    )
    agent_card_url: str | None = Field(
        default=None,
        description=(
            "Custom AgentCard URL if the agent uses a non-standard path "
            "(e.g. Elastic Agent Builder: ``{baseUrl}.json``). "
            "When set, the card is resolved from this URL before calling the agent."
        ),
    )
    agent_card_auth_token: str | None = Field(
        default=None,
        description=(
            "Optional separate auth token for AgentCard resolution. "
            "Falls back to ``auth_token`` if not set."
        ),
    )
    auth_scheme: str = Field(
        default="bearer",
        description=(
            "Authorization scheme: ``\"bearer\"`` (default, sends ``Authorization: Bearer <token>``) "
            "or ``\"apikey\"`` (sends ``Authorization: ApiKey <token>`` for Elastic agents)."
        ),
    )


class RemoteA2AAgentInput(BaseModel):
    """Input to send to the remote A2A agent."""

    message: str = Field(
        description="The message or query to send to the remote agent.",
    )


# ---------------------------------------------------------------------------
# Auth interceptor
# ---------------------------------------------------------------------------


def _build_auth_interceptor(token: str, scheme: str = "bearer") -> Any:
    """Build an auth interceptor for A2A HTTP requests.

    Args:
        token: The raw auth token string.
        scheme: ``"bearer"`` (``Authorization: Bearer <token>``) or
            ``"apikey"`` (``Authorization: ApiKey <token>``).

    Returns:
        An ``AuthInterceptor`` instance.
    """
    from a2a.client.auth.interceptor import AuthInterceptor

    if scheme == "apikey":
        prefix = "ApiKey"
    else:
        prefix = "Bearer"

    class _Auth(AuthInterceptor):
        def __init__(self, token: str):
            self.token = token

        async def intercept(  # pyright: ignore[reportIncompatibleMethodOverride]
            self,
            method_name: str,
            request_payload: dict[str, Any],
            http_kwargs: dict[str, Any],
            agent_card: Any | None = None,
            context: Any | None = None,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            headers = http_kwargs.get("headers", {})
            headers["Authorization"] = f"{prefix} {self.token}"
            http_kwargs["headers"] = headers
            return request_payload, http_kwargs

    return _Auth(token)


# ---------------------------------------------------------------------------
# AgentCard resolver
# ---------------------------------------------------------------------------


async def _resolve_agent_card(
    card_url: str,
    auth_token: str | None = None,
    auth_scheme: str = "bearer",
) -> Any:
    """Resolve an A2A AgentCard from a custom URL (non-standard paths).

    Args:
        card_url: Full URL to the AgentCard JSON document.
        auth_token: Optional bearer/api-key token.
        auth_scheme: ``"bearer"`` or ``"apikey"``.

    Returns:
        An ``AgentCard`` instance.
    """
    import httpx
    from a2a.types import AgentCard

    headers = {}
    if auth_token:
        prefix = "ApiKey" if auth_scheme == "apikey" else "Bearer"
        headers["Authorization"] = f"{prefix} {auth_token}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(card_url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    return AgentCard(**data)


# ---------------------------------------------------------------------------
# Handler factory
# ---------------------------------------------------------------------------


def _build_remote_agent_handler(config: A2ARemoteAgentConfig) -> Any:
    """Create an async handler for a single remote A2A agent.

    The returned function is wrapped as a ``FunctionTool``. Each invocation
    creates a transient ``A2AAgent`` connection, sends the message, and
    returns the text response.

    Args:
        config: Configuration for the remote agent.

    Returns:
        An async callable ``(message: str) -> str``.
    """
    agent_name = config.name
    agent_url = config.url.rstrip("/")
    timeout = float(config.timeout_seconds)
    card_url = config.agent_card_url

    # Build auth interceptor(s)
    scheme = config.auth_scheme
    auth = _build_auth_interceptor(config.auth_token, scheme) if config.auth_token else None
    card_token = config.agent_card_auth_token or config.auth_token

    async def _handler(message: str) -> str:
        """Send a message to the remote A2A agent and return its response."""
        from agent_framework.a2a import A2AAgent

        logger.info(
            "Remote A2A agent '%s': sending message (%d chars)",
            agent_name,
            len(message),
        )
        try:
            a2a_kwargs: dict[str, Any] = {
                "name": agent_name,
                "url": agent_url,
                "auth_interceptor": auth,
                "timeout": timeout,
            }

            # Resolve a non-standard AgentCard URL if configured
            if card_url:
                resolved_card = await _resolve_agent_card(
                    card_url,
                    auth_token=card_token,
                    auth_scheme=scheme,
                )
                a2a_kwargs["agent_card"] = resolved_card

            async with A2AAgent(**a2a_kwargs) as a2a_agent:
                response = await a2a_agent.run(message)

            text = getattr(response, "text", str(response)) or ""
            if not text.strip():
                logger.info(
                    "Remote A2A agent '%s': returned empty response",
                    agent_name,
                )
                return f"Remote agent '{agent_name}' returned no response."

            logger.info(
                "Remote A2A agent '%s': response received (%d chars)",
                agent_name,
                len(text),
            )
            return text

        except Exception as exc:
            logger.warning(
                "Remote A2A agent '%s' call failed: %s",
                agent_name,
                exc,
            )
            return f"Error: Remote agent '{agent_name}' unreachable ({exc})."

    return _handler


# ---------------------------------------------------------------------------
# Config parser
# ---------------------------------------------------------------------------


def _parse_agent_configs() -> list[A2ARemoteAgentConfig]:
    """Parse the ``A2A_REMOTE_AGENTS`` env var into a list of configs.

    Returns:
        A list of validated agent configs. Empty if the env var is unset,
        empty, or contains only invalid entries.
    """
    import os

    raw = os.environ.get("A2A_REMOTE_AGENTS")
    if not raw:
        return []

    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("A2A_REMOTE_AGENTS: invalid JSON — %s", exc)
        return []

    if not isinstance(entries, list):
        logger.warning(
            "A2A_REMOTE_AGENTS: expected a JSON array, got %s",
            type(entries).__name__,
        )
        return []

    configs: list[A2ARemoteAgentConfig] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            logger.warning(
                "A2A_REMOTE_AGENTS entry %d: expected object, got %s",
                i,
                type(entry).__name__,
            )
            continue
        name = entry.get("name")
        url = entry.get("url")
        if not name or not url:
            logger.warning(
                "A2A_REMOTE_AGENTS entry %d: missing required 'name' or 'url' — skipping",
                i,
            )
            continue
        try:
            configs.append(A2ARemoteAgentConfig(**entry))
        except Exception as exc:
            logger.warning(
                "A2A_REMOTE_AGENTS entry %d ('%s'): invalid config — %s",
                i,
                name,
                exc,
            )

    if configs:
        logger.info(
            "A2A_REMOTE_AGENTS: %d remote agent(s) configured: %s",
            len(configs),
            ", ".join(c.name for c in configs),
        )
    else:
        logger.info("A2A_REMOTE_AGENTS: no valid remote agents configured")

    return configs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_remote_a2a_tools() -> list[FunctionTool]:
    """Create function tools for each configured remote A2A agent.

    Returns an empty list when ``A2A_REMOTE_AGENTS`` is unset, empty,
    or contains only invalid entries.

    Returns:
        A list of ``FunctionTool`` instances, one per remote agent.
    """
    configs = _parse_agent_configs()
    tools: list[FunctionTool] = []

    for config in configs:
        tool_name = f"call_{config.name.replace('-', '_')}_agent"
        handler = _build_remote_agent_handler(config)

        tool = FunctionTool(
            name=tool_name,
            description=(
                f"Call the remote '{config.name}' A2A agent. "
                f"{config.description} "
                f"Provide a message describing what you want the remote agent to do."
            ),
            func=handler,
            input_model=RemoteA2AAgentInput,
        )
        tools.append(tool)

    return tools
