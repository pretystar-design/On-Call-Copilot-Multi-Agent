"""
Unified Agent Skill Loader — domain expertise for On-Call Copilot agents.

Loads SKILL.md files from multiple skill catalogs (``elastic/agent-skills`` and
``microsoft/skills``) and injects them into agent instructions when the incident
payload contains relevant signals.

Supports the Agent Skills open standard (``agentskills.io``). Skills are
instruction-space knowledge (not tools) — they teach agents *how* to use
APIs and tools correctly.

Usage::

    from app.skill_loader import SkillLoader

    loader = SkillLoader()
    context = loader.build_incident_context(incident_payload)
    agent_instructions = base_instructions + context
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SkillCatalog — declarative catalog definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillCatalog:
    """A named catalog of agent skills with its directory structure and keyword map.

    Fields:
        name: Unique catalog identifier (e.g. ``"elastic"``, ``"microsoft"``).
        dir_env_var: Environment variable name for the catalog's skills directory
            (e.g. ``"SKILL_CATALOG__ELASTIC__DIR"``).
        fallback_path: Default path relative to project root (e.g. ``"skills/elastic/"``).
        structure: Directory layout type — ``"category-nested"`` for Elastic-style
            (``<cat>/<skill>/SKILL.md``), ``"flat"`` for flat (``<skill>/SKILL.md``).
        keyword_map: Keyword → skill_name mapping for signal detection.
        fallback_env_vars: Ordered list of legacy env var names to check as fallback
            (e.g. ``["ELASTIC_SKILLS_DIR"]``).
        inline_skills: Optional name → instruction block dict for skills without
            SKILL.md files.
        categories: Optional tuple of well-known category names for ``category-nested``
            catalogs, enabling a fast-path lookup before full scan.
    """

    name: str
    dir_env_var: str
    fallback_path: str
    structure: Literal["category-nested", "flat"]
    keyword_map: dict[str, str]
    fallback_env_vars: list[str] = field(default_factory=list)
    inline_skills: dict[str, str] | None = None
    categories: tuple[str, ...] | None = None


# ---------------------------------------------------------------------------
# Catalog registry — module-level singleton
# ---------------------------------------------------------------------------

_catalog_registry: dict[str, SkillCatalog] = {}


def register_catalog(catalog: SkillCatalog) -> None:
    """Register a skill catalog.

    Raises:
        ValueError: If a catalog with the same ``name`` is already registered.
    """
    if catalog.name in _catalog_registry:
        raise ValueError(f"Catalog '{catalog.name}' is already registered")
    _catalog_registry[catalog.name] = catalog


def get_catalog(name: str) -> SkillCatalog | None:
    """Retrieve a registered catalog by name, or ``None``."""
    return _catalog_registry.get(name)


def list_catalogs() -> list[SkillCatalog]:
    """Return all registered catalogs."""
    return list(_catalog_registry.values())


# ---------------------------------------------------------------------------
# Generic directory resolution — 3-tier lookup
# ---------------------------------------------------------------------------


def _resolve_catalog_dir(catalog: SkillCatalog) -> Path | None:
    """Resolve a catalog's skills directory path.

    Lookup order:
    1. ``SKILL_CATALOG__<NAME>__DIR`` (new generic env var)
    2. Each of ``catalog.fallback_env_vars`` in order
    3. ``catalog.fallback_path`` relative to project root

    Returns ``None`` if none of the above resolve to a value.
    """
    # Tier 1: new generic env var
    raw = os.environ.get(catalog.dir_env_var, "")
    if raw:
        return Path(raw).resolve()

    # Tier 2: legacy fallback env vars
    for var in catalog.fallback_env_vars:
        raw = os.environ.get(var, "")
        if raw:
            return Path(raw).resolve()

    # Tier 3: fallback path relative to project root
    if catalog.fallback_path:
        # Old _get_elastic_skills_dir used parent.parent; keep the same base
        return (Path(__file__).resolve().parent.parent / catalog.fallback_path).resolve()

    return None


# ---------------------------------------------------------------------------
# Generic path resolver
# ---------------------------------------------------------------------------


def _resolve_skill_path(catalog: SkillCatalog, skill_name: str) -> Path | None:
    """Resolve a skill name to its ``SKILL.md`` file path for the given catalog.

    Handles two directory structures:
    - ``category-nested``: ``<skills_dir>/skills/<category>/<skill_name>/SKILL.md``
    - ``flat``: ``<skills_dir>/<skill_name>/SKILL.md``
    """
    skills_dir = _resolve_catalog_dir(catalog)
    if skills_dir is None or not skills_dir.is_dir():
        return None

    if catalog.structure == "category-nested":
        # The elastic/agent-skills repo uses an extra ``skills`` sub-directory
        search_dir = skills_dir / "skills" if (skills_dir / "skills").is_dir() else skills_dir

        # Fast path: check known category prefixes
        categories = catalog.categories or ()
        for category in categories:
            candidate = search_dir / category / skill_name / "SKILL.md"
            if candidate.is_file():
                return candidate

        # Slow path: scan all SKILL.md files for matching frontmatter ``name:``
        for sk_path in search_dir.rglob("SKILL.md"):
            try:
                text = sk_path.read_text(encoding="utf-8", errors="replace")
                in_frontmatter = False
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped == "---":
                        in_frontmatter = not in_frontmatter
                        continue
                    if in_frontmatter and stripped.startswith("name:"):
                        name_val = stripped[len("name:"):].strip().strip("\"'")
                        if name_val == skill_name:
                            return sk_path
            except OSError:
                continue

        return None

    if catalog.structure == "flat":
        candidate = skills_dir / skill_name / "SKILL.md"
        return candidate if candidate.is_file() else None

    return None


# ---------------------------------------------------------------------------
# Generic keyword matching
# ---------------------------------------------------------------------------


def _match_keywords(text: str, keyword_map: dict[str, str]) -> list[str]:
    """Match keywords in *text* against a keyword-to-skill-name map.

    Returns a de-duplicated list of matched skill names.
    """
    matched: list[str] = []
    seen: set[str] = set()
    text_lower = text.lower()
    for keyword, skill_name in keyword_map.items():
        if keyword in text_lower and skill_name not in seen:
            matched.append(skill_name)
            seen.add(skill_name)
    return matched


# ---------------------------------------------------------------------------
# Define built-in catalogs
# ---------------------------------------------------------------------------

# ── Elastic catalog ────────────────────────────────────────────────────

_ELASTIC_CATEGORIES = ("cloud", "elasticsearch", "kibana", "observability", "security")

_ELASTIC_CATALOG = SkillCatalog(
    name="elastic",
    dir_env_var="SKILL_CATALOG__ELASTIC__DIR",
    fallback_path="skills/elastic/",
    structure="category-nested",
    keyword_map={
        "esql": "elasticsearch-esql",
        "es|ql": "elasticsearch-esql",
        "elasticsearch query": "elasticsearch-esql",
        "elasticsearch": "elasticsearch-esql",
        "es_": "elasticsearch-esql",
        "es-index": "elasticsearch-esql",
        "kibana": "kibana-dashboards",
        "kibana connector": "kibana-connectors",
        "kibana alert": "kibana-alerting-rules",
        "kibana dashboard": "kibana-dashboards",
        "observability": "observability-logs-search",
        "apm": "observability-service-health",
        "slo": "observability-manage-slos",
        "service health": "observability-service-health",
        "log spike": "observability-logs-search",
        "log error": "observability-logs-search",
        "log anomaly": "observability-logs-search",
        "k8s": "observability-k8s-investigation",
        "kubernetes elastic": "observability-k8s-investigation",
        "elastic security": "security-alert-triage",
        "detection rule": "security-detection-rule-management",
        "threat": "security-alert-triage",
        "malware": "security-alert-triage",
        "endpoint security": "security-alert-triage",
        "case management": "security-case-management",
        "elastic cloud": "cloud-access-management",
        "serverless project": "cloud-create-project",
        "cloud access": "cloud-access-management",
    },
    fallback_env_vars=["ELASTIC_SKILLS_DIR"],
    categories=_ELASTIC_CATEGORIES,
)

# ── Microsoft catalog ─────────────────────────────────────────────────

_MS_CATALOG = SkillCatalog(
    name="microsoft",
    dir_env_var="SKILL_CATALOG__MICROSOFT__DIR",
    fallback_path="skills/microsoft/",
    structure="flat",
    keyword_map={
        # azure-diagnostics — Azure production debugging
        "app service": "azure-diagnostics",
        "vmss": "azure-diagnostics",
        "azure monitor": "azure-diagnostics",
        "app insights": "azure-diagnostics",
        "application insights": "azure-diagnostics",
        "resource health": "azure-diagnostics",
        "applens": "azure-diagnostics",
        # azure-kubernetes — AKS troubleshooting
        "aks": "azure-kubernetes",
        "kubernetes azure": "azure-kubernetes",
        "kubectl": "azure-kubernetes",
        "node pool": "azure-kubernetes",
        "container apps": "azure-kubernetes",
        # azure-kusto — Kusto/ADX queries (also activates if kql skill from microsoft/skills)
        "kusto": "azure-kusto",
        "adx": "azure-kusto",
        "kql": "azure-kusto",
        "data explorer": "azure-kusto",
        "log analytics query": "azure-kusto",
        # azure-messaging — Event Hubs / Service Bus
        "event hub": "azure-messaging",
        "eventhubs": "azure-messaging",
        "service bus": "azure-messaging",
        "amqp": "azure-messaging",
        "dead letter": "azure-messaging",
        "message lock": "azure-messaging",
        # azure-ai-projects-py — Foundry project interactions
        "foundry project": "azure-ai-projects-py",
        "ai project": "azure-ai-projects-py",
        "deployment": "azure-ai-projects-py",
    },
    fallback_env_vars=["MS_SKILLS_DIR"],
    inline_skills={
        "azure-diagnostics": """\
