"""SQLite storage for WahaSud: sensor readings and the decision journal.

Only parameterised queries are used. One short-lived connection per call keeps
the code safe with the threaded HTTP server.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo("Africa/Casablanca")
except Exception:  # tz database missing (some Windows installs)
    TZ = timezone(timedelta(hours=1))

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    city_id     TEXT NOT NULL,
    hour_utc    TEXT NOT NULL,          -- start of the hour, e.g. 2026-11-23T14:00:00Z
    flow_m3h    REAL NOT NULL,          -- average outflow during that hour
    level_pct   REAL,                   -- tank level (optional)
    received_at TEXT NOT NULL,
    PRIMARY KEY (city_id, hour_utc)
);
CREATE TABLE IF NOT EXISTS decision_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    day        TEXT NOT NULL,           -- local date of the decision
    rec_id     TEXT NOT NULL,
    title      TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('valide', 'reporte', 'annule')),
    decided_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_day ON decision_log (day, rec_id);
"""


def hour_iso(dt: datetime) -> str:
    """UTC start-of-hour timestamp string for any aware datetime."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")


def parse_hour(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:00:00Z").replace(tzinfo=timezone.utc)


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._tx() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _tx(self):
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ----- sensor readings -------------------------------------------------
    def upsert_readings(self, rows) -> int:
        """rows: iterable of (city_id, hour_utc, flow_m3h, level_pct|None). Latest value wins."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        data = [(c, h, f, lv, now) for c, h, f, lv in rows]
        with self._tx() as conn:
            conn.executemany(
                "INSERT INTO readings (city_id, hour_utc, flow_m3h, level_pct, received_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (city_id, hour_utc) DO UPDATE SET "
                "flow_m3h = excluded.flow_m3h, level_pct = excluded.level_pct, received_at = excluded.received_at",
                data,
            )
        return len(data)

    def reset_readings(self, city_id=None) -> int:
        with self._tx() as conn:
            if city_id:
                cur = conn.execute("DELETE FROM readings WHERE city_id = ?", (city_id,))
            else:
                cur = conn.execute("DELETE FROM readings")
            return cur.rowcount

    def sensor_inputs(self, now: datetime) -> dict:
        """Per city: the 24 last complete hours as a flow list indexed by local hour of day.

        Returns {city_id: {"q": [24 floats] | None, "level": float | None,
                           "readings": int, "last": str | None}}.
        "q" is only set when all 24 hours are present, otherwise the engine falls back
        to its simulation for that city.
        """
        end = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(hours=24)
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT city_id, hour_utc, flow_m3h, level_pct FROM readings "
                "WHERE hour_utc >= ? AND hour_utc < ? ORDER BY hour_utc",
                (hour_iso(start), hour_iso(end)),
            ).fetchall()
            last = dict(conn.execute("SELECT city_id, MAX(hour_utc) FROM readings GROUP BY city_id").fetchall())
        by_city: dict = {}
        for city_id, hour_utc, flow, level in rows:
            by_city.setdefault(city_id, []).append((hour_utc, flow, level))
        out = {}
        for city_id, items in by_city.items():
            hours = {}
            level = None
            for hour_utc, flow, lv in items:
                hours[parse_hour(hour_utc).astimezone(TZ).hour] = flow
                if lv is not None:
                    level = lv
            out[city_id] = {
                "q": [hours[h] for h in range(24)] if len(items) == 24 and len(hours) == 24 else None,
                "level": level,
                "readings": len(items),
                "last": last.get(city_id),
            }
        for city_id, ts in last.items():  # cities with readings, none in the window
            out.setdefault(city_id, {"q": None, "level": None, "readings": 0, "last": ts})
        return out

    # ----- decision journal ------------------------------------------------
    def log_decision(self, day: str, rec_id: str, title: str, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO decision_log (day, rec_id, title, status, decided_at) VALUES (?, ?, ?, ?, ?)",
                (day, rec_id, title, status, now),
            )

    def today_statuses(self, day: str) -> dict:
        """Current status of each decision of the day (the latest log entry wins; 'annule' clears it)."""
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT rec_id, status FROM decision_log WHERE id IN "
                "(SELECT MAX(id) FROM decision_log WHERE day = ? GROUP BY rec_id)",
                (day,),
            ).fetchall()
        return {rec_id: status for rec_id, status in rows if status != "annule"}

    def history(self, limit: int = 50) -> list:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id, day, rec_id, title, status, decided_at FROM decision_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        keys = ("id", "day", "rec_id", "title", "status", "decided_at")
        return [dict(zip(keys, r)) for r in rows]
