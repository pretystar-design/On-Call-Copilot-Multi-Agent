"""Unit tests for app.skill_loader — unified Agent Skills integration.

Tests multi-catalog skill loading, combined signal detection (Elastic + Microsoft),
context building, and backward-compatible wrappers.
"""

from __future__ import annotations

import os

os.environ.setdefault("ELASTIC_SKILLS_DIR", "skills/elastic")
os.environ.setdefault("MS_SKILLS_DIR", "skills/microsoft")

from app.skill_loader import (
    _detect_elastic_signals,
    _detect_microsoft_signals,
    _resolve_elastic_skill,
    _resolve_ms_skill,
    build_incident_context,
    detect_all_signals,
    get_context_for_agent,
    get_elastic_context_for_agent,
    load_skill,
)


# ── Path Resolution ──────────────────────────────────────────────────


class TestPathResolution:
    def test_resolve_elastic_elasticsearch_esql(self):
        path = _resolve_elastic_skill("elasticsearch-esql")
        assert path is not None
        assert path.name == "SKILL.md"
        assert "elasticsearch-esql" in str(path)

    def test_resolve_elastic_returns_none_for_unknown(self):
        assert _resolve_elastic_skill("nonexistent-skill") is None

    def test_resolve_ms_kql(self):
        path = _resolve_ms_skill("kql")
        assert path is not None
        assert path.name == "SKILL.md"
        assert "kql" in str(path)

    def test_resolve_ms_returns_none_for_unknown(self):
        # azure-diagnostics is inline-only, no SKILL.md file
        assert _resolve_ms_skill("azure-diagnostics") is None


# ── load_skill ───────────────────────────────────────────────────────


class TestLoadSkill:
    def test_loads_elastic_elasticsearch_esql(self):
        content = load_skill("elastic", "elasticsearch-esql")
        assert len(content) > 1000
        assert "ES|QL" in content

    def test_loads_microsoft_kql(self):
        content = load_skill("microsoft", "kql")
        assert len(content) > 100
        assert "KQL" in content or "kql" in content

    def test_loads_microsoft_azure_diagnostics_inline(self):
        content = load_skill("microsoft", "azure-diagnostics")
        assert len(content) > 100
        assert "Azure Diagnostics" in content or "AppLens" in content

    def test_loads_microsoft_azure_kubernetes_inline(self):
        content = load_skill("microsoft", "azure-kubernetes")
        assert len(content) > 100
        assert "AKS" in content or "Kubernetes" in content

    def test_loads_microsoft_azure_messaging_inline(self):
        content = load_skill("microsoft", "azure-messaging")
        assert len(content) > 100
        assert "Event Hubs" in content or "Service Bus" in content

    def test_loads_microsoft_azure_ai_projects_inline(self):
        content = load_skill("microsoft", "azure-ai-projects-py")
        assert len(content) > 100
        assert "AIProjectClient" in content

    def test_returns_empty_for_unknown_elastic(self):
        assert load_skill("elastic", "completely-fake-skill") == ""

    def test_returns_empty_for_unknown_microsoft(self):
        assert load_skill("microsoft", "completely-fake-skill") == ""

    def test_returns_empty_for_unknown_catalog(self):
        assert load_skill("nonexistent-catalog", "some-skill") == ""


# ── detect_all_signals / _detect_elastic_signals / _detect_microsoft_signals ──


class TestSignalDetection:
    def test_detects_elastic_esql_from_title(self):
        incident = {"title": "ES|QL query errors in logs"}
        assert "elasticsearch-esql" in _detect_elastic_signals(incident)

    def test_detects_elastic_kibana_from_alert(self):
        incident = {"alerts": [{"name": "Kibana dashboard not loading"}]}
        assert "kibana-dashboards" in _detect_elastic_signals(incident)

    def test_detects_microsoft_aks(self):
        incident = {"title": "AKS node pool scaling failure"}
        assert "azure-kubernetes" in _detect_microsoft_signals(incident)

    def test_detects_microsoft_app_service(self):
        incident = {"title": "App Service 5xx errors after deployment"}
        assert "azure-diagnostics" in _detect_microsoft_signals(incident)

    def test_detects_microsoft_event_hub(self):
        incident = {"title": "Event Hub connection failures"}
        assert "azure-messaging" in _detect_microsoft_signals(incident)

    def test_detects_microsoft_kusto(self):
        incident = {"title": "Kusto query timeout in Log Analytics"}
        assert "azure-kusto" in _detect_microsoft_signals(incident)

    def test_detects_microsoft_foundry_project(self):
        incident = {"title": "Foundry project deployment stuck"}
        assert "azure-ai-projects-py" in _detect_microsoft_signals(incident)

    def test_detect_all_elastic_only(self):
        incident = {"title": "Elasticsearch cluster health degraded"}
        signals = detect_all_signals(incident)
        assert "elasticsearch-esql" in signals["elastic"]
        assert signals["microsoft"] == []

    def test_detect_all_microsoft_only(self):
        incident = {"title": "AKS node pool scaling failure"}
        signals = detect_all_signals(incident)
        assert signals["elastic"] == []
        assert "azure-kubernetes" in signals["microsoft"]

    def test_detect_all_mixed_signals(self):
        incident = {
            "title": "ES|QL query errors and AKS scaling failure",
            "alerts": [
                {"name": "Kibana dashboard error"},
                {"name": "App Service 5xx"},
            ],
        }
        signals = detect_all_signals(incident)
        assert "elasticsearch-esql" in signals["elastic"]
        assert "kibana-dashboards" in signals["elastic"]
        assert "azure-kubernetes" in signals["microsoft"]
        assert "azure-diagnostics" in signals["microsoft"]

    def test_detect_all_no_signals(self):
        incident = {"title": "Network switch port flap"}
        signals = detect_all_signals(incident)
        assert signals["elastic"] == []
        assert signals["microsoft"] == []

    def test_no_duplicate_elastic(self):
        incident = {"title": "Elasticsearch and ES|QL query issues"}
        skills = _detect_elastic_signals(incident)
        assert len([s for s in skills if s == "elasticsearch-esql"]) == 1

    def test_no_duplicate_microsoft(self):
        incident = {"title": "AKS kubectl connection errors"}
        skills = _detect_microsoft_signals(incident)
        assert len([s for s in skills if s == "azure-kubernetes"]) == 1