### Azure Diagnostics Skill

Use the following patterns when investigating Azure production incidents:

- **AppLens Diagnostics**: Look for built-in diagnostic tools in the Azure portal
  for App Service, VMSS, and Container Apps. Check "Diagnose and Solve Problems"
  for predefined runbooks.
- **Azure Monitor Metrics**: Check platform metrics (CPU, memory, request rate,
  error rate) over the incident window. Look for correlating spikes across
  dependent services (15-minute default window, narrow to 5-min granularity).
- **Resource Health**: Check the Resource Health blade for ongoing platform issues
  vs. application-level problems. A "Platform issue" indicates Azure-side cause;
  "Application issue" indicates user configuration/code.
- **Log Analytics / KQL**: Query Application Insights::
    requests
    | where timestamp between (datetime(INCIDENT_START) .. datetime(INCIDENT_END))
    | summarize failures=countif(success == false), total=count() by bin(timestamp, 5m)
    | where failures > 0
    | project timestamp, failure_rate=(failures*100.0/total)
- **Safe triage**: Start read-only (portal metrics, Log Analytics)—never modify
  production resources without confirming impact and rollback plan.
""",

        "azure-kubernetes": """\
### Azure Kubernetes Service (AKS) Skill

Use the following patterns when investigating AKS incidents:

