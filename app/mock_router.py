"""
Mock Model Router – returns deterministic golden responses for local validation.

When MOCK_MODE=true, main.py uses this instead of calling Azure.
Each scenario maps incident_id -> pre-built triage JSON that passes schema validation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("oncall-copilot.mock")

# Load all golden responses from scripts/golden_outputs/
_GOLDEN_DIR = Path(__file__).resolve().parent.parent / "scripts" / "golden_outputs"
_GOLDEN_RESPONSES: dict[str, dict] = {}


def _load_golden_responses() -> None:
    if _GOLDEN_RESPONSES:
        return
    if not _GOLDEN_DIR.exists():
        logger.warning("Golden output directory not found: %s", _GOLDEN_DIR)
        return
    for f in sorted(_GOLDEN_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            iid = data.get("_incident_id", f.stem)
            _GOLDEN_RESPONSES[iid] = data
            logger.info("Loaded golden response for %s from %s", iid, f.name)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", f.name, exc)


def get_mock_response(incident_id: str) -> dict | None:
    """Return a golden triage response for the given incident_id, or None."""
    _load_golden_responses()
    resp = _GOLDEN_RESPONSES.get(incident_id)
    if resp is None:
        # Fall back to the generic catch-all if present
        resp = _GOLDEN_RESPONSES.get("_default")
    if resp is not None:
        # Strip the internal _incident_id key before returning
        resp = {k: v for k, v in resp.items() if not k.startswith("_")}
    return resp


# ── Chat mock responses ──────────────────────────────────────────────────

_CHAT_MOCK_RESPONSES: dict[str, str] = {
    "redis": "To troubleshoot high Redis latency:\n\n1. Check `INFO CPU` — if `used_cpu_sys` is high, Redis is spending more time in kernel than userspace\n2. Check `INFO COMMANDSTATS` for slow commands (KEYS, SMEMBERS on large sets, etc.)\n3. Check `INFO MEMORY` for `maxmemory` pressure and eviction rates\n4. Check latency with `redis-cli --latency -h <host>` — anything above 1ms on local network warrants investigation\n5. Review slowlog: `SLOWLOG GET 10`\n\nWhat specific aspect would you like to investigate further?",
    "aks": "Here's how to investigate your AKS cluster:\n\n1. Check node pool status: `az aks nodepool show --resource-group <rg> --cluster-name <name> --name <pool>`\n2. Check pod states: `kubectl get pods --all-namespaces` — look for Pending, CrashLoopBackOff, ImagePullBackOff\n3. For CrashLoopBackOff pods: `kubectl logs <pod> --previous` to see the last crash reason\n4. Check cluster autoscaler events: `kubectl get events --all-namespaces | grep -i scale`\n5. Verify node conditions: `kubectl describe nodes | grep -A5 Conditions`\n\nWhat issue are you seeing in your cluster?",
    "topology": "I can help you explore your infrastructure topology. I have access to both Azure and GCP topology tools.\n\nTo get started, I can check:\n- Azure resource groups, virtual networks, and AKS clusters\n- GCP projects, VPCs, and GKE clusters\n\nWould you like me to look up topology for a specific provider or region?",
    "default": "Hello! I'm the On-Call Copilot SRE assistant. I can help you with:\n\n- **Azure & GCP infrastructure**: Cluster topology, resource health, networking\n- **Kubernetes**: Pod troubleshooting, node issues, autoscaler configuration\n- **Monitoring & Alerts**: Debugging alerts, log analysis, runbooks\n- **SRE Best Practices**: Incident response, postmortems, reliability patterns\n\nWhat would you like help with today?",
}


def get_chat_mock_response(message: str) -> dict:
    """Return a deterministic mock chat response based on keyword matching.

    Maps keywords in the user message to pre-built replies. Falls back to
    a generic greeting if no keywords match.
    """
    msg_lower = message.lower()
    for keyword, reply in _CHAT_MOCK_RESPONSES.items():
        if keyword in msg_lower:
            return {"reply": reply, "tool_calls": []}
    return {"reply": _CHAT_MOCK_RESPONSES["default"], "tool_calls": []}


class MockModelResponse:
    """Mimics the shape of a chat completion response for the mock path."""

    def __init__(self, content: str, model: str = "mock-model-router"):
        self.content = content
        self.model = model
        self.usage = type("Usage", (), {
            "prompt_tokens": len(content) // 4,
            "completion_tokens": len(content) // 4,
        })()

    class _Choice:
        def __init__(self, content: str):
            self.message = type("Message", (), {"content": content})()

    @property
    def choices(self):
        return [self._Choice(self.content)]
