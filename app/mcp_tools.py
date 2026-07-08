from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def create_mcp_tools() -> list[Any]:
    """Create MCPStdioTool instances from the MCP_SERVERS env var."""
    try:
        from agent_framework import MCPStdioTool  # noqa:PLC0415
    except ImportError:
        logger.warning("MCPStdioTool not available - install mcp package")
        return []

    raw = os.environ.get("MCP_SERVERS")
    if not raw:
        return []

    try:
        servers = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("MCP_SERVERS contains invalid JSON: %s", exc)
        return []

    if not isinstance(servers, list):
        logger.warning("MCP_SERVERS is not a JSON array - ignoring")
        return []

    tools: list[Any] = []
    for i, _server in enumerate(servers):
        name = "mcp-server-%d" % i
        command = "npx"
        args: list[str] = []
        env = {}

        if _server.get("name"):
            name = _server.get("name")
        if _server.get("command"):
            command = _server.get("command")
        if _server.get("args"):
            args = _server.get("args")
        if _server.get("env"):
            env = _server.get("env")

        try:
            tool = MCPStdioTool(
                name=name,
                command=command,
                args=args,
                env=env,
                load_tools=_server.get("load_tools", True),
                load_prompts=_server.get("load_prompts", False),
            )
            tools.append(tool)
            logger.info("Created MCPStdioTool '%s'", name)
        except ImportError:
            logger.warning("MCPStdioTool not available - install mcp package")
            return []
        except Exception as exc:  # noqa:BLE001
            logger.warning("Failed to create MCPStdioTool '%s': %s", name, exc)

    return tools