- **Node Pool Health**: Check node pool status via ``az aks nodepool show``.
  Look for ``provisioningState`` (Succeeded/Failed), ``powerState`` (Running/Stopped),
  and ``count`` matching expected replica count.
- **Pod Lifecycle**: Common states and their causes:
  - `Pending`: Insufficient resources (CPU/memory), node selector mismatch,
    persistent volume binding issues, taint/toleration mismatch
  - `CrashLoopBackOff`: Application crash - check ``kubectl logs <pod> --previous``
  - `ImagePullBackOff`: Wrong image tag, registry auth failure, image not found
  - `NodeAffinity`: Node label mismatch or resource constraints
- **Cluster Autoscaler**: When pods are pending but autoscaler isn't scaling:
  check ``az aks show`` for ``autoScalerProfile`` settings—
  ``expander`` (random/most-pods/least-waste/priority), ``max-node-count``,
  ``scan-interval``. If nodes are at max count, check resource quotas.
- **Upgrade Failures**: Check ``az aks show`` for ``currentKubernetesVersion``
  and ``upgradeChannel`` settings. Failed upgrades often stem from PodDisruptionBudgets
  blocking node drain or insufficient node pool capacity for surge.
- **Kubectl Access**: If ``kubectl cannot connect``, check:
  1. ``az aks get-credentials`` is current
  2. AKS cluster public/private network settings
  3. RBAC bindings and Azure AD integration status
