"""M0 smoke tests: app boots, DB initializes, stub routes respond."""

from __future__ import annotations

import sqlite3

from app.config import Settings
from app.db import init_db


def test_index_responds(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "gRNAde" in resp.text
    assert "Submit run" in resp.text


def test_health_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_db_init_creates_three_tables(settings: Settings):
    init_db(settings.db_path)
    conn = sqlite3.connect(settings.db_path)
    try:
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert {"method_release", "deployment", "run"} <= names
