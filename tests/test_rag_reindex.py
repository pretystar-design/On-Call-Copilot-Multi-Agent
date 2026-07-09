"""Tests for app.rag reindex_knowledge_base() and chunking logic."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from app.rag import (
    KnowledgeDocument,
    _chunk_document,
    _keyword_search,
    _make_document_id,
    _parse_markdown,
    reindex_knowledge_base,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def knowledge_dir(tmp_path: Path) -> Path:
    """Create a temporary knowledge directory with sample markdown files in
    both root and subdirectories."""
    kd = tmp_path / "knowledge"
    kd.mkdir()

    # Root-level file
    (kd / "redis-failover.md").write_text(
        "# Redis Failover Procedure\n"
        "\n"
        "**Category:** Runbook\n"
        "\n"
        "## Steps\n"
        "\n"
        "1. Identify the Redis master node.\n"
        "2. Run `redis-cli FAILOVER` on the replica.\n"
        "3. Verify replication status.\n"
        "4. Update DNS records if needed.\n"
    )

    # File in a subdirectory
    sub = kd / "subdir"
    sub.mkdir()
    (sub / "tls-cert-rotation.md").write_text(
        "# TLS Certificate Rotation Incident\n"
        "\n"
        "**Category:** Post-Incident Review\n"
        "\n"
        "## Timeline\n"
        "\n"
        "The TLS certificate for api.example.com expired on 2025-03-15.\n"
        "Automated monitoring alerted the SRE team within 5 minutes.\n"
        "\n"
        "## Root Cause\n"
        "\n"
        "The certificate renewal cron job had been silently failing due to\n"
        "a permissions change in the secrets management system.\n"
        "\n"
        "## Resolution\n"
        "\n"
        "Manual certificate rotation was performed. The cron job was fixed\n"
        "and tested to ensure future renewals work automatically.\n"
    )

    return kd


# ── _make_document_id tests ───────────────────────────────────────────────────


class TestMakeDocumentId:
    def test_root_level_file(self) -> None:
        base = Path("/knowledge")
        f = base / "redis-failover.md"
        assert _make_document_id(f, base) == "redis-failover"

    def test_subdirectory_file(self) -> None:
        base = Path("/knowledge")
        f = base / "Taiji-SRE" / "runbook.md"
        assert _make_document_id(f, base) == "Taiji-SRE-runbook"

    def test_deeply_nested_file(self) -> None:
        base = Path("/knowledge")
        f = base / "a" / "b" / "c" / "doc.md"
        assert _make_document_id(f, base) == "a-b-c-doc"

    def test_file_outside_base(self) -> None:
        base = Path("/knowledge")
        f = Path("/other/doc.md")
        assert _make_document_id(f, base) == "doc"


# ── _parse_markdown tests ─────────────────────────────────────────────────────


def test_parse_markdown_extracts_title_and_category(tmp_path: Path) -> None:
    md_file = tmp_path / "test.md"
    md_file.write_text(
        "# My Document Title\n"
        "\n"
        "**Category:** Runbook\n"
        "\n"
        "Some content here.\n"
    )
    doc = _parse_markdown(md_file)
    assert doc is not None
    assert doc.title == "My Document Title"
    assert doc.category == "Runbook"
    assert "Some content here." in doc.content


def test_parse_markdown_missing_file(tmp_path: Path) -> None:
    md_file = tmp_path / "nonexistent.md"
    doc = _parse_markdown(md_file)
    assert doc is None


def test_parse_markdown_defaults(tmp_path: Path) -> None:
    md_file = tmp_path / "untitled.md"
    md_file.write_text("Just some plain text without headers.\n")
    doc = _parse_markdown(md_file)
    assert doc is not None
    assert doc.title == "Untitled"
    assert doc.category == "General"


def test_parse_markdown_with_doc_id(tmp_path: Path) -> None:
    md_file = tmp_path / "test.md"
    md_file.write_text("# Title\n\nContent.\n")
    doc = _parse_markdown(md_file, doc_id="custom-id")
    assert doc is not None
    assert doc.id == "custom-id"


# ── _keyword_search tests ─────────────────────────────────────────────────────


def test_keyword_search_finds_relevant_document() -> None:
    docs = [
        KnowledgeDocument("doc1", "Redis Failover", "Runbook", "Steps to failover Redis."),
        KnowledgeDocument("doc2", "TLS Rotation", "Runbook", "How to rotate TLS certificates."),
    ]
    results = _keyword_search(docs, "redis failover", 5)
    assert len(results) == 1
    assert results[0][0].id == "doc1"


def test_keyword_search_returns_top_k() -> None:
    docs = [
        KnowledgeDocument("a", "Alpha", "General", "alpha beta gamma"),
        KnowledgeDocument("b", "Beta", "General", "beta gamma delta"),
        KnowledgeDocument("c", "Gamma", "General", "gamma delta epsilon"),
    ]
    results = _keyword_search(docs, "beta", 2)
    assert len(results) == 2
    # "beta" appears 2× in doc b (title + content) and 1× in doc a (content only)
    assert results[0][0].id == "b"


def test_keyword_search_no_match() -> None:
    docs = [
        KnowledgeDocument("doc1", "Redis", "Runbook", "Redis content."),
    ]
    results = _keyword_search(docs, "kubernetes", 5)
    assert results == []


# ── _chunk_document tests ─────────────────────────────────────────────────────


def test_chunk_document_single_paragraph() -> None:
    doc = KnowledgeDocument("test", "Test Doc", "General", "This is a single paragraph.")
    chunks = _chunk_document(doc, max_chars=2000)
    assert len(chunks) == 1
    assert chunks[0]["id"] == "test-chunk-0"
    assert chunks[0]["title"] == "Test Doc"
    assert chunks[0]["category"] == "General"
    assert "single paragraph" in chunks[0]["content"]


def test_chunk_document_splits_at_paragraph_boundaries() -> None:
    content = "\n\n".join([f"Paragraph number {i} with some text." for i in range(10)])
    doc = KnowledgeDocument("multi", "Multi Para", "Guide", content)
    chunks = _chunk_document(doc, max_chars=100)
    assert len(chunks) >= 3
    for chunk in chunks:
        assert chunk["id"].startswith("multi-chunk-")
        assert chunk["title"] == "Multi Para"
        assert chunk["category"] == "Guide"
        assert len(chunk["content"]) <= 200


def test_chunk_document_oversized_paragraph() -> None:
    """A single paragraph exceeding max_chars should be word-split."""
    long_text = "word " * 500  # ~2500 chars
    doc = KnowledgeDocument("long", "Long Doc", "General", long_text.strip())
    chunks = _chunk_document(doc, max_chars=500)
    assert len(chunks) >= 3
    for chunk in chunks:
        assert chunk["id"].startswith("long-chunk-")
        assert len(chunk["content"]) <= 500


def test_chunk_document_id_pattern() -> None:
    doc = KnowledgeDocument("my-doc", "My Doc", "Runbook", "Para 1\n\nPara 2\n\nPara 3\n\nPara 4")
    chunks = _chunk_document(doc, max_chars=50)
    for i, chunk in enumerate(chunks):
        assert chunk["id"] == f"my-doc-chunk-{i}"


# ── reindex_knowledge_base (in-memory path) tests ────────────────────────────


def test_load_seed_documents_with_subfolders(knowledge_dir: Path) -> None:
    """Verify _load_seed_documents recursively picks up files in subdirectories."""
    from app.rag import _load_seed_documents
    docs = _load_seed_documents(knowledge_dir)
    assert len(docs) == 2, f"Expected 2 docs (root + subdir), got {len(docs)}"
    ids = [d.id for d in docs]
    assert "redis-failover" in ids
    assert "subdir-tls-cert-rotation" in ids, f"Expected subdir-tls-cert-rotation in {ids}"


def test_reindex_atomic_swap() -> None:
    """Verify document list can be swapped without errors."""
    from app.rag import _documents_lock, _documents_snapshot

    initial_docs = [KnowledgeDocument("a", "A", "General", "content a")]
    new_docs = [KnowledgeDocument("b", "B", "General", "content b")]

    with _documents_lock:
        _documents_snapshot.clear()
        _documents_snapshot.extend(initial_docs)
        assert len(_documents_snapshot) == 1

    with _documents_lock:
        _documents_snapshot.clear()
        _documents_snapshot.extend(new_docs)
        assert len(_documents_snapshot) == 1
        assert _documents_snapshot[0].id == "b"


def test_keyword_search_after_snapshot_swap() -> None:
    """Verify search works correctly after snapshot swap."""
    from app.rag import _documents_lock, _documents_snapshot

    docs_a = [KnowledgeDocument("a", "Alpha Doc", "Runbook", "alpha content")]
    docs_b = [KnowledgeDocument("b", "Beta Doc", "Runbook", "beta content")]

    with _documents_lock:
        _documents_snapshot.clear()
        _documents_snapshot.extend(docs_a)

    hits_a = _keyword_search(list(_documents_snapshot), "alpha", 5)
    assert len(hits_a) == 1

    with _documents_lock:
        _documents_snapshot.clear()
        _documents_snapshot.extend(docs_b)

    hits_b = _keyword_search(list(_documents_snapshot), "alpha", 5)
    assert len(hits_b) == 0

    hits_beta = _keyword_search(list(_documents_snapshot), "beta", 5)
    assert len(hits_beta) == 1


# ── Re-index concurrency test ────────────────────────────────────────────────


def test_reindex_concurrent_rejection() -> None:
    """Concurrent calls to reindex_knowledge_base should be rejected."""
    import app.rag as rag_module

    # Reset state
    rag_module._reindex_in_progress = False

    # First call should succeed
    result1 = rag_module.reindex_knowledge_base()
    assert result1["status"] in ("ok", "error")  # may error if no docs

    # Manually set flag to simulate in-progress
    rag_module._reindex_in_progress = True
    result2 = rag_module.reindex_knowledge_base()
    assert result2["status"] == "error"
    assert "already in progress" in result2.get("error", "").lower()

    # Clean up
    rag_module._reindex_in_progress = False
