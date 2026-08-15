-- ============================================================
-- Temporal Conflict Resolution — SQLite Schema
-- ============================================================

CREATE TABLE IF NOT EXISTS transaction_state (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id          TEXT    NOT NULL,
    version                 INTEGER NOT NULL DEFAULT 1,
    source                  TEXT    NOT NULL,
    event_timestamp         TEXT    NOT NULL,
    amount                  REAL,
    currency                TEXT,
    status                  TEXT,
    reconciliation_notes    TEXT,
    confidence_score        REAL,
    event_sequence          INTEGER,
    updated_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(transaction_id)
);

CREATE TABLE IF NOT EXISTS event_log (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    event_hash              TEXT    NOT NULL UNIQUE,
    transaction_id          TEXT    NOT NULL,
    source                  TEXT    NOT NULL,
    event_timestamp         TEXT    NOT NULL,
    amount                  REAL,
    currency                TEXT,
    status                  TEXT,
    reconciliation_notes    TEXT,
    confidence_score        REAL,
    event_sequence          INTEGER,
    is_duplicate            INTEGER NOT NULL DEFAULT 0,
    processed_at            TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS conflict_log (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id          TEXT    NOT NULL,
    conflict_field          TEXT    NOT NULL,
    existing_value          TEXT,
    incoming_value          TEXT,
    existing_source         TEXT,
    incoming_source         TEXT,
    resolution_source       TEXT    NOT NULL,
    resolution_reason       TEXT    NOT NULL,
    resolved_at             TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS version_history (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id          TEXT    NOT NULL,
    version                 INTEGER NOT NULL,
    source                  TEXT    NOT NULL,
    event_timestamp         TEXT    NOT NULL,
    fields_changed          TEXT    NOT NULL,
    snapshot                TEXT    NOT NULL,
    created_at              TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_trail (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id          TEXT    NOT NULL,
    action                  TEXT    NOT NULL,
    details                 TEXT    NOT NULL,
    created_at              TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_event_log_txn       ON event_log(transaction_id);
CREATE INDEX IF NOT EXISTS idx_conflict_log_txn     ON conflict_log(transaction_id);
CREATE INDEX IF NOT EXISTS idx_version_history_txn  ON version_history(transaction_id);
CREATE INDEX IF NOT EXISTS idx_audit_trail_txn      ON audit_trail(transaction_id);
CREATE INDEX IF NOT EXISTS idx_state_txn            ON transaction_state(transaction_id);
