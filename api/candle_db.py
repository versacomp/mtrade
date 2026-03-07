"""SQLite candle store — supports any symbol/interval combination.

Schema:
  candles(symbol, interval, ts_ms, open, high, low, close, volume, source, recorded_ms)
  Unique key: (symbol, interval, ts_ms) — INSERT OR REPLACE deduplicates live updates.

Usage:
  from api.candle_db import get_db
  db = get_db()
  db.insert("MES", "1m", candle_dict)
  rows = db.query("MES", "1m", from_ms=..., to_ms=...)
  # rows is list[dict] with keys: time, open, high, low, close, volume, source
"""
import logging
import sqlite3
import time
from pathlib import Path

import config

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".mtrade" / "candles.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS candles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    interval    TEXT    NOT NULL,
    ts_ms       INTEGER NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      REAL,
    source      TEXT    NOT NULL DEFAULT 'live',
    recorded_ms INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_candles ON candles(symbol, interval, ts_ms);
CREATE INDEX IF NOT EXISTS idx_sym_int_ts  ON candles(symbol, interval, ts_ms);
"""

_instance: "CandleDB | None" = None


def get_db() -> "CandleDB":
    """Return the module-level singleton, creating it on first call."""
    global _instance
    if _instance is None:
        _instance = CandleDB()
    return _instance


def reset_db() -> None:
    """Close and discard the singleton (call after the db_path pref changes)."""
    global _instance
    if _instance is not None:
        try:
            _instance.close()
        except Exception:
            pass
        _instance = None


class CandleDB:
    """
    SQLite-backed candle store.

    Manages a single connection to the configured database file and exposes
    high-level read/write helpers for OHLCV candle data.  The schema uses a
    composite unique key ``(symbol, interval, ts_ms)`` so that ``INSERT OR
    REPLACE`` naturally deduplicates live streaming updates.

    Typical usage::

        db = get_db()
        db.insert("MES", "1m", candle_dict)
        rows = db.query("MES", "1m", from_ms=..., to_ms=...)
    """

    def __init__(self, db_path: Path | None = None) -> None:
        """
        Open (or create) the SQLite database at *db_path*.

        If *db_path* is ``None``, the path is read from the ``candle_db_path``
        user preference, falling back to ``DEFAULT_DB_PATH``.  Parent directories
        are created automatically.
        """
        if db_path is None:
            raw = config.get_pref("candle_db_path", "")
            db_path = Path(raw) if raw else DEFAULT_DB_PATH
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(_CREATE_SQL)
        self._conn.commit()
        log.info("CandleDB opened: %s", db_path)

    # ── Write ──────────────────────────────────────────────────────────────────

    def insert(self, symbol: str, interval: str, candle: dict, source: str = "live") -> None:
        """Insert or replace a candle.  `candle` must contain a 'time' key in ms."""
        ts_ms = int(candle.get("time") or 0)
        if ts_ms <= 0:
            return
        try:
            self._conn.execute(
                """INSERT OR REPLACE INTO candles
                   (symbol, interval, ts_ms, open, high, low, close, volume, source, recorded_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol, interval, ts_ms,
                    candle.get("open"),   candle.get("high"),
                    candle.get("low"),    candle.get("close"),
                    candle.get("volume"), source,
                    int(time.time() * 1000),
                ),
            )
            self._conn.commit()
        except Exception as exc:
            log.warning("CandleDB insert error: %s", exc)

    # ── Read ───────────────────────────────────────────────────────────────────

    def query(
        self,
        symbol: str,
        interval: str,
        from_ms: int = 0,
        to_ms: int | None = None,
        limit: int = 0,
    ) -> list[dict]:
        """Return candles for symbol/interval in [from_ms, to_ms] ordered ASC.

        Each row is a dict with keys: time, open, high, low, close, volume, source.
        Pass ``limit`` > 0 to cap the result set.
        """
        if to_ms is None:
            to_ms = int(time.time() * 1000)
        sql = (
            "SELECT ts_ms, open, high, low, close, volume, source "
            "FROM candles WHERE symbol=? AND interval=? AND ts_ms BETWEEN ? AND ? "
            "ORDER BY ts_ms ASC"
        )
        args: list = [symbol, interval, from_ms, to_ms]
        if limit > 0:
            sql += " LIMIT ?"
            args.append(limit)
        cur = self._conn.execute(sql, args)
        cols = ["time", "open", "high", "low", "close", "volume", "source"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def symbols(self) -> list[tuple]:
        """Return [(symbol, interval, candle_count), ...] sorted by symbol/interval."""
        cur = self._conn.execute(
            "SELECT symbol, interval, COUNT(*) "
            "FROM candles GROUP BY symbol, interval ORDER BY symbol, interval"
        )
        return cur.fetchall()

    # ── Maintenance ────────────────────────────────────────────────────────────

    def delete(self, symbol: str | None = None, interval: str | None = None) -> int:
        """Delete matching candles.  None acts as a wildcard.  Returns row count."""
        if symbol is None:
            cur = self._conn.execute("DELETE FROM candles")
        elif interval is None:
            cur = self._conn.execute("DELETE FROM candles WHERE symbol=?", (symbol,))
        else:
            cur = self._conn.execute(
                "DELETE FROM candles WHERE symbol=? AND interval=?", (symbol, interval)
            )
        self._conn.commit()
        return cur.rowcount

    def stats(self) -> dict:
        """Return {total_candles, db_size_bytes, db_path}."""
        row = self._conn.execute("SELECT COUNT(*) FROM candles").fetchone()
        total = row[0] if row else 0
        try:
            size = self.db_path.stat().st_size
        except Exception:
            size = 0
        return {
            "total_candles": total,
            "db_size_bytes": size,
            "db_path": str(self.db_path),
        }

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()
