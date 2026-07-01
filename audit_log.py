"""SQLite-backed audit log for provenance decisions.

Every submission is persisted here BEFORE a response is returned, so nothing
shown to a user is ever un-logged. Milestone 4 extends this table with appeal
records and status transitions, so entries are keyed by content_id for lookup.
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

DB_PATH = Path(os.getenv("AUDIT_LOG_DB", "audit_log.db"))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the audit_log and appeals tables if they do not already exist."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id   TEXT    NOT NULL,
                creator_id   TEXT,
                timestamp    TEXT    NOT NULL,
                attribution  TEXT,
                confidence   REAL,
                llm_score    REAL,
                status       TEXT    NOT NULL DEFAULT 'classified'
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_log_content_id "
            "ON audit_log (content_id)"
        )
        # Appeals live in their own table but reference the original decision by
        # content_id, so an appeal is always logged alongside the decision it
        # contests rather than replacing it.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS appeals (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id    TEXT    NOT NULL,
                submitter_id  TEXT,
                timestamp     TEXT    NOT NULL,
                reason        TEXT    NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_appeals_content_id "
            "ON appeals (content_id)"
        )


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with millisecond precision."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def log_submission(
    *,
    content_id: str,
    creator_id: str,
    attribution: str,
    confidence: float,
    llm_score: float,
    status: str = "classified",
    timestamp: str = None,
) -> Dict[str, Any]:
    """Persist a structured decision record and return it.

    Args:
        content_id: Unique ID assigned to the submitted content.
        creator_id: The account that submitted the content.
        attribution: The attribution result (likely_ai | likely_human | uncertain).
        confidence: Calibrated confidence score, 0.0-1.0.
        llm_score: Signal 1 (Groq/LLM) score, 0.0-1.0.
        status: Lifecycle status of the record. Defaults to "classified".
        timestamp: ISO 8601 timestamp. Defaults to the current UTC time.
    """
    entry: Dict[str, Any] = {
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": timestamp or utc_now_iso(),
        "attribution": attribution,
        "confidence": confidence,
        "llm_score": llm_score,
        "status": status,
    }

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO audit_log
                (content_id, creator_id, timestamp, attribution, confidence, llm_score, status)
            VALUES
                (:content_id, :creator_id, :timestamp, :attribution, :confidence, :llm_score, :status)
            """,
            entry,
        )

    return entry


def get_decision(content_id: str) -> Dict[str, Any]:
    """Return the original decision record for a content_id, or None if unknown."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT content_id, creator_id, timestamp, attribution, confidence, llm_score, status
            FROM audit_log
            WHERE content_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (content_id,),
        ).fetchone()

    return dict(row) if row else None


def log_appeal(
    *,
    content_id: str,
    submitter_id: str,
    reason: str,
    timestamp: str = None,
) -> Dict[str, Any]:
    """Record a creator's appeal and flip the decision's status to "under review".

    The appeal is inserted into the appeals table (keyed by content_id, so it sits
    alongside the original decision) and the matching audit_log row's status is
    updated. The original decision is preserved untouched — no re-classification.

    Returns a reviewer-queue view (original decision + appeal) or None if the
    content_id has no prior decision to contest.
    """
    original = get_decision(content_id)
    if original is None:
        return None

    ts = timestamp or utc_now_iso()
    appeal = {
        "content_id": content_id,
        "submitter_id": submitter_id,
        "timestamp": ts,
        "reason": reason,
    }

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO appeals (content_id, submitter_id, timestamp, reason)
            VALUES (:content_id, :submitter_id, :timestamp, :reason)
            """,
            appeal,
        )
        conn.execute(
            "UPDATE audit_log SET status = 'under review' WHERE content_id = ?",
            (content_id,),
        )

    original["status"] = "under review"
    return {
        "content_id": content_id,
        "status": "under review",
        "appeal": appeal,
        "original_decision": original,
    }


def get_appeals(limit: int = 50) -> list:
    """Return the most recent appeals, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT content_id, submitter_id, timestamp, reason
            FROM appeals
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_log(limit: int = 50) -> list:
    """Return the most recent audit log entries, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT content_id, creator_id, timestamp, attribution, confidence, llm_score, status
            FROM audit_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [dict(row) for row in rows]
