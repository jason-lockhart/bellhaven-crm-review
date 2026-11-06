from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = Path(
    os.environ.get(
        "BELLHAVEN_DB_PATH",
        APP_DIR / "backend" / "data" / "review.sqlite3",
    )
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS communities (
                slug TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                scraped_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                reviewer_notes TEXT,
                decided_at TEXT,
                crm_write_status TEXT NOT NULL DEFAULT 'not_started',
                crm_write_error TEXT,
                crm_result_account_id TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id TEXT NOT NULL,
                event TEXT NOT NULL,
                detail TEXT,
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )


def save_communities(communities: list[dict]) -> None:
    now = utc_now()
    with connect() as connection:
        connection.executemany(
            """
            INSERT INTO communities (slug, payload_json, scraped_at)
            VALUES (?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
              payload_json = excluded.payload_json,
              scraped_at = excluded.scraped_at
            """,
            [
                (community["slug"], json.dumps(community, sort_keys=True), now)
                for community in communities
            ],
        )
        connection.execute(
            """
            INSERT INTO app_metadata (key, value) VALUES ('last_run_at', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (now,),
        )


def list_communities() -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT payload_json FROM communities ORDER BY json_extract(payload_json, '$.name')"
        ).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def upsert_proposals(proposals: list[dict]) -> tuple[int, int]:
    created = 0
    existing = 0
    now = utc_now()
    with connect() as connection:
        # Only undecided, unstarted proposals may be superseded. Decisions remain immutable.
        active_ids = {p["id"] for p in proposals}
        for row in connection.execute("SELECT id FROM proposals WHERE status = 'pending' AND crm_write_status = 'not_started'").fetchall():
            if row["id"] not in active_ids:
                connection.execute("UPDATE proposals SET status = 'superseded' WHERE id = ?", (row["id"],))
        for proposal in proposals:
            # Legacy decisions used an action-only hash. Keep their decisions even
            # when the newer matcher attaches a richer fact fingerprint.
            historical = connection.execute("SELECT payload_json FROM proposals WHERE status IN ('approved', 'rejected')").fetchall()
            def same_legacy_action(old):
                return (not old.get("matcher_version") and old.get("type") == proposal.get("type")
                    and (old.get("account") or {}).get("id") == (proposal.get("account") or {}).get("id")
                    and (old.get("community") or {}).get("slug") == (proposal.get("community") or {}).get("slug")
                    and old.get("changes") == proposal.get("changes"))
            if any(same_legacy_action(json.loads(row["payload_json"])) for row in historical):
                existing += 1
                continue
            proposal["created_at"] = now
            cursor = connection.execute(
                """
                INSERT INTO proposals (id, type, status, payload_json, created_at)
                VALUES (?, ?, 'pending', ?, ?)
                ON CONFLICT(id) DO UPDATE SET status = 'pending', payload_json = excluded.payload_json
                WHERE proposals.status = 'superseded' AND proposals.crm_write_status = 'not_started'
                """,
                (
                    proposal["id"],
                    proposal["type"],
                    json.dumps(proposal, sort_keys=True),
                    now,
                ),
            )
            if cursor.rowcount:
                created += 1
            else:
                existing += 1
    return created, existing


def _proposal_from_row(row: sqlite3.Row) -> dict:
    payload = json.loads(row["payload_json"])
    payload["status"] = row["status"]
    payload["reviewer_notes"] = row["reviewer_notes"]
    payload["created_at"] = row["created_at"]
    payload["crm_write_status"] = row["crm_write_status"]
    payload["crm_write_error"] = row["crm_write_error"]
    payload["crm_result_account_id"] = row["crm_result_account_id"]
    return payload


def get_proposal(proposal_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
    return _proposal_from_row(row) if row else None


def list_proposals(
    status: str | None = None,
    proposal_type: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[dict], int]:
    clauses: list[str] = []
    values: list[object] = []
    if status:
        clauses.append("status = ?")
        values.append(status)
    if proposal_type:
        clauses.append("type = ?")
        values.append(proposal_type)
    if search:
        clauses.append("(payload_json LIKE ? OR reviewer_notes LIKE ?)")
        pattern = f"%{search}%"
        values.extend([pattern, pattern])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    offset = (page - 1) * page_size
    with connect() as connection:
        total = connection.execute(
            f"SELECT COUNT(*) FROM proposals {where}",
            values,
        ).fetchone()[0]
        rows = connection.execute(
            f"""
            SELECT * FROM proposals {where}
            ORDER BY
              CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
              created_at DESC
            LIMIT ? OFFSET ?
            """,
            [*values, page_size, offset],
        ).fetchall()
    return [_proposal_from_row(row) for row in rows], int(total)


def set_decision(
    proposal_id: str,
    decision: str,
    reviewer_notes: str | None,
    write_status: str | None = None,
    write_error: str | None = None,
    result_account_id: str | None = None,
) -> dict | None:
    decided_at = utc_now()
    with connect() as connection:
        current = connection.execute(
            "SELECT * FROM proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        if not current:
            return None
        connection.execute("INSERT INTO audit_events (proposal_id, event, detail, recorded_at) VALUES (?, ?, ?, ?)",
                           (proposal_id, decision, reviewer_notes, decided_at))
        next_write_status = write_status or current["crm_write_status"]
        next_error = write_error
        if write_status is None and decision == current["status"]:
            next_error = current["crm_write_error"]
        connection.execute(
            """
            UPDATE proposals
            SET status = ?,
                reviewer_notes = ?,
                decided_at = COALESCE(decided_at, ?),
                crm_write_status = ?,
                crm_write_error = ?,
                crm_result_account_id = COALESCE(?, crm_result_account_id)
            WHERE id = ?
            """,
            (
                decision,
                reviewer_notes if reviewer_notes is not None else current["reviewer_notes"],
                decided_at,
                next_write_status,
                next_error,
                result_account_id,
                proposal_id,
            ),
        )
        row = connection.execute(
            "SELECT * FROM proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
    return _proposal_from_row(row)


def set_write_status(
    proposal_id: str,
    status: str,
    error: str | None = None,
    result_account_id: str | None = None,
) -> dict | None:
    with connect() as connection:
        connection.execute("INSERT INTO audit_events (proposal_id, event, detail, recorded_at) VALUES (?, ?, ?, ?)",
                           (proposal_id, status, error, utc_now()))
        connection.execute(
            """
            UPDATE proposals
            SET crm_write_status = ?,
                crm_write_error = ?,
                crm_result_account_id = COALESCE(?, crm_result_account_id)
            WHERE id = ?
            """,
            (status, error, result_account_id, proposal_id),
        )
    return get_proposal(proposal_id)


def summary(total_accounts: int, crm_configured: bool) -> dict:
    with connect() as connection:
        counts = {
            row["status"]: row["count"]
            for row in connection.execute(
                "SELECT status, COUNT(*) AS count FROM proposals GROUP BY status"
            ).fetchall()
        }
        high_risk = connection.execute(
            """
            SELECT COUNT(*) FROM proposals
            WHERE status = 'pending' AND json_extract(payload_json, '$.risk_level') = 'high'
            """
        ).fetchone()[0]
        last_run = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'last_run_at'"
        ).fetchone()
        community_count = connection.execute("SELECT COUNT(*) FROM communities").fetchone()[0]
    return {
        "total_communities": int(community_count),
        "total_accounts": int(total_accounts),
        "pending_count": int(counts.get("pending", 0)),
        "approved_count": int(counts.get("approved", 0)),
        "rejected_count": int(counts.get("rejected", 0)),
        "high_risk_count": int(high_risk),
        "last_run_at": last_run["value"] if last_run else None,
        "crm_configured": crm_configured,
    }


def recent_activity(limit: int = 30) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT id, type, status, payload_json, reviewer_notes, decided_at, crm_write_status
            FROM proposals
            WHERE status IN ('approved', 'rejected') AND decided_at IS NOT NULL
            ORDER BY decided_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    activity = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        activity.append(
            {
                "proposal_id": row["id"],
                "title": payload["title"],
                "decision": row["status"],
                "reviewer_notes": row["reviewer_notes"],
                "crm_write_status": row["crm_write_status"],
                "decided_at": row["decided_at"],
            }
        )
    return activity


def claim_approval(proposal_id: str, notes: str | None) -> bool:
    """Atomic claim across workers. Interrupted writes require explicit investigation."""
    with connect() as connection:
        cursor = connection.execute("""
            UPDATE proposals SET status = 'approved', reviewer_notes = COALESCE(?, reviewer_notes),
              decided_at = COALESCE(decided_at, ?), crm_write_status = 'applying'
            WHERE id = ? AND status IN ('pending', 'approved')
              AND crm_write_status NOT IN ('applying', 'applied')
              AND NOT EXISTS (SELECT 1 FROM proposals WHERE crm_write_status = 'applying')
        """, (notes, utc_now(), proposal_id))
        if cursor.rowcount:
            connection.execute("INSERT INTO audit_events (proposal_id, event, detail, recorded_at) VALUES (?, 'approved', ?, ?)",
                               (proposal_id, notes, utc_now()))
        return cursor.rowcount == 1


def record_rejection(proposal_id: str, notes: str | None) -> bool:
    with connect() as connection:
        cursor = connection.execute("""
            UPDATE proposals SET status = 'rejected', reviewer_notes = ?, decided_at = ?,
              crm_write_status = 'not_applicable'
            WHERE id = ? AND status = 'pending' AND crm_write_status = 'not_started'
        """, (notes, utc_now(), proposal_id))
        if cursor.rowcount:
            connection.execute("INSERT INTO audit_events (proposal_id, event, detail, recorded_at) VALUES (?, 'rejected', ?, ?)",
                               (proposal_id, notes, utc_now()))
        return cursor.rowcount == 1