# ── build_incident_context ───────────────────────────────────────────


class TestBuildIncidentContext:
    def test_returns_context_for_elastic_incident(self):
        incident = {"title": "ES|QL query performance degradation"}
        ctx = build_incident_context(incident)
        assert len(ctx) > 500
        assert "Agent Skills Context" in ctx
        assert "Elastic Skill: elasticsearch-esql" in ctx

    def test_returns_context_for_microsoft_incident(self):
        incident = {"title": "AKS node pool scaling failure"}
        ctx = build_incident_context(incident)
        assert len(ctx) > 100
        assert "Agent Skills Context" in ctx
        assert "Microsoft Skill: azure-kubernetes" in ctx

    def test_returns_context_for_mixed_incident(self):
        incident = {
            "title": "ES|QL query errors and AKS scaling failure",
        }
        ctx = build_incident_context(incident)
        assert "Elastic Skill: elasticsearch-esql" in ctx
        assert "Microsoft Skill: azure-kubernetes" in ctx

    def test_returns_empty_for_non_relevant_incident(self):
        incident = {"title": "Network switch port flap"}
        assert build_incident_context(incident) == ""

    def test_returns_empty_for_empty_incident(self):
        assert build_incident_context({}) == ""

    def test_kql_skill_loaded_from_skilli_md(self):
        kql_content = load_skill("microsoft", "kql")
        assert len(kql_content) > 100
        assert "KQL" in kql_content


# ── get_context_for_agent ────────────────────────────────────────────


class TestGetContextForAgent:
    def test_triage_combined_context_includes_both_catalogs(self):
        ctx = get_context_for_agent("triage")
        assert len(ctx) > 500
        assert "Elastic Skills Reference" in ctx
        assert "Microsoft Azure Skills Reference" in ctx

    def test_elastic_only_context(self):
        ctx = get_elastic_context_for_agent("triage")
        assert len(ctx) > 500
        assert "Elastic Skills Reference" in ctx
        assert "Microsoft" not in ctx

    def test_each_agent_has_unique_context(self):
        contexts = {t: get_context_for_agent(t) for t in ("triage", "summary", "comms", "pir")}
        assert len(set(contexts.values())) == 4

    def test_returns_empty_for_unknown_agent(self):
        assert get_context_for_agent("unknown-agent") == ""


# ── Backward Compatibility ───────────────────────────────────────────


class TestBackwardCompatibility:
    def test_elastic_skills_module_imports_work(self):
        from app.elastic_skills import (  # noqa: F811
            ALL_ELASTIC_SKILLS,
            build_elastic_context,
            detect_elastic_signals,
            get_elastic_context_for_agent,
            load_skill as old_load_skill,
        )

        assert "elasticsearch-esql" in ALL_ELASTIC_SKILLS
        assert len(old_load_skill("elasticsearch-esql")) > 1000

    def test_build_elastic_context_has_agent_skills_header(self):
        from app.elastic_skills import build_elastic_context

        incident = {"title": "ES|QL query latency"}
        ctx = build_elastic_context(incident)
        assert "Agent Skills Context" in ctx
        assert "elasticsearch-esql" in ctx


# ── Generic Catalog Registry ──────────────────────────────────────────


