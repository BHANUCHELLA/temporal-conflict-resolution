"""
SQLite persistence layer.

Handles the five tables defined in schema.sql:
  transaction_state  - current authoritative state per transaction (1 row each)
  event_log          - every event ever received, keyed by a content hash (idempotency)
  conflict_log        - every field-level conflict detected and how it was resolved
  version_history     - append-only history of every accepted state change
  audit_trail          - human-readable narrative of every decision made

Deliberately dependency-free (Python stdlib `sqlite3` only), per the PRD's
constraint of "no additional libraries beyond standard Python + cx_Oracle".
"""

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from .models import TransactionState

_DEFAULT_SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql"
)


class Database:
    def __init__(self, path: str = ":memory:", schema_path: Optional[str] = None):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;")
        schema_path = schema_path or _DEFAULT_SCHEMA_PATH
        with open(schema_path, "r", encoding="utf-8") as f:
            self.conn.executescript(f.read())
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # event_log / idempotency
    # ------------------------------------------------------------------ #
    def log_event(self, event, event_hash: str, is_duplicate: bool = False) -> bool:
        """Insert into event_log. Returns False (and does nothing else) if
        event_hash already exists, i.e. this is a duplicate event."""
        try:
            self.conn.execute(
                """INSERT INTO event_log
                   (event_hash, transaction_id, source, event_timestamp, amount,
                    currency, status, reconciliation_notes, confidence_score,
                    event_sequence, is_duplicate)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_hash, event.transaction_id, event.source, event.timestamp,
                    event.amount, event.currency, event.status,
                    event.reconciliation_notes, event.confidence_score,
                    event.event_sequence, int(is_duplicate),
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def count_events(self, transaction_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM event_log WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------ #
    # transaction_state
    # ------------------------------------------------------------------ #
    def get_state(self, transaction_id: str) -> Optional[TransactionState]:
        row = self.conn.execute(
            "SELECT * FROM transaction_state WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
        if row is None:
            return None
        return TransactionState(
            transaction_id=row["transaction_id"],
            version=row["version"],
            source=row["source"],
            timestamp=row["event_timestamp"],
            amount=row["amount"],
            currency=row["currency"],
            status=row["status"],
            reconciliation_notes=row["reconciliation_notes"],
            confidence_score=row["confidence_score"],
            event_sequence=row["event_sequence"],
        )

    def save_state(self, state: TransactionState) -> None:
        self.conn.execute(
            """INSERT INTO transaction_state
               (transaction_id, version, source, event_timestamp, amount, currency,
                status, reconciliation_notes, confidence_score, event_sequence, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?, datetime('now'))
               ON CONFLICT(transaction_id) DO UPDATE SET
                 version=excluded.version,
                 source=excluded.source,
                 event_timestamp=excluded.event_timestamp,
                 amount=excluded.amount,
                 currency=excluded.currency,
                 status=excluded.status,
                 reconciliation_notes=excluded.reconciliation_notes,
                 confidence_score=excluded.confidence_score,
                 event_sequence=excluded.event_sequence,
                 updated_at=datetime('now')""",
            (
                state.transaction_id, state.version, state.source, state.timestamp,
                state.amount, state.currency, state.status, state.reconciliation_notes,
                state.confidence_score, state.event_sequence,
            ),
        )
        self.conn.commit()

    def get_all_states(self) -> List[TransactionState]:
        rows = self.conn.execute(
            "SELECT transaction_id FROM transaction_state ORDER BY transaction_id"
        ).fetchall()
        return [self.get_state(r["transaction_id"]) for r in rows]

    # ------------------------------------------------------------------ #
    # conflict_log
    # ------------------------------------------------------------------ #
    def log_conflict(self, transaction_id, field_name, existing_value, incoming_value,
                      existing_source, incoming_source, resolution_source, resolution_reason):
        self.conn.execute(
            """INSERT INTO conflict_log
               (transaction_id, conflict_field, existing_value, incoming_value,
                existing_source, incoming_source, resolution_source, resolution_reason)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                transaction_id, field_name, str(existing_value), str(incoming_value),
                existing_source, incoming_source, resolution_source, resolution_reason,
            ),
        )
        self.conn.commit()

    def count_conflicts(self, transaction_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM conflict_log WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------ #
    # version_history
    # ------------------------------------------------------------------ #
    def log_version(self, transaction_id, version, source, timestamp,
                     fields_changed: List[str], snapshot: Dict[str, Any]):
        self.conn.execute(
            """INSERT INTO version_history
               (transaction_id, version, source, event_timestamp, fields_changed, snapshot)
               VALUES (?,?,?,?,?,?)""",
            (
                transaction_id, version, source, timestamp,
                json.dumps(fields_changed), json.dumps(snapshot, default=str),
            ),
        )
        self.conn.commit()

    def get_version_history(self, transaction_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT version, source, event_timestamp, fields_changed, snapshot, created_at
               FROM version_history WHERE transaction_id = ? ORDER BY version ASC""",
            (transaction_id,),
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                "version": r["version"],
                "source": r["source"],
                "timestamp": r["event_timestamp"],
                "fields_changed": json.loads(r["fields_changed"]),
                "snapshot": json.loads(r["snapshot"]),
                "created_at": r["created_at"],
            })
        return out

    # ------------------------------------------------------------------ #
    # audit_trail
    # ------------------------------------------------------------------ #
    def log_audit(self, transaction_id: str, action: str, details: Dict[str, Any]):
        self.conn.execute(
            "INSERT INTO audit_trail (transaction_id, action, details) VALUES (?,?,?)",
            (transaction_id, action, json.dumps(details, default=str)),
        )
        self.conn.commit()

    def get_audit_trail(self, transaction_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT action, details, created_at FROM audit_trail
               WHERE transaction_id = ? ORDER BY id ASC""",
            (transaction_id,),
        ).fetchall()
        return [
            {"action": r["action"], "details": json.loads(r["details"]), "created_at": r["created_at"]}
            for r in rows
        ]

    def get_conflicts(self, transaction_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT conflict_field, existing_value, incoming_value, existing_source,
                      incoming_source, resolution_source, resolution_reason, resolved_at
               FROM conflict_log WHERE transaction_id = ? ORDER BY id ASC""",
            (transaction_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.conn.close()
