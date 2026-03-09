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
            # Table for strategy performance stats
            conn.execute('''
                         CREATE TABLE IF NOT EXISTS sim_trades
                         (
                             symbol
                             TEXT,
                             direction
                             TEXT,
                             entry_price
                             REAL,
                             timestamp
                             REAL
                         )
                         ''')
            # Create indices for lightning-fast queries later
            conn.execute('CREATE INDEX IF NOT EXISTS idx_candle_symbol ON candles(symbol)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_trade_symbol ON sim_trades(symbol)')

    def insert_candle(self, symbol: str, candle: dict, timestamp: float):
        """Saves a fully closed candle to the local database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?)',
                (symbol, timestamp, candle['open'], candle['high'], candle['low'], candle['close'], candle['volume'])
            )

    def insert_trade(self, symbol: str, direction: str, entry_price: float):
        """Logs an Alpha Engine execution for KPI tracking."""
        timestamp = datetime.datetime.now().timestamp()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'INSERT INTO sim_trades VALUES (?, ?, ?, ?)',
                (symbol, direction, entry_price, timestamp)
            )