""",

        "azure-messaging": """\
### Azure Messaging (Event Hubs / Service Bus) Skill

Use the following patterns when investigating messaging incidents:

- **Event Hubs Connection Failures**:
  - Check ``EventHubProducerClient`` / ``EventHubConsumerClient`` connection string
  - Verify ``fullyQualifiedNamespace`` matches the Event Hubs namespace
  - Check network: private endpoint vs public access, firewall rules
  - SDK errors: `AuthenticationError` → SAS token expired/invalid;
    `ConnectException` → network/firewall blocking AMQP port 5671
- **Service Bus Dead-Letter Queue**:
  - Messages exceeding ``maxDeliveryCount`` land in the DLQ
  - Check ``deadLetterReason`` and ``deadLetterErrorDescription``
  - Common causes: unhandled exceptions in message handler, message lock expired
    during processing, session lock lost, invalid message format
- **Message Lock and Session Handling**:
  - Default lock duration: 30 seconds (Service Bus), can be increased per queue/subscription
  - Use ``renewLock()`` or ``autoRenewLock=true`` for long-running processing
  - Sessions: ``sessionId`` must match for FIFO ordering; session lock expires after 60s idle
- **Throttling / Quota**:
  - Event Hubs: per-partition throughput limits—check ``IncomingBytes`` / ``OutgoingBytes`` metrics
  - Service Bus: 1000 concurrent connections per namespace (Standard tier), 1MB message size limit
""",

        "azure-ai-projects-py": """\
### Azure AI Projects Python SDK Skill

Use the following patterns when working with Azure AI Foundry projects:

- **AIProjectClient**: Initialize with DefaultAzureCredential:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential
    client = AIProjectClient(
        project_endpoint="https://<region>.services.ai.azure.com/...",
        credential=DefaultAzureCredential(),
    )
- **Connections**: List and manage AI service connections (Azure OpenAI, AI Search,
  etc.) via ``client.connections.list()``. Connections provide pre-configured
  endpoints and authentication for AI services.
- **Deployments**: Check model deployment status via ``client.deployments.list()``.
  Each deployment has ``model.name``, ``model.version``, ``provisioningState``.
- **Agents**: For versioned agent operations, use ``PromptAgentDefinition``:
    from azure.ai.projects.models import PromptAgentDefinition
    prompt_def = PromptAgentDefinition(
        name="my-agent",
        instruction="You are...",
        deployment_name="gpt-4o",
    )
