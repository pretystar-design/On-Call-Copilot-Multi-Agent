"""
MCP (Model Context Protocol) tool definitions for On-Call Copilot.

Parses the ``MCP_SERVERS`` JSON environment variable and creates
``MCPStdioTool`` instances — one per server definition. Each server
runs as a local subprocess (via ``npx``) and communicates over stdio.

Expected ``MCP_SERVERS`` format (JSON array)::

    MCP_SERVERS=[
      {
        "name": "ado",
        "command": "npx",
        "args": ["-y", "@azure-devops/mcp", "<org>", "--authentication", "envvar"],
        "env": { "ADO_MCP_AUTH_TOKEN": "<pat>" },
        "load_tools": true,
        "load_prompts": false
      },
      {
        "name": "elastic-agent-builder",
        "command": "npx",
        "args": ["mcp-remote", "<url>", "--header", "Authorization:ApiKey <key>"]
      }
    ]

Optional fields per server:

- ``load_tools`` (bool): Whether to load the server's tool list after connecting.
  Defaults to ``true``.
- ``load_prompts`` (bool): Whether to load the server's prompt list after
  connecting. Defaults to ``false`` since most MCP servers do not expose
  prompts and ``prompts/list`` raises ``McpError`` on servers that lack the
  capability.

Returns an empty list when ``MCP_SERVERS`` is unset, empty, or invalid,
so agents degrade gracefully without MCP context.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def create_mcp_tools() -> list[Any]:
    """Create ``MCPStdioTool`` instances from the ``MCP_SERVERS`` env var.

    Returns a (possibly empty) list of ``MCPStdioTool`` instances, one
    per entry in the JSON array. Each tool will spawn its command as a
    local subprocess when invoked by an agent.

    Returns an empty list if:
    - ``MCP_SERVERS`` is not set
    - ``MCP_SERVERS`` is empty
    - ``MCP_SERVERS`` contains invalid JSON
    - Any server definition is missing required fields (name, command)
    """
    raw = os.environ.get("MCP_SERVERS")
    if not raw:
        logger.debug("MCP_SERVERS not set — no MCP tools created")
        return []

    try:
        servers = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("MCP_SERVERS contains invalid JSON: %s", exc)
        return []

    if not isinstance(servers, list):
        logger.warning("MCP_SERVERS is not a JSON array — ignoring")
        return []

    tools: list[Any] = []
    for i, server in enumerate(servers):
        if not isinstance(server, dict):
            logger.warning("MCP_SERVERS[%d] is not a JSON object — skipping", i)
            continue

        name = server.get("name", f"mcp-server-{i}")
        command = server.get("command")
        args = server.get("args", [])
        env = server.get("env")
        load_tools = server.get("load_tools", True)
        load_prompts = server.get("load_prompts", False)

        if not command:
            logger.warning(
                "MCP_SERVERS[%d] missing 'command' — skipping server '%s'",
                i,
                name,
            )
            continue

        try:
            from agent_framework import MCPStdioTool  # noqa: PLC0415

            tool = MCPStdioTool(
                name=name,
                command=command,
                args=args,
                env=env,
                load_tools=load_tools,
                load_prompts=load_prompts,
            )
            tools.append(tool)
            logger.info(
                "Created MCPStdioTool '%s' (%s %s) [load_tools=%s, load_prompts=%s]",
                name,
                command,
                " ".join(args),
                load_tools,
                load_prompts,
            )
        except ImportError:
            logger.warning(
                "MCPStdioTool not available — install mcp package"
            )
            return []
        except Exception as exc:
            logger.warning("Failed to create MCPStdioTool '%s': %s", name, exc)
            continue

    return tools
