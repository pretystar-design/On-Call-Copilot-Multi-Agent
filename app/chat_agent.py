from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from app.agents.chat import CHAT_INSTRUCTIONS
from app.config import get_azure_credential
from app.infra_topology import tool_definitions as topology_tools
from app.rag import create_rag_tools

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)


def _get_chat_client():
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-03-01-preview")
    model = os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME", "model-router")
    endpoint = os.environ.get("AZURE_MODEL_PROJECT_ENDPOINT") or os.environ.get(
        "AZURE_AI_PROJECT_ENDPOINT"
    )

    if endpoint:
        from agent_framework_foundry import FoundryChatClient

        return FoundryChatClient(
            project_endpoint=endpoint,
            model=model,
            credential=get_azure_credential(),
        )

    if not (azure_endpoint and api_key):
        raise ValueError(
            "Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY, "
            "or AZURE_AI_PROJECT_ENDPOINT/AZURE_MODEL_PROJECT_ENDPOINT"
        )

    from agent_framework_openai import OpenAIChatClient

    return OpenAIChatClient(
        model=model,
        azure_endpoint=azure_endpoint,
        api_key=api_key,
        api_version=api_version,
    )


def _build_chat_tools() -> list:
    rag_tools = create_rag_tools()
    return [*topology_tools.TOPOLOGY_TOOLS, *rag_tools]


async def run_chat_agent(messages: list[dict]) -> str:
    from agent_framework import Message

    converted_messages = [
        Message(
            role=message.get("role", "user"),
            contents=message.get("content", ""),
        )
        for message in messages
    ]

    client = _get_chat_client()
    tools = _build_chat_tools()

    from agent_framework import Agent

    agent = Agent(
        client=client,
        instructions=CHAT_INSTRUCTIONS,
        name="chat-agent",
        tools=tools,
    )

    result = await agent.run(messages=converted_messages)
    if hasattr(result, "output"):
        return str(result.output)
    return str(result)
