"""Unit tests for app.elastic_skills — Elastic Agent Skills integration.

Tests skill loading, signal detection, context building, and graceful degradation.
"""

from __future__ import annotations

import os

os.environ.setdefault("SKILLS_DIR", "skills/elastic")

from app.elastic_skills import (
    ALL_ELASTIC_SKILLS,
    _get_skills_dir,
    _skill_path,
    build_elastic_context,
    detect_elastic_signals,
    get_elastic_context_for_agent,
    load_skill,
)


# ── _get_skills_dir / _skill_path ────────────────────────────────────


class TestSkillsDirectory:
    def test_default_skills_dir_exists(self):
        path = _get_skills_dir()
        assert path.exists(), f"SKILLS_DIR '{path}' should exist"
        assert path.is_dir()

    def test_skill_path_resolves_elasticsearch_esql(self):
        path = _skill_path("elasticsearch-esql")
        assert path is not None
        assert path.name == "SKILL.md"
        assert "elasticsearch-esql" in str(path)

    def test_skill_path_returns_none_for_unknown(self):
        assert _skill_path("nonexistent-skill") is None


# ── load_skill ───────────────────────────────────────────────────────


class TestLoadSkill:
    def test_loads_elasticsearch_esql(self):
        content = load_skill("elasticsearch-esql")
        assert len(content) > 1000
        assert "ES|QL" in content

    def test_loads_observability_logs_search(self):
        content = load_skill("observability-logs-search")
        assert len(content) > 1000
        assert "Logs Search" in content

    def test_loads_kibana_connectors(self):
        content = load_skill("kibana-connectors")
        assert len(content) > 1000
        assert "Connectors" in content or "connector" in content.lower()

    def test_loads_security_alert_triage(self):
        content = load_skill("security-alert-triage")
        assert len(content) > 500
        assert "alert" in content.lower()

    def test_returns_empty_for_unknown_skill(self):
        assert load_skill("completely-fake-skill") == ""

    def test_all_known_skills_load(self):
        for skill_name in ALL_ELASTIC_SKILLS:
            content = load_skill(skill_name)
            assert content, f"Skill '{skill_name}' should load successfully"


# ── detect_elastic_signals ───────────────────────────────────────────


class TestDetectElasticSignals:
    def test_detects_esql_from_title(self):
        incident = {"title": "ES|QL query errors in logs"}
        skills = detect_elastic_signals(incident)
        assert "elasticsearch-esql" in skills

    def test_detects_kibana_from_alert(self):
        incident = {"alerts": [{"name": "Kibana dashboard not loading"}]}
        skills = detect_elastic_signals(incident)
        assert "kibana-dashboards" in skills

    def test_detects_observability_from_apm(self):
        incident = {"title": "APM transaction error rate spike"}
        skills = detect_elastic_signals(incident)
        assert "observability-service-health" in skills

    def test_detects_security_from_detection_rule(self):
        incident = {"alerts": [{"name": "detection rule match"}, {"name": "malware detected"}]}
        skills = detect_elastic_signals(incident)
        assert "security-detection-rule-management" in skills
        assert "security-alert-triage" in skills

    def test_detects_elasticsearch_keyword(self):
        incident = {"title": "Elasticsearch cluster health degraded"}
        skills = detect_elastic_signals(incident)
        assert "elasticsearch-esql" in skills

    def test_returns_empty_for_non_elastic_incident(self):
        incident = {"title": "CPU spike in web servers", "alerts": [{"name": "High CPU"}]}
        skills = detect_elastic_signals(incident)
        assert skills == []

    def test_no_duplicate_skills(self):
        incident = {"title": "Elasticsearch and ES|QL query issues"}
        skills = detect_elastic_signals(incident)
        # Both 'elasticsearch' and 'esql' match -> same skill -> should appear once
        assert len([s for s in skills if s == "elasticsearch-esql"]) == 1

    def test_multi_signal_correlation(self):
        incident = {
            "title": "Security incident with ES|QL queries",
            "alerts": [{"name": "detection rule match"}],
            "logs": [{"source": "elasticsearch", "lines": ["ERROR timeout"]}],
        }
        skills = detect_elastic_signals(incident)
        assert "elasticsearch-esql" in skills
        assert "security-detection-rule-management" in skills

    def test_handles_nested_incident_structure(self):
        incident = {"incident": {"title": "Kibana connector failure"}, "metadata": {"source": "elastic"}}
        skills = detect_elastic_signals(incident)
        assert "kibana-connectors" in skills or "kibana-dashboards" in skills

    def test_detects_cloud_signals(self):
        incident = {"title": "Elastic Cloud access management issue"}
        skills = detect_elastic_signals(incident)
        assert "cloud-access-management" in skills


# ── build_elastic_context ────────────────────────────────────────────


class TestBuildElasticContext:
    def test_returns_context_for_elastic_incident(self):
        incident = {"title": "ES|QL query performance degradation"}
        ctx = build_elastic_context(incident)
        assert len(ctx) > 500
        assert "Agent Skills Context" in ctx
        assert "elasticsearch-esql" in ctx

    def test_returns_empty_for_non_elastic_incident(self):
        incident = {"title": "Network switch port flap"}
        ctx = build_elastic_context(incident)
        assert ctx == ""

    def test_returns_empty_for_empty_incident(self):
        assert build_elastic_context({}) == ""


# ── get_elastic_context_for_agent ────────────────────────────────────


class TestGetElasticContextForAgent:
    def test_triage_context_is_not_empty(self):
        ctx = get_elastic_context_for_agent("triage")
        assert len(ctx) > 500
        assert "ES|QL" in ctx

    def test_summary_context_is_not_empty(self):
        ctx = get_elastic_context_for_agent("summary")
        assert len(ctx) > 200

    def test_comms_context_is_not_empty(self):
        ctx = get_elastic_context_for_agent("comms")
        assert len(ctx) > 200
        assert "Kibana" in ctx

    def test_pir_context_is_not_empty(self):
        ctx = get_elastic_context_for_agent("pir")
        assert len(ctx) > 200

    def test_returns_empty_for_unknown_agent(self):
        assert get_elastic_context_for_agent("unknown-agent") == ""

    def test_each_agent_has_unique_context(self):
        contexts = {t: get_elastic_context_for_agent(t) for t in ("triage", "summary", "comms", "pir")}
        # Each agent context should be unique
        assert len(set(contexts.values())) == 4

    def test_context_includes_skill_reference_table(self):
        ctx = get_elastic_context_for_agent("triage")
        assert "| `elasticsearch-esql`" in ctx
        assert "| `observability-logs-search`" in ctx
        assert "| `security-alert-triage`" in ctx


# ── ALL_ELASTIC_SKILLS ───────────────────────────────────────────────


class TestAllElasticSkills:
    def test_contains_core_skills(self):
        assert "elasticsearch-esql" in ALL_ELASTIC_SKILLS
        assert "observability-logs-search" in ALL_ELASTIC_SKILLS
        assert "kibana-connectors" in ALL_ELASTIC_SKILLS
        assert "security-alert-triage" in ALL_ELASTIC_SKILLS
        assert "cloud-access-management" in ALL_ELASTIC_SKILLS

    def test_has_no_duplicates(self):
        assert len(ALL_ELASTIC_SKILLS) == len(set(ALL_ELASTIC_SKILLS))