class TestCatalogRegistry:
    def test_skill_catalog_dataclass_fields(self):
        """Verify SkillCatalog stores all fields correctly."""
        from app.skill_loader import SkillCatalog

        cat = SkillCatalog(
            name="test-cat",
            dir_env_var="SKILL_CATALOG__TEST__DIR",
            fallback_path="skills/test/",
            structure="flat",
            keyword_map={"keyword": "test-skill"},
        )
        assert cat.name == "test-cat"
        assert cat.dir_env_var == "SKILL_CATALOG__TEST__DIR"
        assert cat.structure == "flat"
        assert cat.keyword_map == {"keyword": "test-skill"}
        assert cat.fallback_env_vars == []
        assert cat.inline_skills is None
        assert cat.categories is None

    def test_register_catalog_duplicate_raises(self):
        """Registering the same catalog name twice raises ValueError."""
        from app.skill_loader import SkillCatalog, register_catalog, _catalog_registry

        cat = SkillCatalog(
            name="_test_dup_check",
            dir_env_var="SKILL_CATALOG__DUP__DIR",
            fallback_path="skills/dup/",
            structure="flat",
            keyword_map={},
        )
        try:
            register_catalog(cat)
            import pytest
            with pytest.raises(ValueError, match="already registered"):
                register_catalog(cat)
        finally:
            _catalog_registry.pop("_test_dup_check", None)

    def test_get_catalog_returns_none_for_unknown(self):
        from app.skill_loader import get_catalog

        assert get_catalog("__nonexistent__") is None

    def test_list_catalogs_includes_builtins(self):
        from app.skill_loader import list_catalogs

        names = {c.name for c in list_catalogs()}
        assert "elastic" in names
        assert "microsoft" in names


class TestResolveCatalogDir:
    def test_elastic_catalog_resolves_from_env(self):
        """Verify _resolve_catalog_dir finds the elastic catalog."""
        from app.skill_loader import _resolve_catalog_dir, get_catalog

        elastic = get_catalog("elastic")
        assert elastic is not None
        path = _resolve_catalog_dir(elastic)
        assert path is not None
        assert path.is_dir()

    def test_microsoft_catalog_resolves_from_env(self):
        from app.skill_loader import _resolve_catalog_dir, get_catalog

        ms = get_catalog("microsoft")
        assert ms is not None
        path = _resolve_catalog_dir(ms)
        assert path is not None
        assert path.is_dir()


class TestResolveSkillPath:
    def test_resolve_elastic_skill_path(self):
        from app.skill_loader import _resolve_skill_path, get_catalog

        elastic = get_catalog("elastic")
        assert elastic is not None
        path = _resolve_skill_path(elastic, "elasticsearch-esql")
        assert path is not None
        assert path.name == "SKILL.md"
        assert "elasticsearch-esql" in str(path)

    def test_resolve_flat_skill_path_kql(self):
        from app.skill_loader import _resolve_skill_path, get_catalog

        ms = get_catalog("microsoft")
        assert ms is not None
        path = _resolve_skill_path(ms, "kql")
        assert path is not None
        assert path.name == "SKILL.md"

    def test_resolve_skill_path_returns_none_for_unknown(self):
        from app.skill_loader import _resolve_skill_path, get_catalog

        elastic = get_catalog("elastic")
        assert elastic is not None
        assert _resolve_skill_path(elastic, "__bogus__") is None


class TestDetectAllSignalsExtended:
    def test_detect_all_catalog_keys_match_registry(self):
        """detect_all_signals returns keys matching all registered catalog names."""
        from app.skill_loader import detect_all_signals, list_catalogs

        incident = {"title": "AKS node pool and ES|QL query errors"}
        signals = detect_all_signals(incident)

        catalog_names = {c.name for c in list_catalogs()}
        assert set(signals.keys()) == catalog_names

    def test_detect_all_elastic_keyword_matches(self):
        from app.skill_loader import detect_all_signals

        signals = detect_all_signals({"title": "node pool scaling failure in AKS"})
        assert "elastic" in signals
        assert "microsoft" in signals
        assert signals["elastic"] == []
        assert signals["microsoft"] == ["azure-kubernetes"]

    def test_detect_all_elastic_kibana(self):
        from app.skill_loader import detect_all_signals

        signals = detect_all_signals({"title": "Kibana dashboard errors"})
        assert "kibana-dashboards" in signals["elastic"]


class TestGenericKeywordMatching:
    def test_match_keywords_empty_text(self):
        from app.skill_loader import _match_keywords

        assert _match_keywords("", {"aks": "azure-kubernetes"}) == []

    def test_match_keywords_no_match(self):
        from app.skill_loader import _match_keywords

        result = _match_keywords("hello world", {"aks": "azure-kubernetes"})
        assert result == []

    def test_match_keywords_deduplicates(self):
        from app.skill_loader import _match_keywords

        result = _match_keywords(
            "aks kubectl aks node pool",
            {"aks": "azure-kubernetes", "kubectl": "azure-kubernetes"},
        )
        # "aks" and "kubectl" both map to "azure-kubernetes" — deduped
        assert result == ["azure-kubernetes"]
