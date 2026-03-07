import asyncio
import pandas as pd
import numpy as np
from datetime import datetime
from tastytrade import Session
from tastytrade.dxfeed import DXLinkStreamer, Quote

class LiquidityBot:
    """
    A trading bot that monitors real-time prices, aggregates them into 1-minute bars,
    and executes a Liquidity Grab Reversal strategy with Trend and ADX filters.
    """
    def __init__(self, username, password, symbol='/MES', buffer_size=300):
        """
        Initialise the bot with credentials and trading parameters.

        Args:
            username:    tastytrade account username or email.
            password:    tastytrade account password.
            symbol:      Futures symbol to trade (default ``'/MES'``).
            buffer_size: Number of 1-minute bars to retain in memory; clamped to a
                         minimum of 250 so that the 200-period SMA always has enough data.
        """
        self.username = username
        self.password = password
        self.symbol = symbol
        self.buffer_size = max(buffer_size, 250) 

        # Data Structures
        self.session = None
        self.streamer = None
        self.bar_data = [] 
        self.current_bar = None
        self.current_minute = None

    def calculate_indicators(self, df):
        """Calculates Trend (200 SMA), RSI (14), and ADX (14)."""
        df = df.copy()
        
        # 1. Trend (200 SMA)
        df['SMA_200'] = df['Close'].rolling(window=200).mean()
        df['Trend'] = 0
        df.loc[df['Close'] > df['SMA_200'], 'Trend'] = 1
        df.loc[df['Close'] < df['SMA_200'], 'Trend'] = -1

        # 2. RSI (14)
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # 3. ADX (14)
        h_l = df['High'] - df['Low']
        h_pc = abs(df['High'] - df['Close'].shift(1))
        l_pc = abs(df['Low'] - df['Close'].shift(1))
        tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)

        plus_dm = np.where((df['High'] - df['High'].shift(1)) > (df['Low'].shift(1) - df['Low']), 
                           np.maximum(df['High'] - df['High'].shift(1), 0), 0)
        minus_dm = np.where((df['Low'].shift(1) - df['Low']) > (df['High'] - df['High'].shift(1)), 
                            np.maximum(df['Low'].shift(1) - df['Low'], 0), 0)

        window = 14
        tr_smooth = tr.rolling(window=window).mean()
        plus_dm_smooth = pd.Series(plus_dm).rolling(window=window).mean()
        minus_dm_smooth = pd.Series(minus_dm).rolling(window=window).mean()

        plus_di = (plus_dm_smooth / tr_smooth) * 100
        minus_di = (minus_dm_smooth / tr_smooth) * 100
        
        # Handle division by zero if tr_smooth is 0
        with np.errstate(divide='ignore', invalid='ignore'):
            dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
        
        df['ADX'] = dx.rolling(window=window).mean()
        
        return df

    def run_strategy(self):
        """Analyzes the buffered data for trade signals."""
        if len(self.bar_data) < 220:
            return

        df = pd.DataFrame(self.bar_data)
        df.set_index('Date', inplace=True)
        
        df = self.calculate_indicators(df)
        last_bar = df.iloc[-1]
        
        # Liquidity Levels (20-period lookback)
        rolling_max = df['High'].rolling(window=20).max().shift(1).iloc[-1]
        rolling_min = df['Low'].rolling(window=20).min().shift(1).iloc[-1]

        signal = 0
        adx_threshold = 30

        # Strategy Logic
        if (last_bar['High'] > rolling_max) and (last_bar['Close'] < rolling_max):
            if last_bar['Trend'] == -1 and last_bar['ADX'] < adx_threshold and last_bar['RSI'] < 70:
                signal = -1

        elif (last_bar['Low'] < rolling_min) and (last_bar['Close'] > rolling_min):
            if last_bar['Trend'] == 1 and last_bar['ADX'] < adx_threshold and last_bar['RSI'] > 30:
                signal = 1

        if signal == 1:
            print(f"[ALERT] {df.index[-1]} | BUY | Price: {last_bar['Close']:.2f} | ADX: {last_bar['ADX']:.2f}")
        elif signal == -1:
            print(f"[ALERT] {df.index[-1]} | SELL | Price: {last_bar['Close']:.2f} | ADX: {last_bar['ADX']:.2f}")

    async def authenticate(self):
        """Open a tastytrade session using stored username/password credentials."""
        print(f"Authenticating {self.username}...")
        self.session = Session(self.username, self.password)
        print("Authenticated.")

    async def setup_stream(self):
        """Create a DXLink streamer and subscribe to trade quotes for the target symbol."""
        self.streamer = DXLinkStreamer(self.session)
        await self.streamer.subscribe_trade([self.symbol])
        print(f"Subscribed to {self.symbol}")

    def update_bar(self, price, timestamp):
        """
        Incorporate a new trade tick into the current 1-minute OHLCV bar.

        When the tick belongs to a new minute the completed bar is appended to
        ``bar_data``, the rolling buffer is trimmed to ``buffer_size``,
        ``run_strategy`` is called to check for signals, and a fresh bar is
        opened.  If the tick falls within the current minute only the High,
        Low, and Close fields are updated.
        """
        trade_time = pd.to_datetime(timestamp, unit='ms') if isinstance(timestamp, (int, float)) else timestamp
        trade_minute = trade_time.floor('T')

        if self.current_minute is None:
            self.current_minute = trade_minute
            self.current_bar = {'Open': price, 'High': price, 'Low': price, 'Close': price, 'Date': trade_minute}

        elif trade_minute > self.current_minute:
            self.bar_data.append(self.current_bar)
            if len(self.bar_data) > self.buffer_size:
                self.bar_data.pop(0)
            self.run_strategy()
            self.current_minute = trade_minute
            self.current_bar = {'Open': price, 'High': price, 'Low': price, 'Close': price, 'Date': trade_minute}

        else:
            self.current_bar['High'] = max(self.current_bar['High'], price)
            self.current_bar['Low'] = min(self.current_bar['Low'], price)
            self.current_bar['Close'] = price

    async def run(self):
        """
        Authenticate, open the DXLink stream, and process incoming quotes indefinitely.

        Each incoming ``Quote`` event is forwarded to ``update_bar`` so that
        1-minute OHLCV bars are built in real time and the strategy is evaluated
        at the close of every bar.
        """
        await self.authenticate()
        await self.setup_stream()
        print("Bot running... Press Stop to exit.")
        async for trade in self.streamer.listen(Quote):
            try:
                price = trade.price if hasattr(trade, 'price') else trade.bidPrice
                time = trade.time if hasattr(trade, 'time') else datetime.now()
                self.update_bar(price, time)
            except Exception as e:
                print(f"Error: {e}")
