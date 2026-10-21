"""Database access for Canon (SQLite).

One file database. On first start the schema is created and demo data is
seeded, so a fresh deploy is usable immediately.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone

from flask import g

DB_PATH = os.environ.get("CANON_DB", os.path.join(os.path.dirname(os.path.dirname(__file__)), "canon.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, name TEXT, email TEXT, role TEXT, team TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS rules (
  id TEXT PRIMARY KEY, type TEXT, title TEXT, text TEXT,
  pattern TEXT, replacement TEXT, trigger TEXT,
  severity TEXT, active INTEGER DEFAULT 1, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS facts (
  id TEXT PRIMARY KEY, label TEXT, value TEXT, keyword TEXT, value_pattern TEXT,
  source TEXT, owner TEXT, expires_on TEXT, visibility TEXT,
  version INTEGER DEFAULT 1, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS fact_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, fact_id TEXT, old_value TEXT,
  new_value TEXT, changed_by TEXT, changed_at TEXT
);
CREATE TABLE IF NOT EXISTS content (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, channel TEXT,
  author_id TEXT, status TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, content_id INTEGER, n INTEGER,
  body TEXT, note TEXT, created_by TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, content_id INTEGER, version_n INTEGER,
  quote TEXT, issue TEXT, ref TEXT, method TEXT, severity TEXT,
  confidence REAL, intent TEXT, fix TEXT, action TEXT DEFAULT 'replace', needs_review INTEGER DEFAULT 0,
  status TEXT DEFAULT 'open', created_at TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, finding_id INTEGER, content_id INTEGER,
  rule_ref TEXT, action TEXT, reason TEXT, user_id TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, content_id INTEGER, user_id TEXT,
  body TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS claim_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT, content_id INTEGER, fact_id TEXT,
  quote TEXT, value TEXT
);
CREATE TABLE IF NOT EXISTS rule_rewrites (
  id INTEGER PRIMARY KEY AUTOINCREMENT, rule_id TEXT, proposal TEXT,
  summary TEXT, status TEXT, created_at TEXT, decided_by TEXT
);
CREATE TABLE IF NOT EXISTS check_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, content_id INTEGER, version_n INTEGER,
  ai_status TEXT, ai_dropped INTEGER DEFAULT 0, created_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, action TEXT,
  detail TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS reliability_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT, progress INTEGER,
  total INTEGER, with_ai INTEGER, results_json TEXT, summary_json TEXT,
  created_at TEXT, finished_at TEXT
);
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def q(conn, sql, params=(), one=False):
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def ex(conn, sql, params=()):
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.lastrowid


def setting(conn, key, default=None):
    row = q(conn, "SELECT value FROM settings WHERE key=?", (key,), one=True)
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
        return row["value"]


def set_setting(conn, key, value):
    ex(conn, "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
       (key, json.dumps(value)))


def audit(conn, user_id, action, detail=""):
    ex(conn, "INSERT INTO audit_log(user_id,action,detail,created_at) VALUES(?,?,?,?)",
       (user_id, action, detail, now()))


def latest_version(conn, content_id):
    return q(conn, "SELECT * FROM versions WHERE content_id=? ORDER BY n DESC LIMIT 1", (content_id,), one=True)


def add_version(conn, content_id, body, user_id, note=""):
    cur = latest_version(conn, content_id)
    n = (cur["n"] + 1) if cur else 1
    ex(conn, "INSERT INTO versions(content_id,n,body,note,created_by,created_at) VALUES(?,?,?,?,?,?)",
       (content_id, n, body, note, user_id, now()))
    ex(conn, "UPDATE content SET updated_at=? WHERE id=?", (now(), content_id))
    return n


def visible_facts(conn, role):
    """Agency users never see internal facts, in the UI or in AI context."""
    if role == "agency":
        return q(conn, "SELECT * FROM facts WHERE visibility!='internal' ORDER BY id")
    return q(conn, "SELECT * FROM facts ORDER BY id")
