#!/usr/bin/env python3
"""
Tests for the A2A remote agent tool integration.

Tests configuration parsing, tool creation, and error handling
for the ``A2A_REMOTE_AGENTS`` environment variable.

Usage:
    python scripts/test_a2a_remote.py                        # unit tests only
    python scripts/test_a2a_remote.py --live                  # includes live endpoint test
    python scripts/test_a2a_remote.py --config '[...]'        # test with custom config
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

# Ensure the project root is on sys.path so 'app' module resolves
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def test_empty_env():
    """When A2A_REMOTE_AGENTS is unset, should return empty list."""
    os.environ.pop("A2A_REMOTE_AGENTS", None)
    from app.a2a_remote import create_remote_a2a_tools

    tools = create_remote_a2a_tools()
    assert len(tools) == 0, f"Expected 0 tools, got {len(tools)}"
    print("  ✓ Empty env returns 0 tools")


def test_malformed_json():
    """Invalid JSON should log a warning and return empty list."""
    os.environ["A2A_REMOTE_AGENTS"] = "not valid json {{{"
    from app.a2a_remote import create_remote_a2a_tools

    tools = create_remote_a2a_tools()
    assert len(tools) == 0, f"Expected 0 tools, got {len(tools)}"
    print("  ✓ Malformed JSON returns 0 tools")


def test_not_an_array():
    """Non-array JSON should return empty."""
    os.environ["A2A_REMOTE_AGENTS"] = '{"name": "test"}'
    from app.a2a_remote import create_remote_a2a_tools

    tools = create_remote_a2a_tools()
    assert len(tools) == 0, f"Expected 0 tools, got {len(tools)}"
    print("  ✓ Non-array JSON returns 0 tools")


def test_missing_required_fields():
    """Entries without name/url should be skipped."""
    os.environ["A2A_REMOTE_AGENTS"] = json.dumps([
        {"name": "good", "url": "http://good/a2a", "description": "Good agent"},
        {"url": "http://no-name/a2a", "description": "Missing name"},
        {"name": "no-url", "description": "Missing URL"},
        {"name": "ok-too", "url": "http://ok-too/a2a", "description": "Also good"},
    ])
    from app.a2a_remote import create_remote_a2a_tools

    tools = create_remote_a2a_tools()
    assert len(tools) == 2, f"Expected 2 tools, got {len(tools)}"
    print("  ✓ Entries with missing fields are skipped (2/4 valid)")


def test_single_agent():
    """Single valid agent creates one tool with expected name."""
    os.environ["A2A_REMOTE_AGENTS"] = json.dumps([
        {
            "name": "network-diag",
            "url": "https://net-diag.example.com/a2a",
            "description": "Network diagnostics tool",
        },
    ])
    from app.a2a_remote import create_remote_a2a_tools

    tools = create_remote_a2a_tools()
    assert len(tools) == 1, f"Expected 1 tool, got {len(tools)}"
    t = tools[0]
    assert t.name == "call_network_diag_agent", f"Unexpected name: {t.name}"
    assert "Network diagnostics tool" in t.description
    print(f"  ✓ Single agent -> tool name: {t.name}")


def test_multiple_agents():
    """Multiple agents create tools with correct names."""
    os.environ["A2A_REMOTE_AGENTS"] = json.dumps([
        {"name": "agent-one", "url": "http://one/a2a", "description": "First"},
        {"name": "agent-two", "url": "http://two/a2a", "description": "Second"},
        {"name": "agent-three", "url": "http://three/a2a", "description": "Third"},
    ])
    from app.a2a_remote import create_remote_a2a_tools

    tools = create_remote_a2a_tools()
    assert len(tools) == 3, f"Expected 3 tools, got {len(tools)}"
    names = [t.name for t in tools]
    assert "call_agent_one_agent" in names, f"Missing call_agent_one_agent in {names}"
    assert "call_agent_two_agent" in names, f"Missing call_agent_two_agent in {names}"
    assert "call_agent_three_agent" in names, f"Missing call_agent_three_agent in {names}"
    print("  ✓ Multiple agents create 3 tools with correct names")


def test_auth_config():
    """Auth token should be accepted in config."""
    os.environ["A2A_REMOTE_AGENTS"] = json.dumps([
        {
            "name": "secure-agent",
            "url": "https://secure/a2a",
            "description": "Secure agent",
            "auth_token": "my-secret-token",
            "timeout_seconds": 30,
        },
    ])
    from app.a2a_remote import create_remote_a2a_tools

    tools = create_remote_a2a_tools()
    assert len(tools) == 1, f"Expected 1 tool, got {len(tools)}"
    print("  ✓ Auth config accepted (secure-agent with timeout=30)")


def test_input_schema():
    """Tool should accept a 'message' string parameter."""
    os.environ["A2A_REMOTE_AGENTS"] = json.dumps([
        {"name": "test", "url": "http://test/a2a", "description": "Test"},
    ])
    from app.a2a_remote import create_remote_a2a_tools

    tools = create_remote_a2a_tools()
    t = tools[0]
    # Check that the tool has an input_model
    assert hasattr(t, "input_model"), "Tool has no input_model"
    model = t.input_model
    schema = model.model_json_schema()
    props = schema.get("properties", {})
    assert "message" in props, f"Expected 'message' property, got {list(props.keys())}"
    assert props["message"].get("type") == "string", "message should be string type"
    print("  ✓ Tool input schema has 'message: string'")


def test_default_timeout():
    """Default timeout is 60 when not specified."""
    from app.a2a_remote import A2ARemoteAgentConfig

    config = A2ARemoteAgentConfig(
        name="test",
        url="http://test/a2a",
        description="Test",
    )
    assert config.timeout_seconds == 60, f"Expected default 60, got {config.timeout_seconds}"
    print("  ✓ Default timeout is 60 seconds")


def test_error_handler_reachable():
    """The error handler should handle unreachable agents gracefully."""
    import asyncio

    os.environ["A2A_REMOTE_AGENTS"] = json.dumps([
        {
            "name": "unreachable",
            "url": "http://127.0.0.1:1/a2a",  # port 1 is almost certainly closed
            "description": "Unreachable test",
            "timeout_seconds": 2,
        },
    ])
    from app.a2a_remote import create_remote_a2a_tools

    tools = create_remote_a2a_tools()
    assert len(tools) == 1

    # Get the underlying function
    handler = tools[0].func
    result = asyncio.run(handler(message="Hello"))
    assert "Error" in result, f"Expected error message, got: {result[:100]}"
    assert "unreachable" in result.lower(), f"Expected 'unreachable' in: {result}"
    print(f"  ✓ Error handler returns graceful error: {result[:80]}...")


def main():
    print("\n=== A2A Remote Agent Tool Tests ===\n")
    
    tests = [
        ("empty env", test_empty_env),
        ("malformed JSON", test_malformed_json),
        ("not an array", test_not_an_array),
        ("missing required fields", test_missing_required_fields),
        ("single agent", test_single_agent),
        ("multiple agents", test_multiple_agents),
        ("auth config", test_auth_config),
        ("input schema", test_input_schema),
        ("default timeout", test_default_timeout),
        ("error handler", test_error_handler_reachable),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        print(f"[{name}]")
        try:
            test_fn()
            passed += 1
        except Exception:
            failed += 1
            traceback.print_exc()
        print()

    # Clean up
    os.environ.pop("A2A_REMOTE_AGENTS", None)

    total = passed + failed
    print(f"=== {total} tests: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
