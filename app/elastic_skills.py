"""
Elastic Agent Skills — backward-compatible wrapper.

All functionality has moved to :mod:`app.skill_loader`. This module
re-exports the public API from the unified skill loader for callers
that haven't been migrated yet.

.. deprecated::
    Import directly from ``app.skill_loader`` instead.
"""

from __future__ import annotations

import logging
import os
import warnings
from pathlib import Path

from app.skill_loader import (  # noqa: F401 — re-export for backward compat
    _ELASTIC_KEYWORD_TO_SKILL,
    _resolve_elastic_skill as _skill_path,
    build_incident_context as build_elastic_context,
    detect_elastic_signals,
    get_elastic_context_for_agent as _get_elastic_only,
    load_skill as _load_skill_new,
)

logger = logging.getLogger(__name__)

# Maintain backward compatibility for the all-skills set
ALL_ELASTIC_SKILLS = sorted(set(_ELASTIC_KEYWORD_TO_SKILL.values()))


def load_skill(skill_name: str) -> str:
    """Load a single Elastic skill's SKILL.md content (backward-compatible).

    Calls the new two-argument ``load_skill(catalog, skill_name)`` with
    ``catalog="elastic"``.
    """
    return _load_skill_new("elastic", skill_name)


def _get_skills_dir() -> Path:
    """Return the path to the Elastic Agent Skills directory.

    Backward-compatible wrapper. Uses the old ``SKILLS_DIR`` env var
    or falls back to ``skills/elastic/``.
    """
    raw = os.environ.get("SKILLS_DIR", "")
    if raw:
        path = Path(raw)
    else:
        path = Path(__file__).resolve().parent.parent / "skills" / "elastic"
    return path.resolve()


def get_elastic_context_for_agent(agent_type: str) -> str:
    """Return a pre-built Elastic skills context block for a specific agent type.

    This is a backward-compatible wrapper that returns the **Elastic-only**
    portion of the combined skills context.

    .. deprecated::
        Use ``skill_loader.get_context_for_agent()`` to get combined
        Elastic + Microsoft skills context.

    Args:
        agent_type: One of ``"triage"``, ``"summary"``, ``"comms"``, ``"pir"``.

    Returns:
        A formatted instruction context block with Elastic domain guidance.
    """
    warnings.warn(
        "get_elastic_context_for_agent() is deprecated. "
        "Use skill_loader.get_context_for_agent() for combined skills context.",
        DeprecationWarning,
        stacklevel=2,
    )
    return _get_elastic_only(agent_type)
