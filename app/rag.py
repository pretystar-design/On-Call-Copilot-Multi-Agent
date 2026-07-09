"""RAG (Retrieval-Augmented Generation) module for On-Call Copilot.

Provides a search tool that agents can call to retrieve knowledge base
documents (runbooks, PIRs, playbooks). Supports two backends:

  1. Azure AI Search (production) — when RAG_SEARCH_ENDPOINT is set
  2. In-memory (local dev/demo) — with seed documents from knowledge/ directory

Usage:
    from app.rag import create_rag_tools
    tools = create_rag_tools()  # returns list[FunctionTool]
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from agent_framework._tools import FunctionTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Module-level thread-safe document snapshot for in-memory backend
_documents_lock = threading.Lock()
_documents_snapshot: list[KnowledgeDocument] = []
_reindex_in_progress = False

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


def _make_document_id(filepath: Path, base_dir: Path) -> str:
    """Create a unique document ID from a file's relative path.

    Uses the path relative to ``base_dir`` with directory separators
    replaced by hyphens (e.g. ``subdir/redis-failover.md`` →
    ``subdir-redis-failover``). If the file is directly in ``base_dir``,
    returns just the stem (``redis-failover``).
    """
    try:
        rel = filepath.relative_to(base_dir)
    except ValueError:
        return filepath.stem
    stem = rel.with_suffix("")
    parts = stem.parts
    if len(parts) <= 1:
        return parts[0]
    return "-".join(parts)


def _parse_markdown(filepath: Path, doc_id: str | None = None) -> KnowledgeDocument | None:
    """Parse a markdown file into a ``KnowledgeDocument``.

    Args:
        filepath: Path to the markdown file.
        doc_id: Optional document ID override. If ``None``, uses
            ``_make_document_id(filepath, knowledge_dir)`` where
            ``knowledge_dir`` is the parent of the file's common ancestor.
            For simple cases (no subdirs), falls back to ``filepath.stem``.
    """
    if doc_id is None:
        doc_id = filepath.stem

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
        id=doc_id,
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
    for md_file in sorted(knowledge_dir.rglob("*.md")):
        doc_id = _make_document_id(md_file, knowledge_dir)
        doc = _parse_markdown(md_file, doc_id=doc_id)
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


def _create_inmemory_search_tool() -> FunctionTool:
    """Create a ``FunctionTool`` that searches seed documents in memory.

    Reads from the module-level ``_documents_snapshot`` so that the search
    tool always uses the latest indexed documents.
    """

    async def search_handler(**kw: Any) -> str:
        q = kw.get("query", "")
        t = kw.get("top", 5)
        with _documents_lock:
            docs = list(_documents_snapshot)
        hits = _keyword_search(docs, q, t)
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


# ── Chunking utility (for Azure AI Search indexing) ──────────────────────


def _chunk_document(
    doc: KnowledgeDocument,
    max_chars: int = 2000,
) -> list[dict]:
    """Split a ``KnowledgeDocument`` into chunks for Azure AI Search indexing.

    Chunks are split on paragraph boundaries (double newlines) and merged
    until they reach ``max_chars``. Each chunk is a dict with fields:
    ``id``, ``title``, ``category``, ``content``.
    """
    paragraphs = re.split(r'\n\s*\n', doc.content.strip())
    chunks: list[dict] = []
    buffer: list[str] = []
    buffer_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # If this single paragraph exceeds max_chars, split it by words
        if len(para) > max_chars:
            # Flush buffer first
            if buffer:
                chunk_text = '\n\n'.join(buffer)
                chunks.append({
                    'id': f'{doc.id}-chunk-{len(chunks)}',
                    'title': doc.title,
                    'category': doc.category,
                    'content': chunk_text,
                })
                buffer = []
                buffer_len = 0
            # Split oversized paragraph into word-bounded segments
            words = para.split()
            seg_buffer: list[str] = []
            seg_len = 0
            for word in words:
                if seg_len + len(word) + 1 > max_chars and seg_buffer:
                    chunks.append({
                        'id': f'{doc.id}-chunk-{len(chunks)}',
                        'title': doc.title,
                        'category': doc.category,
                        'content': ' '.join(seg_buffer),
                    })
                    seg_buffer = []
                    seg_len = 0
                seg_buffer.append(word)
                seg_len += len(word) + 1
            if seg_buffer:
                chunks.append({
                    'id': f'{doc.id}-chunk-{len(chunks)}',
                    'title': doc.title,
                    'category': doc.category,
                    'content': ' '.join(seg_buffer),
                })
            continue

        # Normal case: add paragraph to buffer
        new_len = buffer_len + len(para) + (2 if buffer else 0)
        if new_len > max_chars and buffer:
            chunk_text = '\n\n'.join(buffer)
            chunks.append({
                'id': f'{doc.id}-chunk-{len(chunks)}',
                'title': doc.title,
                'category': doc.category,
                'content': chunk_text,
            })
            buffer = [para]
            buffer_len = len(para)
        else:
            buffer.append(para)
            buffer_len = new_len

    if buffer:
        chunk_text = '\n\n'.join(buffer)
        chunks.append({
            'id': f'{doc.id}-chunk-{len(chunks)}',
            'title': doc.title,
            'category': doc.category,
            'content': chunk_text,
        })

    return chunks


# ── Re-index API ─────────────────────────────────────────────────────────


def reindex_knowledge_base() -> dict:
    """Re-index all knowledge base documents from ``knowledge/``.

    Detects the active backend:
    - In-memory: reloads markdown files and atomically swaps the document snapshot.
    - Azure AI Search: chunks markdown files and upserts into the search index.

    Returns a dict matching the spec format:
    ``{"status": "ok", "total_documents": N, "success_count": N,
      "error_count": N, "documents": [...], "elapsed_seconds": float,
      "backend": "in_memory" | "azure_ai_search"}``
    """
    global _reindex_in_progress

    if _reindex_in_progress:
        return {
            "status": "error",
            "error": "A re-index operation is already in progress.",
        }

    _reindex_in_progress = True
    t0 = time.time()

    try:
        azure_endpoint = os.environ.get("RAG_SEARCH_ENDPOINT")
        if azure_endpoint:
            result = _reindex_azure_search(azure_endpoint)
        else:
            result = _reindex_in_memory()

        result["elapsed_seconds"] = round(time.time() - t0, 3)
        return result
    finally:
        _reindex_in_progress = False


def _reindex_in_memory() -> dict:
    """Re-index in-memory backend: reload documents and atomically swap."""
    documents = _load_seed_documents()

    doc_results: list[dict] = []
    success_count = 0
    error_count = 0

    # Build per-document status from parsed documents
    knowledge_dir = Path(__file__).resolve().parent.parent / "knowledge"
    if knowledge_dir.is_dir():
        for md_file in sorted(knowledge_dir.rglob("*.md")):
            try:
                file_rel = str(md_file.relative_to(knowledge_dir))
            except ValueError:
                file_rel = md_file.name
            doc_id = _make_document_id(md_file, knowledge_dir)
            doc = _parse_markdown(md_file, doc_id=doc_id)
            if doc is not None:
                doc_results.append({
                    "file": file_rel,
                    "title": doc.title,
                    "category": doc.category,
                    "status": "indexed",
                })
                success_count += 1
            else:
                doc_results.append({
                    "file": file_rel,
                    "title": "",
                    "category": "",
                    "status": "error",
                    "error": f"Failed to parse {md_file.name}",
                })
                error_count += 1

    # Atomically swap the document snapshot
    with _documents_lock:
        _documents_snapshot.clear()
        _documents_snapshot.extend(documents)

    logger.info(
        "RAG: Re-indexed in-memory backend (%d documents, %d errors)",
        success_count,
        error_count,
    )

    result: dict = {
        "status": "ok",
        "total_documents": len(doc_results),
        "success_count": success_count,
        "error_count": error_count,
        "documents": doc_results,
        "backend": "in_memory",
        "warning": None,
    }
    if not doc_results:
        result["warning"] = "No documents found in knowledge/ directory."

    return result


def _reindex_azure_search(azure_endpoint: str) -> dict:
    """Re-index Azure AI Search backend: chunk documents and upsert."""
    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
        from azure.search.documents.models import IndexDocumentsResult

        from app.config import get_azure_credential
    except ImportError as exc:
        logger.error("RAG: Azure AI Search SDK not available: %s", exc)
        return {
            "status": "error",
            "error": f"Azure AI Search SDK not available: {exc}",
            "backend": "azure_ai_search",
        }

    index_name = os.environ.get("RAG_SEARCH_INDEX_NAME", "knowledge-index")
    api_key = os.environ.get("RAG_SEARCH_API_KEY")

    try:
        if api_key:
            credential: AzureKeyCredential = AzureKeyCredential(api_key)
        else:
            credential = get_azure_credential()

        client = SearchClient(
            endpoint=azure_endpoint,
            index_name=index_name,
            credential=credential,
        )
    except Exception as exc:
        logger.error("RAG: Failed to create Azure AI Search client: %s", exc)
        return {
            "status": "error",
            "error": f"Failed to create search client: {exc}",
            "backend": "azure_ai_search",
        }

    knowledge_dir = Path(__file__).resolve().parent.parent / "knowledge"
    if not knowledge_dir.is_dir():
        return {
            "status": "ok",
            "total_documents": 0,
            "success_count": 0,
            "error_count": 0,
            "documents": [],
            "backend": "azure_ai_search",
            "warning": "No documents found in knowledge/ directory.",
        }

    total_chunks = 0
    doc_results: list[dict] = []

    for md_file in sorted(knowledge_dir.rglob("*.md")):
        doc_id = _make_document_id(md_file, knowledge_dir)
        try:
            file_rel = str(md_file.relative_to(knowledge_dir))
        except ValueError:
            file_rel = md_file.name
        doc = _parse_markdown(md_file, doc_id=doc_id)
        if doc is None:
            doc_results.append({
                "file": file_rel,
                "title": "",
                "category": "",
                "status": "error",
                "error": f"Failed to parse {md_file.name}",
            })
            continue

        chunks = _chunk_document(doc)
        if not chunks:
            doc_results.append({
                "file": md_file.name,
                "title": doc.title,
                "category": doc.category,
                "status": "skipped",
            })
            continue

        try:
            upload_result: IndexDocumentsResult = client.upload_documents(documents=chunks)
            succeeded = sum(1 for r in upload_result.results if r.succeeded)
            total_chunks += succeeded
            doc_results.append({
                "file": md_file.name,
                "title": doc.title,
                "category": doc.category,
                "chunks": len(chunks),
                "chunks_indexed": succeeded,
                "status": "indexed" if succeeded == len(chunks) else "partial",
            })
        except Exception as exc:
            logger.error("RAG: Failed to upsert chunks for %s: %s", md_file.name, exc)
            doc_results.append({
                "file": md_file.name,
                "title": doc.title,
                "category": doc.category,
                "status": "error",
                "error": str(exc),
            })

    logger.info(
        "RAG: Re-indexed Azure AI Search backend (%d chunks, %d documents)",
        total_chunks,
        len(doc_results),
    )

    return {
        "status": "ok",
        "total_documents": len(doc_results),
        "success_count": total_chunks,
        "error_count": sum(1 for d in doc_results if d.get("status") == "error"),
        "documents": doc_results,
        "backend": "azure_ai_search",
    }


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

    # Atomically populate the module-level snapshot
    with _documents_lock:
        _documents_snapshot.clear()
        _documents_snapshot.extend(documents)

    tool = _create_inmemory_search_tool()
    logger.info(
        "RAG: In-memory backend ready (%d documents)", len(documents)
    )
    return [tool]
