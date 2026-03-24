import sqlite3
from pathlib import Path
import datetime


class OphirDatabase:
    def __init__(self):
        # 1. Create the hidden directory in the user's home folder
        self.db_dir = Path.home() / ".ophirtrade"
        self.db_dir.mkdir(parents=True, exist_ok=True)

        # 2. Establish the SQLite file path
        self.db_path = self.db_dir / "ophir_history.db"
        self._init_db()

    def _init_db(self):
        """Creates the tables if they don't already exist."""
        with sqlite3.connect(self.db_path) as conn:
            # Table for backtesting/replay candles
            conn.execute('''
                         CREATE TABLE IF NOT EXISTS candles
                         (
                             symbol
                             TEXT,
                             timestamp
                             REAL,
                             open
                             REAL,
                             high
                             REAL,
                             low
                             REAL,
                             close
                             REAL,
                             volume
                             REAL
                         )
                         ''')
            # UPGRADED: Table for strategy performance stats
            conn.execute('''
                         CREATE TABLE IF NOT EXISTS closed_trades
                         (
                             symbol
                             TEXT,
                             direction
                             TEXT,
                             entry_price
                             REAL,
                             exit_price
                             REAL,
                             sl
                             REAL,
                             tp
                             REAL,
                             pnl
                             REAL,
                             status
                             TEXT,
                             entry_time
                             REAL,
                             exit_time
                             REAL
                         )
                         ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_candle_symbol ON candles(symbol)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_trade_symbol ON closed_trades(symbol)')

    def log_closed_trade(self, trade_record: dict):
        """Saves a fully closed trade to the local database for KPI analysis."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                '''INSERT INTO closed_trades
                   (symbol, direction, entry_price, exit_price, sl, tp, pnl, status, entry_time, exit_time)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    trade_record['symbol'], trade_record['direction'],
                    trade_record['entry_price'], trade_record['exit_price'],
                    trade_record['sl'], trade_record['tp'], trade_record['pnl'],
                    trade_record['status'], trade_record['entry_time'], trade_record['exit_time']
                )
            )

    def insert_candle(self, symbol: str, candle: dict, timestamp: float):
        """Saves a fully closed candle to the local database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?)',
                (symbol, timestamp, candle['open'], candle['high'], candle['low'], candle['close'], candle['volume'])
            )
