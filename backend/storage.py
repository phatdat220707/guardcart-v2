"""SQLite store with explicit transactions and per-operation connections."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from backend.services.errors import StorageBusy


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY CHECK (version = 1));
INSERT OR IGNORE INTO schema_version(version) VALUES (1);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS merchants (
    id TEXT PRIMARY KEY, version INTEGER NOT NULL CHECK(version > 0), data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL REFERENCES merchants(id),
    version INTEGER NOT NULL CHECK(version > 0), data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY, version INTEGER NOT NULL CHECK(version > 0), data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mandates (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
    version INTEGER NOT NULL CHECK(version > 0),
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','CONSUMED','REVOKED','SUPERSEDED')),
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotes (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
    mandate_id TEXT NOT NULL REFERENCES mandates(id), status TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policy_decisions (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
    quote_id TEXT NOT NULL REFERENCES quotes(id), data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
    quote_id TEXT UNIQUE NOT NULL REFERENCES quotes(id),
    mandate_id TEXT NOT NULL REFERENCES mandates(id), data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
    quote_id TEXT UNIQUE NOT NULL REFERENCES quotes(id),
    mandate_id TEXT UNIQUE NOT NULL REFERENCES mandates(id),
    idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, data TEXT NOT NULL,
    UNIQUE(session_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
    session_id TEXT NOT NULL REFERENCES sessions(id), data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS quote_owner ON quotes(session_id, mandate_id);
CREATE INDEX IF NOT EXISTS decision_quote ON policy_decisions(quote_id, sequence);
CREATE INDEX IF NOT EXISTS audit_owner ON audit_events(session_id, sequence);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
    draft_json TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_runs (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
    request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, kind TEXT NOT NULL,
    conversation_id TEXT REFERENCES conversations(id), state TEXT NOT NULL,
    requested_model TEXT NOT NULL, created_at TEXT NOT NULL, lease_until TEXT NOT NULL,
    result_json TEXT, error_code TEXT, UNIQUE(session_id, request_id)
);
CREATE TABLE IF NOT EXISTS ai_calls (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES ai_runs(id),
    session_id TEXT NOT NULL REFERENCES sessions(id), state TEXT NOT NULL,
    created_at TEXT NOT NULL, usage_json TEXT, elapsed_ms INTEGER, error_code TEXT
);
CREATE INDEX IF NOT EXISTS ai_calls_owner ON ai_calls(session_id, created_at);
CREATE INDEX IF NOT EXISTS ai_runs_owner ON ai_runs(session_id, state);
"""


class Database:
    def __init__(self, path: str | Path):
        if str(path) == ":memory:":
            raise ValueError("Use a file database: operations use independent connections.")
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
        finally:
            connection.close()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def transaction(self, *, write: bool = False):
        connection = self._connect()
        try:
            # Take the writer lock before reading any authorization state.
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except sqlite3.OperationalError as exc:
            connection.rollback()
            if getattr(exc, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise StorageBusy() from exc
            raise
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
