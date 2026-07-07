"""RAG (Retrieval-Augmented Generation) module for On-Call Copilot.

Provides a search tool that agents can call to retrieve knowledge base
documents (runbooks, PIRs, playbooks). Supports two backends:

  1. Azure AI Search (production) — when RAG_SEARCH_ENDPOINT is set
  2. In-memory (local dev/demo) — with seed documents from knowledge/ directory

Usage:
    from app.rag import create_rag_tools
    tools = create_rag_tools()  # returns list[FunctionTool]
"""

import logging
import os
import re
from pathlib import Path
from typing import Any

from agent_framework._tools import FunctionTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── Document model (lightweight, no SK dependency for in-memory) ──────────


class KnowledgeDocument:
    """A single document in the RAG knowledge base."""

    __slots__ = ("id", "title", "category", "content")

    def __init__(self, id: str, title: str, category: str, content: str) -> None:
        self.id = id
        self.title = title
        self.category = category
        self.content = content


# ── Input model for the search tool ───────────────────────────────────────


class SearchKnowledgeBaseInput(BaseModel):
    """Input parameters for the search_knowledge_base tool."""

    query: str = Field(
        description="Search query describing what to find "
        "(e.g. 'Redis failover steps', 'TLS certificate rotation')."
    )
    top: int = Field(
        default=5,
        description="Maximum number of results to return (1–20).",
        ge=1,
        le=20,
    )


# ── Seed Document Loader ──────────────────────────────────────────────────


def _parse_markdown(filepath: Path) -> KnowledgeDocument | None:
    """Parse a markdown file into a ``KnowledgeDocument``."""
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Cannot read %s: %s", filepath, exc)
        return None

    lines = text.splitlines()

    title = "Untitled"
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            title = stripped[2:].strip()
            break

    category = "General"
    for line in lines:
        stripped = line.strip()
        m = re.match(r"\*\*Category:\*\*\s*(.+)", stripped)
        if m:
            category = m.group(1).strip()
            break

    return KnowledgeDocument(
        id=filepath.stem,
        title=title,
        category=category,
        content=text.strip(),
    )


def _load_seed_documents(knowledge_dir: str | Path | None = None) -> list[KnowledgeDocument]:
    """Load all ``.md`` files from *knowledge_dir* (default ``knowledge/``)."""
    if knowledge_dir is None:
        knowledge_dir = Path(__file__).resolve().parent.parent / "knowledge"
    else:
        knowledge_dir = Path(knowledge_dir)

    if not knowledge_dir.is_dir():
        logger.info("Knowledge directory %s not found — no seed documents loaded", knowledge_dir)
        return []

    documents: list[KnowledgeDocument] = []
    for md_file in sorted(knowledge_dir.glob("*.md")):
        doc = _parse_markdown(md_file)
        if doc is not None:
            documents.append(doc)

    logger.info("Loaded %d seed document(s) from %s", len(documents), knowledge_dir)
    return documents


# ── Azure AI Search Backend ──────────────────────────────────────────────


