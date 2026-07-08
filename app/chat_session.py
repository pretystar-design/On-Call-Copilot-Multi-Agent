"""
In-memory session manager for the chat API.

Maintains per-session conversation history with TTL-based expiry.
Each session stores an ordered list of messages and has a maximum
capacity of 50 messages (25 turns) and 50000 characters total.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

_SESSION_TTL_SECONDS: int = 1800  # 30 minutes
_MAX_MESSAGES_PER_SESSION: int = 50
_MAX_CHARS_PER_SESSION: int = 50000
_CLEANUP_INTERVAL_SECONDS: int = 300  # 5 minutes


# ── Data types ────────────────────────────────────────────────────────


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class ChatSession:
    session_id: str
    messages: List[ChatMessage] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)


# ── Session manager ───────────────────────────────────────────────────


class SessionManager:
    """Manages in-memory chat sessions with TTL-based expiry.

    Thread-safe operations for concurrent access from multiple requests.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()
        self._start_cleanup_thread()

    # ── Public API ──────────────────────────────────────────────────

    def get_or_create_session(self, session_id: str) -> ChatSession:
        """Return an existing session or create a new one."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = ChatSession(session_id=session_id)
                self._sessions[session_id] = session
                logger.debug("Created new chat session: %s", session_id)
            else:
                session.last_active_at = time.time()
            return session

    def add_message(self, session_id: str, role: str, content: str) -> None:
        """Append a message to a session's history, enforcing limits.

        Drops oldest messages (FIFO) when exceeding max count or total
        character limit.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                logger.warning("add_message called for unknown session: %s", session_id)
                return

            session.messages.append(ChatMessage(role=role, content=content))
            session.last_active_at = time.time()

            # Enforce max message count (FIFO eviction)
            while len(session.messages) > _MAX_MESSAGES_PER_SESSION:
                removed = session.messages.pop(0)
                logger.debug(
                    "Dropped oldest message from session %s (exceeded %d msg limit)",
                    session_id,
                    _MAX_MESSAGES_PER_SESSION,
                )

            # Enforce max character count (FIFO eviction)
            total_chars = sum(len(m.content) for m in session.messages)
            while total_chars > _MAX_CHARS_PER_SESSION and len(session.messages) > 1:
                removed = session.messages.pop(0)
                total_chars -= len(removed.content)
                logger.debug(
                    "Dropped oldest message from session %s (exceeded %d char limit)",
                    session_id,
                    _MAX_CHARS_PER_SESSION,
                )

    def get_history(self, session_id: str) -> list[ChatMessage]:
        """Return a copy of the session's message history."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return []
            return list(session.messages)

    def remove_expired(self) -> int:
        """Remove all sessions that have exceeded the TTL.

        Returns the number of sessions removed.
        """
        now = time.time()
        expired_ids: list[str] = []
        with self._lock:
            for sid, session in self._sessions.items():
                if now - session.last_active_at > _SESSION_TTL_SECONDS:
                    expired_ids.append(sid)
            for sid in expired_ids:
                del self._sessions[sid]
        if expired_ids:
            logger.info("Evicted %d expired chat session(s)", len(expired_ids))
        return len(expired_ids)

    # ── Cleanup thread ──────────────────────────────────────────────

    def _start_cleanup_thread(self) -> None:
        """Start a daemon thread that periodically evicts expired sessions."""
        thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        thread.name = "chat-session-cleanup"
        thread.start()
        logger.debug(
            "Chat session cleanup thread started (interval=%ds)",
            _CLEANUP_INTERVAL_SECONDS,
        )

    def _cleanup_loop(self) -> None:
        """Periodic sweep that removes expired sessions."""
        while True:
            time.sleep(_CLEANUP_INTERVAL_SECONDS)
            try:
                self.remove_expired()
            except Exception:
                logger.exception("Chat session cleanup failed")