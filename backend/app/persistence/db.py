"""SQLite persistence (stdlib sqlite3, WAL mode).

All statements are parameterised. Calls are synchronous and guarded by a lock; async code
invokes them through asyncio.to_thread so the event loop is never blocked on disk I/O.
Schema: see docs/ARCHITECTURE.md#persistence.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, config TEXT NOT NULL,
    status TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL, finished_at REAL,
    error TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    variant TEXT NOT NULL, repetition INTEGER NOT NULL, routing_strategy TEXT NOT NULL, cache_policy TEXT NOT NULL,
    started_at REAL NOT NULL, finished_at REAL, summary TEXT, events TEXT);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT, run_id INTEGER, ts REAL NOT NULL,
    object_id TEXT NOT NULL, edge_id TEXT, status_code INTEGER, ok INTEGER NOT NULL, error TEXT,
    latency_ms REAL, resolve_ms REAL, bytes INTEGER NOT NULL DEFAULT 0, cache_status TEXT,
    service_ms REAL, sim_delay_ms REAL);
CREATE TABLE IF NOT EXISTS metric_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, edge_id TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS idx_requests_run ON requests(run_id);
CREATE INDEX IF NOT EXISTS idx_requests_exp ON requests(experiment_id, ts);
CREATE INDEX IF NOT EXISTS idx_runs_exp ON runs(experiment_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON metric_snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_experiments_created ON experiments(created_at);
"""

REQUEST_COLS = ("experiment_id", "run_id", "ts", "object_id", "edge_id", "status_code", "ok", "error",
                "latency_ms", "resolve_ms", "bytes", "cache_status", "service_ms", "sim_delay_ms")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
        self.mark_interrupted()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    # -- settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self._all("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_setting(self, key: str, value: Any) -> None:
        self._exec("INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                   (key, json.dumps(value), time.time()))

    # -- experiments
    def mark_interrupted(self) -> None:
        """Experiments left 'running' by a crashed/killed process cannot resume."""
        self._exec("UPDATE experiments SET status = 'interrupted', finished_at = ? "
                   "WHERE status IN ('running', 'stopping')", (time.time(),))

    def create_experiment(self, exp_id: str, name: str, type_: str, config: dict) -> None:
        self._exec("INSERT INTO experiments(id, name, type, config, status, created_at) VALUES (?, ?, ?, ?, 'created', ?)",
                   (exp_id, name, type_, json.dumps(config), time.time()))

    def update_experiment(self, exp_id: str, **fields: Any) -> None:
        allowed = {"status", "started_at", "finished_at", "error", "summary"}
        cols = [k for k in fields if k in allowed]
        if not cols:
            return
        vals = [json.dumps(fields[k]) if k == "summary" else fields[k] for k in cols]
        self._exec(f"UPDATE experiments SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?", (*vals, exp_id))

    @staticmethod
    def _decode_exp(r: dict) -> dict:
        r["config"] = json.loads(r["config"])
        r["summary"] = json.loads(r["summary"]) if r.get("summary") else None
        return r

    def list_experiments(self, limit: int = 200) -> list[dict]:
        return [self._decode_exp(r) for r in
                self._all("SELECT * FROM experiments ORDER BY created_at DESC LIMIT ?", (limit,))]

    def get_experiment(self, exp_id: str) -> dict | None:
        rows = self._all("SELECT * FROM experiments WHERE id = ?", (exp_id,))
        return self._decode_exp(rows[0]) if rows else None

    def delete_experiment(self, exp_id: str) -> bool:
        with self._lock:
            self._conn.execute("DELETE FROM requests WHERE experiment_id = ?", (exp_id,))
            return self._conn.execute("DELETE FROM experiments WHERE id = ?", (exp_id,)).rowcount > 0

    # -- runs
    def create_run(self, exp_id: str, variant: str, repetition: int, routing: str, cache: str) -> int:
        cur = self._exec("INSERT INTO runs(experiment_id, variant, repetition, routing_strategy, cache_policy, started_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (exp_id, variant, repetition, routing, cache, time.time()))
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, summary: dict, events: list[dict]) -> None:
        self._exec("UPDATE runs SET finished_at = ?, summary = ?, events = ? WHERE id = ?",
                   (time.time(), json.dumps(summary), json.dumps(events), run_id))

    def list_runs(self, exp_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM runs WHERE experiment_id = ? ORDER BY id", (exp_id,))
        for r in rows:
            r["summary"] = json.loads(r["summary"]) if r["summary"] else None
            r["events"] = json.loads(r["events"]) if r["events"] else []
        return rows

    # -- requests
    def insert_requests(self, records: list[dict]) -> None:
        if not records:
            return
        sql = f"INSERT INTO requests({', '.join(REQUEST_COLS)}) VALUES ({', '.join('?' * len(REQUEST_COLS))})"
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(sql, [tuple(r.get(c) for c in REQUEST_COLS) for r in records])
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def run_requests(self, run_id: int) -> list[dict]:
        return self._all("SELECT * FROM requests WHERE run_id = ? ORDER BY ts", (run_id,))

    def experiment_requests(self, exp_id: str) -> list[dict]:
        return self._all("SELECT r.*, u.variant, u.repetition FROM requests r LEFT JOIN runs u ON u.id = r.run_id "
                         "WHERE r.experiment_id = ? ORDER BY r.ts", (exp_id,))

    # -- snapshots & events
    def insert_snapshot(self, payload: dict) -> None:
        self._exec("INSERT INTO metric_snapshots(ts, payload) VALUES (?, ?)", (time.time(), json.dumps(payload)))

    def snapshots(self, since: float, limit: int = 2000) -> list[dict]:
        rows = self._all("SELECT ts, payload FROM metric_snapshots WHERE ts >= ? ORDER BY ts LIMIT ?", (since, limit))
        return [{"ts": r["ts"], **json.loads(r["payload"])} for r in rows]

    def insert_event(self, ts: float, kind: str, edge_id: str | None, detail: dict) -> None:
        self._exec("INSERT INTO events(ts, kind, edge_id, detail) VALUES (?, ?, ?, ?)",
                   (ts, kind, edge_id, json.dumps(detail)))

    def events(self, limit: int = 200) -> list[dict]:
        rows = self._all("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,))
        for r in rows:
            r["detail"] = json.loads(r["detail"]) if r["detail"] else {}
        return rows

    def reset(self) -> None:
        """Delete all experiments, requests, snapshots and events (settings are kept)."""
        with self._lock:
            self._conn.executescript("DELETE FROM requests; DELETE FROM runs; DELETE FROM experiments; "
                                     "DELETE FROM metric_snapshots; DELETE FROM events;")
            self._conn.execute("VACUUM")

    def prune_snapshots(self, older_than_s: float = 7 * 86400) -> None:
        self._exec("DELETE FROM metric_snapshots WHERE ts < ?", (time.time() - older_than_s,))