""",
    },
)

# The kql skill IS available as a SKILL.md — loaded from skills/microsoft/kql/SKILL.md
# No special handling needed: _resolve_skill_path finds it as a flat file, and if the
# file is absent (which it isn't), the inline fallback above would be tried.

# ── Register built-in catalogs ────────────────────────────────────────

register_catalog(_ELASTIC_CATALOG)
register_catalog(_MS_CATALOG)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def load_skill(catalog_name: str, skill_name: str) -> str:
    """Load a single skill's full content by catalog name and skill name.

    Tries ``SKILL.md`` file resolution first, then falls back to inline
    instruction blocks (if the catalog defines them).

    Args:
        catalog_name: Catalog identifier (e.g. ``"elastic"``, ``"microsoft"``).
        skill_name: The skill identifier (e.g. ``"azure-diagnostics"``,
            ``"elasticsearch-esql"``, ``"kql"``).

    Returns:
        The full skill content, or an empty string on failure.
    """
    catalog = get_catalog(catalog_name)
    if catalog is None:
        logger.warning("Unknown skill catalog '%s'", catalog_name)
        return ""

    # Try file first
    sk_path = _resolve_skill_path(catalog, skill_name)
    if sk_path is not None:
        try:
            text = sk_path.read_text(encoding="utf-8", errors="replace")
            logger.debug(
                "Loaded skill '%s' from catalog '%s' (%d bytes)",
                skill_name, catalog_name, len(text),
            )
            return text
        except OSError as exc:
            logger.debug(
                "Failed to read skill '%s' from catalog '%s': %s",
                skill_name, catalog_name, exc,
            )

    # Try inline fallback
    if catalog.inline_skills:
        inline = catalog.inline_skills.get(skill_name)
        if inline is not None:
            logger.debug(
                "Using inline instruction block for skill '%s' in catalog '%s'",
                skill_name, catalog_name,
            )
            return inline

    logger.debug("Skill '%s' not found in catalog '%s'", skill_name, catalog_name)
    return ""


def detect_all_signals(incident: dict) -> dict[str, list[str]]:
    """Detect signals from all registered skill catalogs in an incident payload.

    Args:
        incident: The full incident payload dictionary.

    Returns:
        A dict mapping each catalog name to a list of relevant skill names:
        ``{"elastic": [...], "microsoft": [...]}``
    """
    text = json.dumps(incident, default=str).lower()
    result: dict[str, list[str]] = {}
    for catalog in list_catalogs():
        result[catalog.name] = _match_keywords(text, catalog.keyword_map)
    return result


def build_incident_context(incident: dict) -> str:
    """Build a formatted skills context block for agent instructions.

    Orchestrates signal detection and skill loading across all registered
    catalogs:

    1. Scans the incident for signals from every registered catalog
    2. Loads the corresponding skill content (SKILL.md or inline blocks)
    3. Returns a formatted context block for agent instructions

    Returns an empty string if no signals are detected or no skill content loads.

    Args:
        incident: The full incident payload dictionary.

    Returns:
        A formatted string with skill context, or an empty string.
    """
    signals = detect_all_signals(incident)

    parts: list[str] = []

    for catalog in list_catalogs():
        for skill_name in signals.get(catalog.name, []):
            content = load_skill(catalog.name, skill_name)
            if content:
                parts.append(f"### {catalog.name.title()} Skill: {skill_name}\n\n{content}")

    if not parts:
        return ""

    header = (
        "## Agent Skills Context\n"
        "The following skills provide domain-specific guidance for processing "
        "this incident. Apply these patterns in your analysis and output.\n"
    )
    body = "\n\n---\n\n".join(parts)
    footer = (
        "\n\n---\n"
        "These skills provide domain-specific guidance for the services involved "
        "in this incident. Apply the patterns above when analysing signals and "
        "producing your response."
    )
    return f"\n\n{header}{body}{footer}\n"


# ---------------------------------------------------------------------------
# Reference tables — static documentation blocks included in agent prompts
# ---------------------------------------------------------------------------

_ELASTIC_SKILLS_REFERENCE = """\
## Elastic Skills Reference

The following Elastic Stack skills are available for domain-specific guidance:

| Skill | Category | Use When |
|-------|----------|----------|
| `elasticsearch-esql` | Elasticsearch | Querying Elasticsearch data, analysing logs, aggregating metrics |
| `observability-logs-search` | Observability | Investigating log spikes, errors, or anomalies |
| `observability-service-health` | Observability | Checking service health, APM transaction data, dependencies |
| `observability-k8s-investigation` | Observability | Debugging Kubernetes pod, node, or cluster issues |
| `observability-manage-slos` | Observability | Assessing SLO burn rate, error budget, or service targets |
| `kibana-dashboards` | Kibana | Managing dashboards, visualizations, and saved objects |
| `kibana-connectors` | Kibana | Configuring Slack, PagerDuty, Jira, or webhook connectors |
| `kibana-alerting-rules` | Kibana | Creating or interpreting alert rules and conditions |
| `security-alert-triage` | Security | Triaging Elastic Security alerts, threat intelligence |
| `security-detection-rule-management` | Security | Managing detection rules and MITRE ATT&CK mappings |
| `security-case-management` | Security | Managing security cases and incident response workflows |
| `cloud-access-management` | Cloud | Managing Elastic Cloud organisation access and API keys |
| `cloud-create-project` | Cloud | Creating Serverless projects (Elasticsearch, Observability, Security) |
"""

_MS_SKILLS_REFERENCE = """\
## Microsoft Azure Skills Reference

The following Azure skills are available for domain-specific guidance:

