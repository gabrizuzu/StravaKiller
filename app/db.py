"""SQLite: una connessione per operazione, WAL per letture concorrenti alla sync."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
  id TEXT PRIMARY KEY, source TEXT NOT NULL, start_local TEXT NOT NULL, type TEXT NOT NULL,
  name TEXT, distance_m REAL, duration_s REAL, moving_s REAL, avg_hr REAL, max_hr REAL,
  cadence REAL, elev_gain REAL, calories REAL, vo2max REAL, garmin_load REAL, te_aerobic REAL,
  hr_hist TEXT, stream TEXT, fit_done INTEGER NOT NULL DEFAULT 0, raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_activities_start ON activities(start_local);
CREATE TABLE IF NOT EXISTS best_efforts (
  activity_id TEXT NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
  distance_m INTEGER NOT NULL, time_s REAL NOT NULL, origin TEXT NOT NULL DEFAULT 'fit',
  PRIMARY KEY (activity_id, distance_m)
);
CREATE INDEX IF NOT EXISTS idx_efforts ON best_efforts(distance_m, time_s);
CREATE TABLE IF NOT EXISTS splits (
  activity_id TEXT NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
  km INTEGER NOT NULL, time_s REAL NOT NULL, avg_hr REAL, elev_gain REAL,
  PRIMARY KEY (activity_id, km)
);
CREATE TABLE IF NOT EXISTS daily (
  date TEXT PRIMARY KEY, steps REAL, resting_hr REAL, stress REAL, sleep_s REAL, sleep_score REAL,
  hrv REAL, bb_high REAL, bb_low REAL, weight_kg REAL, spo2 REAL, calories REAL
);
CREATE TABLE IF NOT EXISTS daily_raw (
  date TEXT NOT NULL, kind TEXT NOT NULL, json TEXT NOT NULL, PRIMARY KEY (date, kind)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS chat_messages (
  conversation_id TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
  PRIMARY KEY (conversation_id, seq)
);
"""

DAILY_COLUMNS = {"steps", "resting_hr", "stress", "sleep_s", "sleep_score", "hrv",
                 "bb_high", "bb_low", "weight_kg", "spo2", "calories"}


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA)


def get_setting(key: str, default=None):
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value) -> None:
    with connect() as c:
        if value is None:
            c.execute("DELETE FROM settings WHERE key=?", (key,))
        else:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))


def upsert_daily(conn: sqlite3.Connection, day: str, values: dict) -> None:
    clean = {k: v for k, v in values.items() if v is not None and k in DAILY_COLUMNS}
    if not clean:
        return
    cols = ",".join(clean)
    marks = ",".join("?" for _ in clean)
    updates = ",".join(f"{k}=excluded.{k}" for k in clean)
    conn.execute(f"INSERT INTO daily(date,{cols}) VALUES(?,{marks}) "
                 f"ON CONFLICT(date) DO UPDATE SET {updates}", (day, *clean.values()))
