"""Unit tests for api/candle_db.py."""

import time
from pathlib import Path

import pytest

import api.candle_db as candle_db_module
from api.candle_db import CandleDB, get_db, reset_db


@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure the module-level singleton is cleared before and after each test."""
    candle_db_module._instance = None
    yield
    if candle_db_module._instance is not None:
        try:
            candle_db_module._instance.close()
        except Exception:
            pass
        candle_db_module._instance = None


@pytest.fixture
def db(tmp_path):
    """Provide a fresh in-memory-equivalent CandleDB backed by a temp file."""
    instance = CandleDB(db_path=tmp_path / "test_candles.db")
    yield instance
    instance.close()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _candle(ts_ms: int, o=100.0, h=105.0, l=99.0, c=102.0, v=500.0) -> dict:
    return {"time": ts_ms, "open": o, "high": h, "low": l, "close": c, "volume": v}


# ---------------------------------------------------------------------------
# CandleDB.insert and query
# ---------------------------------------------------------------------------

class TestInsertAndQuery:
    def test_insert_single_candle(self, db):
        db.insert("MES", "1m", _candle(1_000_000))
        rows = db.query("MES", "1m")
        assert len(rows) == 1
        assert rows[0]["time"] == 1_000_000
        assert rows[0]["open"] == 100.0

    def test_insert_replaces_duplicate(self, db):
        db.insert("MES", "1m", _candle(1_000_000, c=100.0))
        db.insert("MES", "1m", _candle(1_000_000, c=999.0))
        rows = db.query("MES", "1m")
        assert len(rows) == 1
        assert rows[0]["close"] == 999.0

    def test_insert_ignores_zero_time(self, db):
        db.insert("MES", "1m", {"time": 0, "open": 1.0})
        rows = db.query("MES", "1m")
        assert rows == []

    def test_insert_ignores_missing_time(self, db):
        db.insert("MES", "1m", {"open": 1.0})
        rows = db.query("MES", "1m")
        assert rows == []

    def test_query_returns_all_keys(self, db):
        db.insert("MES", "1m", _candle(2_000_000))
        row = db.query("MES", "1m")[0]
        for key in ("time", "open", "high", "low", "close", "volume", "source"):
            assert key in row

    def test_query_filters_by_from_ms(self, db):
        db.insert("MES", "1m", _candle(1_000))
        db.insert("MES", "1m", _candle(2_000))
        db.insert("MES", "1m", _candle(3_000))
        rows = db.query("MES", "1m", from_ms=2_000)
        assert all(r["time"] >= 2_000 for r in rows)
        assert len(rows) == 2

    def test_query_filters_by_to_ms(self, db):
        db.insert("MES", "1m", _candle(1_000))
        db.insert("MES", "1m", _candle(2_000))
        db.insert("MES", "1m", _candle(3_000))
        rows = db.query("MES", "1m", to_ms=2_000)
        assert all(r["time"] <= 2_000 for r in rows)
        assert len(rows) == 2

    def test_query_limit(self, db):
        for ts in range(1_000, 6_000, 1_000):
            db.insert("MES", "1m", _candle(ts))
        rows = db.query("MES", "1m", limit=2)
        assert len(rows) == 2

    def test_query_returns_ascending_order(self, db):
        for ts in [3_000, 1_000, 2_000]:
            db.insert("MES", "1m", _candle(ts))
        rows = db.query("MES", "1m")
        times = [r["time"] for r in rows]
        assert times == sorted(times)

    def test_query_isolates_by_symbol(self, db):
        db.insert("MES", "1m", _candle(1_000))
        db.insert("NQ", "1m", _candle(1_000))
        rows = db.query("MES", "1m")
        assert len(rows) == 1

    def test_query_isolates_by_interval(self, db):
        db.insert("MES", "1m", _candle(1_000))
        db.insert("MES", "5m", _candle(1_000))
        rows = db.query("MES", "1m")
        assert len(rows) == 1

    def test_source_default_is_live(self, db):
        db.insert("MES", "1m", _candle(1_000))
        assert db.query("MES", "1m")[0]["source"] == "live"

    def test_source_custom(self, db):
        db.insert("MES", "1m", _candle(1_000), source="historical")
        assert db.query("MES", "1m")[0]["source"] == "historical"


# ---------------------------------------------------------------------------
# CandleDB.symbols
# ---------------------------------------------------------------------------

class TestSymbols:
    def test_symbols_empty(self, db):
        assert db.symbols() == []

    def test_symbols_lists_unique_combinations(self, db):
        db.insert("MES", "1m", _candle(1_000))
        db.insert("MES", "5m", _candle(2_000))
        db.insert("NQ", "1m", _candle(3_000))
        result = db.symbols()
        assert len(result) == 3

    def test_symbols_returns_counts(self, db):
        db.insert("MES", "1m", _candle(1_000))
        db.insert("MES", "1m", _candle(2_000))
        result = {(s, i): cnt for s, i, cnt in db.symbols()}
        assert result[("MES", "1m")] == 2


# ---------------------------------------------------------------------------
# CandleDB.delete
# ---------------------------------------------------------------------------

class TestDelete:
    def test_delete_all(self, db):
        db.insert("MES", "1m", _candle(1_000))
        db.insert("NQ", "5m", _candle(2_000))
        n = db.delete()
        assert n == 2
        assert db.query("MES", "1m") == []

    def test_delete_by_symbol(self, db):
        db.insert("MES", "1m", _candle(1_000))
        db.insert("NQ", "1m", _candle(2_000))
        n = db.delete(symbol="MES")
        assert n == 1
        assert db.query("NQ", "1m") != []

    def test_delete_by_symbol_and_interval(self, db):
        db.insert("MES", "1m", _candle(1_000))
        db.insert("MES", "5m", _candle(2_000))
        n = db.delete(symbol="MES", interval="1m")
        assert n == 1
        assert db.query("MES", "5m") != []

    def test_delete_returns_zero_when_nothing_matches(self, db):
        n = db.delete(symbol="GHOST")
        assert n == 0


# ---------------------------------------------------------------------------
# CandleDB.stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_empty_db(self, db):
        s = db.stats()
        assert s["total_candles"] == 0
        assert "db_size_bytes" in s
        assert "db_path" in s

    def test_stats_counts_candles(self, db):
        db.insert("MES", "1m", _candle(1_000))
        db.insert("MES", "1m", _candle(2_000))
        s = db.stats()
        assert s["total_candles"] == 2

    def test_stats_db_path_matches(self, tmp_path):
        p = tmp_path / "stats_test.db"
        instance = CandleDB(db_path=p)
        try:
            s = instance.stats()
            assert s["db_path"] == str(p)
        finally:
            instance.close()


# ---------------------------------------------------------------------------
# Singleton get_db / reset_db
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_get_db_returns_same_instance(self, tmp_path, monkeypatch):
        monkeypatch.setattr(candle_db_module, "DEFAULT_DB_PATH", tmp_path / "singleton.db")
        db1 = get_db()
        db2 = get_db()
        assert db1 is db2

    def test_reset_db_clears_singleton(self, tmp_path, monkeypatch):
        monkeypatch.setattr(candle_db_module, "DEFAULT_DB_PATH", tmp_path / "reset.db")
        db1 = get_db()
        reset_db()
        db2 = get_db()
        assert db1 is not db2
        db2.close()

    def test_reset_db_is_safe_on_none(self):
        candle_db_module._instance = None
        reset_db()  # Should not raise