def _create_azure_search_tool(search_endpoint: str) -> FunctionTool | None:
    """Create a ``FunctionTool`` that searches an Azure AI Search index.

    Reads ``RAG_SEARCH_INDEX_NAME`` (default ``knowledge-index``) and
    optionally ``RAG_SEARCH_API_KEY`` from environment variables.

    Uses the **azure-search-documents** SDK directly (no Semantic Kernel dependency).
    The index is expected to have fields: ``id``, ``title``, ``category``, ``content``.
    """
    index_name = os.environ.get("RAG_SEARCH_INDEX_NAME", "knowledge-index")
    api_key = os.environ.get("RAG_SEARCH_API_KEY")
    # cloud default is None, which means Entra ID auth

    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient

        from app.config import get_azure_credential

        if api_key:
            credential: AzureKeyCredential = AzureKeyCredential(api_key)
        else:
            credential = get_azure_credential()

        client = SearchClient(
            endpoint=search_endpoint,
            index_name=index_name,
            credential=credential,
        )

        async def search_handler(**kw: Any) -> str:
            q = kw.get("query", "")
            t = kw.get("top", 5)
            try:
                results = client.search(
                    search_text=q,
                    top=t,
                    include_total_count=False,
                )
            except Exception as exc:
                logger.error("RAG: Azure AI Search query failed: %s", exc)
                return "No results found."

            lines: list[str] = []
            for i, r in enumerate(results, 1):
                title = r.get("title", "Untitled")
                category = r.get("category", "General")
                content = r.get("content", "")
                score = r.get("@search.score", 0.0)
                lines.append(
                    f"Result {i} [{category}] {title} (score={score:.3f})\n---\n{content}\n"
                )
            if not lines:
                return "No results found."
            return "\n".join(lines)

        tool = FunctionTool(
            name="search_knowledge_base",
            description=(
                "Search the incident response knowledge base for troubleshooting "
                "runbooks, post-incident reviews, and operational playbooks. "
                "Provide a query describing what you need (e.g. 'Redis failover "
                "steps', 'TLS certificate rotation')."
            ),
            func=search_handler,
            input_model=SearchKnowledgeBaseInput,
        )
        logger.info("RAG: Azure AI Search backend ready (index=%s)", index_name)
        return tool

    except Exception as exc:
        logger.error("RAG: Failed to create Azure AI Search tool: %s", exc)
        return None


# ── In-Memory Backend ────────────────────────────────────────────────────


def _keyword_search(
    docs: list[KnowledgeDocument],
    query: str,
    top: int,
) -> list[tuple[KnowledgeDocument, int]]:
    """Simple case-insensitive keyword score over title, category, and content."""
    terms = query.lower().split()
    scored: list[tuple[KnowledgeDocument, int]] = []
    for doc in docs:
        body = (doc.title + " " + doc.category + " " + doc.content).lower()
        score = sum(body.count(term) for term in terms)
        if score > 0:
            scored.append((doc, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top]


def _create_inmemory_search_tool(documents: list[KnowledgeDocument]) -> FunctionTool:
    """Create a ``FunctionTool`` that searches seed documents in memory."""

    async def search_handler(**kw: Any) -> str:
        q = kw.get("query", "")
        t = kw.get("top", 5)
        hits = _keyword_search(documents, q, t)
        if not hits:
            return "No results found."
        lines: list[str] = []
        for i, (doc, score) in enumerate(hits, 1):
            lines.append(
                f"Result {i} [{doc.category}] {doc.title} (score={score})\n---\n{doc.content}\n"
            )
        return "\n".join(lines)

    return FunctionTool(
        name="search_knowledge_base",
        description=(
            "Search the incident response knowledge base for troubleshooting "
            "runbooks, post-incident reviews, and operational playbooks. "
            "Provide a query describing what you need (e.g. 'Redis failover "
            "steps', 'TLS certificate rotation')."
        ),
        func=search_handler,
        input_model=SearchKnowledgeBaseInput,
    )


# ── Public API ────────────────────────────────────────────────────────────


def create_rag_tools() -> list[FunctionTool]:
    """Create RAG search tools for the agent framework.

    Checks the environment to determine which backend to use:

    * ``RAG_SEARCH_ENDPOINT`` set → Azure AI Search
    * otherwise → in-memory with ``knowledge/`` seed documents

    Returns an empty list when neither backend is available or when the
    ``RAG__DISABLE`` environment variable is set to a truthy value.
    """
    if os.environ.get("RAG__DISABLE", "").lower() in ("1", "true", "yes"):
        logger.info("RAG: Disabled via RAG__DISABLE")
        return []

    azure_endpoint = os.environ.get("RAG_SEARCH_ENDPOINT")
    if azure_endpoint:
        tool = _create_azure_search_tool(azure_endpoint)
        if tool is not None:
            return [tool]
        logger.warning("RAG: Azure AI Search failed — attempting in-memory fallback")

    documents = _load_seed_documents()
    if not documents:
        logger.warning("RAG: No seed documents — RAG disabled")
        return []

    tool = _create_inmemory_search_tool(documents)
    logger.info(
        "RAG: In-memory backend ready (%d documents)", len(documents)
    )
    return [tool]
