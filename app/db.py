"""SQLite connection and schema.

Three records are kept distinct per AGENTS.md:

- ``method_release`` — immutable method definition (schemas + digests + citation).
- ``deployment``     — maps a release to an operator-owned endpoint + a credential
                       *reference* (never the secret) and execution limits.
- ``run``            — one execution: state, input fingerprint, seed, provider job id,
                       timing, outputs. No sequence/structure content is stored here.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS method_release (
    id                 TEXT PRIMARY KEY,
    version            TEXT NOT NULL,
    input_schema       TEXT NOT NULL,   -- json
    output_schema      TEXT NOT NULL,   -- json
    source_digest      TEXT NOT NULL,
    checkpoint_digest  TEXT NOT NULL,
    worker_image_digest TEXT NOT NULL,
    exec_requirements  TEXT NOT NULL,   -- json
    example_ref        TEXT,
    citation           TEXT NOT NULL,
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deployment (
    id                 TEXT PRIMARY KEY,
    method_release_id  TEXT NOT NULL REFERENCES method_release(id),
    endpoint_ref       TEXT NOT NULL,
    exec_limits        TEXT NOT NULL,   -- json
    credential_ref     TEXT NOT NULL,   -- secret name / id, NOT the secret value
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run (
    id                 TEXT PRIMARY KEY,
    deployment_id      TEXT NOT NULL REFERENCES deployment(id),
    resolved_release_id TEXT NOT NULL REFERENCES method_release(id),
    state              TEXT NOT NULL,   -- queued|running|succeeded|failed|canceled
    input_fingerprint  TEXT NOT NULL,
    idempotency_key    TEXT UNIQUE,
    seed               INTEGER,
    inputs             TEXT NOT NULL,   -- json: validated input values
    provider_job_id    TEXT,
    submitted_at       TEXT,
    finished_at        TEXT,
    error_category     TEXT,            -- category only; never message content
    outputs_ref        TEXT
);

CREATE INDEX IF NOT EXISTS idx_run_state ON run(state);
CREATE INDEX IF NOT EXISTS idx_deployment_release ON deployment(method_release_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: Path) -> None:
    """Create tables if absent. Idempotent."""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


@contextmanager
def get_conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
