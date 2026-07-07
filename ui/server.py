#!/usr/bin/env python3
"""
On-Call Copilot local UI server.

Usage:
    set AZURE_AI_PROJECT_ENDPOINT=https://...
    .venv\\Scripts\\python.exe ui\\server.py

Opens at http://localhost:7860
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

ROOT = Path(__file__).resolve().parent.parent
DEMOS_DIR     = ROOT / "scripts" / "demos"
SCENARIOS_DIR = ROOT / "scripts" / "scenarios"
HTML_FILE     = Path(__file__).resolve().parent / "index.html"
PORT          = int(os.environ.get("UI_PORT", "7860"))

DEMO_LABELS = {
    "demo_1_simple_alert.json":          "Demo 1 — API Gateway 5xx (SEV3)",
    "demo_2_multi_signal.json":          "Demo 2 — DB Connection Pool (SEV1)",
    "demo_3_post_incident.json":         "Demo 3 — Auth TLS Cert Expiry",
}
SCENARIO_LABELS = {
    "scenario_1_redis_outage.json":      "Scenario 1 — Redis Cluster Down (SEV2)",
    "scenario_2_aks_scaling.json":       "Scenario 2 — AKS Scaling Failure",
    "scenario_3_dns_cascade.json":       "Scenario 3 — DNS Cascade Failure",
    "scenario_4_minimal_alert.json":     "Scenario 4 — Minimal Alert",
    "scenario_5_storage_throttle_pir.json": "Scenario 5 — Storage Throttle PIR",
}


# ─── config ────────────────────────────────────────────────────────────────────

LOCAL_SERVER_URL = os.environ.get("LOCAL_SERVER_URL", "http://localhost:8088")

# When MOCK_MODE=true or LOCAL_MODE=true, route to the local agent server
# at LOCAL_SERVER_URL instead of the Foundry API endpoint.
LOCAL_MODE = os.environ.get("LOCAL_MODE", "").lower() in ("true", "1", "yes") or \
             os.environ.get("MOCK_MODE", "").lower() in ("true", "1", "yes")

# ─── auth (skipped in mock/local mode) ──────────────────────────────────────────

_credential = None


def _get_credential():
    """Lazy-init the Azure credential. Skips entirely in local/mock mode."""
    global _credential
    if LOCAL_MODE:
        return None
    if _credential is not None:
        return _credential
    from azure.identity import AzureCliCredential, InteractiveBrowserCredential
    try:
        print("  Trying Azure CLI credential (az login)...", flush=True)
        cred = AzureCliCredential()
        cred.get_token("https://ai.azure.com/.default")
        print("  Signed in via Azure CLI.", flush=True)
        _credential = cred
        return _credential
    except Exception:
        pass
    print("  Opening browser for Azure sign-in (one-time)...", flush=True)
    cred = InteractiveBrowserCredential()
    cred.get_token("https://ai.azure.com/.default")
    print("  Signed in successfully.", flush=True)
    _credential = cred
    return _credential


# ─── helpers ──────────────────────────────────────────────────────────────────

def _get_auth_headers() -> dict:
    """Return bearer token headers using the cached credential."""
    cred = _get_credential()
    if cred is None:
        return {"Content-Type": "application/json"}
    token = cred.get_token("https://ai.azure.com/.default").token
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-ms-protocol-version": "1.0.0",
        "x-ms-agent-protocol-version": "1.0.0",
    }


def _invoke_agent(content: str) -> dict:
    t0 = time.time()

    # Prefix raw JSON with an instruction so the Responses API doesn't reject
    # minimal payloads with "ID cannot be null or empty".
    user_message = content
    try:
        json.loads(content)
        user_message = (
            "Please analyze the following incident data and provide "
            "triage, summary, communications, and a post-incident "
            "report:\n\n" + content
        )
    except (json.JSONDecodeError, ValueError):
        pass

    body = {
        "input": [{"role": "user", "content": user_message}],
    }

    if LOCAL_MODE:
        # ── Local path: call the local Agent Framework server ──
        r = requests.post(
            f"{LOCAL_SERVER_URL}/responses",
            json=body, timeout=180,
        )
        elapsed = round(time.time() - t0, 1)
        if r.status_code != 200:
            raise RuntimeError(
                f"Local agent error ({r.status_code}): {r.text[:500]}"
            )
        raw = r.json()
        agent_label = f"local:{LOCAL_SERVER_URL}"
    else:
        # ── Live path: call Foundry Responses API ──
        endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "").rstrip("/")
        if not endpoint:
            raise ValueError(
                "AZURE_AI_PROJECT_ENDPOINT env var is not set.\n"
                "Set it to: https://<account>.services.ai.azure.com/api/projects/<project>\n"
                "Or set LOCAL_MODE=true / MOCK_MODE=true to use the local server."
            )
        agent_name    = os.environ.get("AGENT_NAME", "oncall-copilot")
        agent_version = os.environ.get("AGENT_VERSION", "")
        agent_path = urllib.parse.quote(agent_name, safe="")
        agent_label = f"{agent_name}:{agent_version or 'latest'}"

        headers = _get_auth_headers()
        r = requests.post(
            f"{endpoint}/agents/{agent_path}/endpoint/protocols/openai/responses?api-version=2025-11-15-preview",
            headers=headers, json=body, timeout=180,
        )
        elapsed = round(time.time() - t0, 1)

        raw = r.json()
        if raw.get("error"):
            raise RuntimeError(
                f"Agent error ({r.status_code}): {raw['error'].get('message', json.dumps(raw['error']))}"
            )

    # Extract & merge all JSON objects from the concatenated text blob
    merged: dict = {}
    decoder = json.JSONDecoder()
    for output in raw.get("output", []):
        for c in output.get("content", []):
            text = c.get("text", "").strip()
            text = re.sub(r'```(?:json)?\s*', '', text)
            pos  = 0
            while pos < len(text):
                while pos < len(text) and text[pos] in " \t\n\r":
                    pos += 1
                if pos >= len(text):
                    break
                try:
                    obj, end = decoder.raw_decode(text, pos)
                    if isinstance(obj, dict):
                        merged.update(obj)
                    pos = end
                except json.JSONDecodeError:
                    pos += 1

    return {
        "http_status": r.status_code,
        "agent_status": raw.get("status"),
        "elapsed_seconds": elapsed,
        "agent": agent_label,
        "output": merged,
    }


# ─── request handler ──────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter logs
        if args and str(args[1]) not in ("200", "304"):
            super().log_message(fmt, *args)

    # helpers
    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    # routing
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path.rstrip("/") or "/"
        qs     = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._send_html(HTML_FILE.read_bytes())
            return

        if path == "/api/config":
            endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
            self._send_json({
                "endpoint": endpoint,
                "agent_name": os.environ.get("AGENT_NAME", "oncall-copilot"),
                "agent_version": os.environ.get("AGENT_VERSION", "latest"),
                "configured": bool(endpoint),
            })
            return

        if path == "/api/incidents":
            items = []
            for f in sorted(DEMOS_DIR.glob("demo_*.json")):
                items.append({
                    "type": "demo",
                    "file": f.name,
                    "label": DEMO_LABELS.get(f.name, f.stem),
                    "severity": json.loads(f.read_text())["severity"],
                })
            for f in sorted(SCENARIOS_DIR.glob("scenario_*.json")):
                items.append({
                    "type": "scenario",
                    "file": f.name,
                    "label": SCENARIO_LABELS.get(f.name, f.stem),
                    "severity": json.loads(f.read_text())["severity"],
                })
            self._send_json({"incidents": items})
            return

        if path == "/api/load":
            ftype = qs.get("type", ["demo"])[0]
            fname = qs.get("file", [""])[0]
            base_dir = DEMOS_DIR if ftype == "demo" else SCENARIOS_DIR
            fpath = base_dir / fname
            if not fpath.exists() or not fname:
                self._send_json({"error": f"File not found: {fname}"}, 404)
                return
            data = json.loads(fpath.read_text())
            self._send_json({"content": json.dumps(data, indent=2), "meta": data})
            return

        self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        if self.path == "/api/invoke":
            try:
                body_bytes = self._read_body()
                payload    = json.loads(body_bytes)
                content    = payload.get("content", "")
                if not content.strip():
                    self._send_json({"error": "content is required"}, 400)
                    return
                result = _invoke_agent(content)
                self._send_json(result)
            except requests.Timeout:
                self._send_json({"error": "Request timed out (180s). The agent may be busy."}, 504)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        self._send_json({"error": "Not found"}, 404)


# ─── entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not HTML_FILE.exists():
        print(f"ERROR: {HTML_FILE} not found.")
        sys.exit(1)

    endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
    agent    = os.environ.get("AGENT_NAME", "oncall-copilot")
    version  = os.environ.get("AGENT_VERSION", "") or "latest"

    print("=" * 60)
    print("  On-Call Copilot UI")
    print("=" * 60)
    print(f"  URL     : http://localhost:{PORT}")
    if LOCAL_SERVER_URL:
        print(f"  Mode    : Local (→ {LOCAL_SERVER_URL})")
    else:
        print(f"  Agent   : {agent}:{version}")
        if endpoint:
            print(f"  Endpoint: {endpoint[:60]}...")
        else:
            print("  Endpoint: ⚠  AZURE_AI_PROJECT_ENDPOINT not set")
    print("=" * 60)
    print("  Press Ctrl+C to stop.")
    print()

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    server = ThreadedHTTPServer(("localhost", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