| Skill | Category | Use When |
|-------|----------|----------|
| `azure-diagnostics` | Azure Monitor | Debugging App Service, VMSS, or Container Apps production issues |
| `azure-kubernetes` | AKS | Investigating AKS cluster, node pool, or pod lifecycle issues |
| `azure-kusto` | Kusto/ADX | Querying Azure Data Explorer with KQL for log analytics |
| `azure-messaging` | Event Hubs / Service Bus | Troubleshooting messaging connection, auth, or dead-letter issues |
| `kql` | KQL Language | Writing correct, efficient Kusto Query Language queries |
"""

# Build a mapping so get_context_for_agent can dynamically collect reference
# tables from all registered catalogs.
_SKILLS_REFERENCE_TABLES: dict[str, str] = {
    "elastic": _ELASTIC_SKILLS_REFERENCE,
    "microsoft": _MS_SKILLS_REFERENCE,
}

# Agent-type-specific tails appended to the combined reference section
_TRIAGE_TAIL = """\
When the incident contains Elastic Stack signals (Elasticsearch, Kibana alerts,
Elastic Observability metrics/logs, or Elastic Security detection rules), use
the relevant Elastic skills above for API patterns, query syntax, and domain-specific
investigation workflows.

When the incident involves Azure services (App Service, AKS, Event Hubs, VMSS,
Application Insights), use the relevant Microsoft Azure skills above. Key patterns:
- **Azure Diagnostics**: Use AppLens and Azure Monitor metrics for root cause;
  check Resource Health for platform vs. application issues
- **AKS**: Check node pool status, pod lifecycle (Pending/CrashLoopBackOff/ImagePullBackOff),
  and cluster autoscaler configuration
- **Kusto/KQL**: Use pipe-forward KQL syntax for log analytics and time series correlation
- **Event Hubs/Service Bus**: Check connection/auth issues first, then message processing
  patterns (dead-letter, lock expiry, throttling)

Reference skill content from:
- `skills/elastic/skills/<category>/<skill-name>/SKILL.md`
- `skills/microsoft/<skill-name>/SKILL.md`

Key patterns for triage:
- **ES|QL queries**: Use piped syntax: FROM index | WHERE condition | STATS ... BY field | SORT field | LIMIT n
- **KQL queries**: Use pipe-forward: table | where condition | summarize ... by field
- **Log investigation**: Iteratively filter logs with exclusion patterns until root cause is isolated
- **Service health**: Check transaction error rates, latency percentiles, and dependency status
"""

_SUMMARY_TAIL = """\
When summarising an incident that involves Elastic Stack or Azure services, use
the skills reference above to correctly describe affected components, query results,
and domain-specific technical details in the summary narrative.
"""

_COMMS_TAIL = """\
When drafting communications for incidents involving Elastic Stack or Azure services:
- **Elastic incidents**: Reference Kibana connector skills for notification channels;
  use SLO/error budget language for impact; translate Elastic domain terms into business impact
- **Azure incidents**: Reference Azure diagnostics for accurately describing Azure service
  impact; use Kusto/KQL for log analysis context; translate Azure domain terms into business impact
- Translate all domain-specific technical details into business impact for stakeholder updates
"""

_PIR_TAIL = """\
When producing post-incident reports for Elastic or Azure-related incidents:
- **Elastic incidents**: Use ES|QL skill for describing data queries; reference Kibana
  dashboard skills for monitoring improvements; use SLO management for error budget impact
- **Azure incidents**: Use Kusto/KQL skill for log analysis queries used during investigation;
  reference Azure diagnostics for Azure service incident timeline; use AKS skill for
  AKS-specific timeline reconstruction; recommend additional Azure Monitor telemetry coverage
  based on incident findings
