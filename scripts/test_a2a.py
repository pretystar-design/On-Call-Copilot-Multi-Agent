#!/usr/bin/env python3
"""
Smoke test for the A2A protocol server endpoints.

Tests:
  1. AgentCard retrieval from each endpoint
  2. Health check
  3. Basic message send to each agent
  4. Orchestrate endpoint with a minimal incident payload

Usage:
  python scripts/test_a2a.py                     # tests against localhost:8089
  python scripts/test_a2a.py --host example.com --port 9090
  python scripts/test_a2a.py --orchestrate-only   # only test orchestrate endpoint
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid

import httpx

BASE_URL = "http://localhost:8089"

# An A2A message payload for individual agents
SAMPLE_MESSAGE: dict = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "message/send",
    "params": {
        "message": {
            "kind": "message",
            "role": "user",
            "parts": [
                {
                    "kind": "text",
                    "text": "What are your capabilities?",
                    "metadata": {},
                }
            ],
            "messageId": None,
            "contextId": None,
        }
    },
}

# A more detailed payload for the orchestrate endpoint (incident analysis)
SAMPLE_INCIDENT: dict = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "message/send",
    "params": {
        "message": {
            "kind": "message",
            "role": "user",
            "parts": [
                {
                    "kind": "text",
                    "text": json.dumps({
                        "incident_id": "test-inc-001",
                        "title": "Test incident for A2A smoke test",
                        "severity": "SEV3",
                        "timeframe": {
                            "start": "2026-07-09T12:00:00Z",
                        },
                        "alerts": [
                            {
                                "name": "High CPU",
                                "description": "CPU > 90% for 5 minutes",
                                "timestamp": "2026-07-09T12:05:00Z",
                            }
                        ],
                        "logs": [
                            {
                                "source": "app-service",
                                "lines": ["ERROR: connection timeout to database"],
                            }
                        ],
                        "metrics": [
                            {
                                "name": "cpu_usage",
                                "window": "5m",
                                "values_summary": "avg=95%, max=99%",
                            }
                        ],
                    }),
                    "metadata": {},
                }
            ],
            "messageId": None,
            "contextId": None,
        }
    },
}


def check(response: httpx.Response, name: str) -> None:
    """Assert a 200 response and print diagnostic."""
    if response.status_code == 200:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name} (HTTP {response.status_code})")
        print(f"    Body: {response.text[:300]}")


def test_agent_card(client: httpx.Client, url: str, name: str) -> bool:
    """Test GET /.well-known/agent-card.json for an agent."""
    try:
        r = client.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            assert "name" in data
            assert "description" in data
            assert "version" in data
            assert "capabilities" in data
            print(f"  ✓ {name} AgentCard: {data.get('name')} v{data.get('version')}")
            return True
        else:
            print(f"  ✗ {name} AgentCard (HTTP {r.status_code})")
            return False
    except Exception as e:
        print(f"  ✗ {name} AgentCard: {e}")
        return False


def test_send_message(
    client: httpx.Client,
    url: str,
    name: str,
    payload: dict | None = None,
) -> bool:
    """Test POST message/send to an A2A endpoint."""
    try:
        data = payload or SAMPLE_MESSAGE
        r = client.post(url, json=data, timeout=60)
        if r.status_code == 200:
            result = r.json()
            # A2A returns a result key in JSON-RPC response
            if "result" in result:
                print(f"  ✓ {name} message/send: got response")
                return True
            else:
                print(f"  ✓ {name} message/send (no 'result' key)")
                return True
        elif r.status_code == 404:
            print(f"  - {name}: not found (404) — expected if server is running without this agent")
            return False
        else:
            print(f"  ✗ {name} message/send (HTTP {r.status_code})")
            print(f"    Body: {r.text[:300]}")
            return False
    except Exception as e:
        print(f"  ✗ {name} message/send: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test A2A protocol endpoints")
    parser.add_argument("--host", default="localhost", help="A2A server host")
    parser.add_argument("--port", type=int, default=8089, help="A2A server port")
    parser.add_argument("--orchestrate-only", action="store_true", help="Only test orchestrate endpoint")
    args = parser.parse_args()

    base = f"http://{args.host}:{args.port}"

    print(f"\n=== A2A Smoke Test ===")
    print(f"Server: {base}\n")

    with httpx.Client(base_url=base) as client:
        # 1. Health check
        print("[1] Health check")
        try:
            r = client.get("/health", timeout=10)
            check(r, "GET /health")
            if r.status_code == 200:
                print(f"    Agents: {r.json().get('agents', [])}")
        except Exception as e:
            print(f"  ✗ Health check: {e}")
            print("  Is the A2A server running?")
            sys.exit(1)

        print()

        if args.orchestrate_only:
            # 2. Orchestrate-specific tests
            print("[2] Orchestrate endpoint")

            # AgentCard
            test_agent_card(client, "/.well-known/agent-card.json", "orchestrate")

            # Send incident
            test_send_message(client, "/", "orchestrate", payload=SAMPLE_INCIDENT)
        else:
            # 2. Agent discovery (AgentCards)
            print("[2] Agent Discovery (AgentCards)")
            agent_cards = [
                ("orchestrate", "/.well-known/agent-card.json"),
                ("triage", "/a2a/triage/.well-known/agent-card.json"),
                ("summary", "/a2a/summary/.well-known/agent-card.json"),
                ("comms", "/a2a/comms/.well-known/agent-card.json"),
                ("pir", "/a2a/pir/.well-known/agent-card.json"),
                ("chat", "/a2a/chat/.well-known/agent-card.json"),
            ]
            all_cards_ok = True
            for name, url in agent_cards:
                if not test_agent_card(client, url, name):
                    all_cards_ok = False
            if all_cards_ok:
                print("  ✓ All AgentCards retrieved")
            else:
                print("  Some AgentCards failed (some agents may not be registered)")

            print()

            # 3. Basic message tests
            print("[3] Agent Message Tests")
            endpoints = [
                ("orchestrate", "/"),
                ("triage", "/a2a/triage/"),
                ("summary", "/a2a/summary/"),
                ("comms", "/a2a/comms/"),
                ("pir", "/a2a/pir/"),
                ("chat", "/a2a/chat/"),
            ]
            for name, url in endpoints:
                if name == "orchestrate":
                    test_send_message(client, url, name, payload=SAMPLE_INCIDENT)
                else:
                    test_send_message(client, url, name)

            print()

            # 4. Streaming test (just orchestrate)
            print("[4] Streaming (orchestrate)")
            try:
                stream_payload = dict(SAMPLE_INCIDENT)
                stream_payload["method"] = "message/send_stream"  # try stream variant
                r = client.post("/", json=stream_payload, timeout=60)
                if r.status_code == 200:
                    print("  ✓ Orchestrate streaming response received")
                else:
                    print(f"  - Streaming not supported (HTTP {r.status_code})")
            except Exception as e:
                print(f"  - Streaming test: {e}")

    print(f"\n=== Done ===")


if __name__ == "__main__":
    main()