"""


def get_context_for_agent(agent_type: str) -> str:
    """Return a pre-built combined skills reference context for a specific agent type.

    Dynamically collects reference tables from all registered skill catalogs
    and appends the agent-type-specific guidance tail.

    Args:
        agent_type: One of ``"triage"``, ``"summary"``, ``"comms"``, ``"pir"``.

    Returns:
        A formatted instruction context block with combined domain guidance
        from all registered catalogs. Returns an empty string for unknown agent types.
    """
    tails: dict[str, str] = {
        "triage": _TRIAGE_TAIL,
        "summary": _SUMMARY_TAIL,
        "comms": _COMMS_TAIL,
        "pir": _PIR_TAIL,
    }

    if agent_type not in tails:
        return ""

    ref_parts: list[str] = []
    for catalog in list_catalogs():
        ref = _SKILLS_REFERENCE_TABLES.get(catalog.name, "")
        if ref:
            ref_parts.append(ref)

    combined_ref = "\n\n".join(ref_parts)

    if not combined_ref:
        return ""

    return combined_ref + "\n\n" + tails[agent_type]


# ---------------------------------------------------------------------------
# Backward-compatible wrappers (preserves the old API for callers not yet migrated)
# ---------------------------------------------------------------------------


def get_elastic_context_for_agent(agent_type: str) -> str:
    """Backward-compatible wrapper — returns Elastic-only context.

    Delegates to ``get_context_for_agent()`` but returns only the Elastic
    portion. Maintained for callers not yet migrated to the unified API.

    .. deprecated::
        Use ``get_context_for_agent()`` instead.
    """
    full = get_context_for_agent(agent_type)
    # Extract just the Elastic skills reference section
    if _ELASTIC_SKILLS_REFERENCE in full:
        return _ELASTIC_SKILLS_REFERENCE + _extract_elastic_tail(agent_type)
    return ""


def _extract_elastic_tail(agent_type: str) -> str:
    """Extract the Elastic-specific guidance tail from the combined context."""
    tails = {
        "triage": (
            "\n\nWhen the incident contains Elastic Stack signals (Elasticsearch, Kibana alerts,\n"
            "Elastic Observability metrics/logs, or Elastic Security detection rules), use\n"
            "the relevant skills above for API patterns, query syntax, and domain-specific\n"
            "investigation workflows.\n\n"
            "Key patterns for triage:\n"
            "- **ES|QL queries**: Use piped syntax: FROM index | WHERE condition | STATS ... BY field\n"
            "- **Log investigation**: Iteratively filter logs with exclusion patterns until root cause\n"
            "- **Service health**: Check transaction error rates, latency percentiles, and dependencies\n"
            "- **Security alerts**: Factor MITRE ATT&CK mapping into severity and urgency assessment\n"
        ),
        "summary": ("\n\nWhen summarising an incident involving Elastic Stack services, use correct\n"
                     "Elastic domain terminology (ES|QL queries, Kibana dashboards, SLOs, detection rules).\n"),
        "comms": ("\n\nWhen drafting communications for incidents involving Elastic Stack services:\n"
                  "- Reference Kibana connector skills for notification channels\n"
                  "- Use Elastic Observability language for impact descriptions (SLOs, error budgets)\n"
                  "- For security incidents, reference detection rules and threat intelligence\n"),
        "pir": ("\n\nWhen producing post-incident reports for Elastic-related incidents:\n"
                "- Use ES|QL skill for describing data queries\n"
                "- Reference Kibana dashboard skills for monitoring improvements\n"
                "- Use SLO management for assessing error budget impact\n"
                "- Reference Observability skills for telemetry recommendations\n"),
    }
    return tails.get(agent_type, "")


# ===========================================================================
# Backward-compat aliases for callers importing private names
# (preserves imports from app.elastic_skills and tests)
# ===========================================================================

# _ELASTIC_KEYWORD_TO_SKILL — preserves from app.elastic_skills import
_ELASTIC_KEYWORD_TO_SKILL: dict[str, str] = _ELASTIC_CATALOG.keyword_map

# _resolve_elastic_skill — preserves from app.elastic_skills._skill_path import
def _resolve_elastic_skill(skill_name: str) -> Path | None:
    return _resolve_skill_path(_ELASTIC_CATALOG, skill_name)


# _resolve_ms_skill — preserves test import
def _resolve_ms_skill(skill_name: str) -> Path | None:
    return _resolve_skill_path(_MS_CATALOG, skill_name)


# _detect_elastic_signals — preserves test import
def _detect_elastic_signals(incident: dict) -> list[str]:
    return detect_all_signals(incident).get("elastic", [])


# _detect_microsoft_signals — preserves test import
def _detect_microsoft_signals(incident: dict) -> list[str]:
    return detect_all_signals(incident).get("microsoft", [])


# detect_elastic_signals — preserves from app.elastic_skills import
detect_elastic_signals = _detect_elastic_signals  # type: ignore[assignment]  # noqa: E731

# build_elastic_context — preserves from app.elastic_skills import
build_elastic_context = build_incident_context  # noqa: E731

# load_elastic_skill — preserves deprecated alias
load_elastic_skill = lambda name: load_skill("elastic", name)  # noqa: E731
