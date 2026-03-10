import os
import asyncio
import traceback
from dotenv import load_dotenv
from decimal import Decimal

from tastytrade import Session
from tastytrade.account import Account
from tastytrade.instruments import Equity, Future
from tastytrade.order import NewOrder, OrderAction, OrderTimeInForce, OrderType

load_dotenv()


class OphirBroker:
    """
    The secure gateway to the live market using OAuth2 Refresh Tokens.
    Maintains a persistent asyncio event loop to bridge the v12 SDK with our sync engine.
    """

    def __init__(self, is_live=False):
        self.is_live = is_live

        # --- THE FIX: Create ONE persistent event loop for the entire session ---
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        if self.is_live:
            print("[NETWORK] WARNING: Initializing LIVE Production Session via OAuth...")
            client_secret = os.getenv("TASTYTRADE_CLIENT_SECRET")
            refresh_token = os.getenv("TASTYTRADE_REFRESH_TOKEN")

            if not client_secret or not refresh_token:
                raise ValueError("Production OAuth credentials missing from .env file.")

            self.session = Session(client_secret, refresh_token)
        else:
            print("[NETWORK] Initializing Sandbox Certification Session via OAuth...")
            client_secret = os.getenv("TASTYTRADE_CLIENT_SECRET_SANDBOX")
            refresh_token = os.getenv("TASTYTRADE_REFRESH_TOKEN_SANDBOX")

            if not client_secret or not refresh_token:
                raise ValueError("Sandbox OAuth credentials missing from .env file.")

            self.session = Session(client_secret, refresh_token, is_test=True)

        # We now use our persistent loop instead of asyncio.run()
        accounts = self.loop.run_until_complete(Account.get(self.session))

        if not accounts:
            raise ValueError(
                "Authentication successful, but NO ACCOUNTS were found. "
                "Log into the tastytrade sandbox portal and generate a test account."
            )

        self.account = accounts[0]
        print(f"[NETWORK] Secured connection to Account: {self.account.account_number}")

    def route_order(self, symbol: str, action_type: str, qty: int, price: float = None):
        """Routes live market or limit orders to the clearinghouse."""
        from tastytrade.order import NewOrder, OrderAction, OrderTimeInForce, OrderType, Leg
        from tastytrade.instruments import InstrumentType
        from decimal import Decimal

        try:
            # TASTYTRADE TRANSLATION MATRIX
            # (Tastytrade strictly forces options verbs onto equities)
            if action_type == "BUY":  # Going Long
                action = OrderAction.BUY_TO_OPEN
            elif action_type == "SELL":  # Closing a Long
                action = OrderAction.SELL_TO_CLOSE
            elif action_type == "SELL_SHORT":  # Going Short
                action = OrderAction.SELL_TO_OPEN
            elif action_type == "BUY_TO_COVER":  # Closing a Short
                action = OrderAction.BUY_TO_CLOSE
            else:
                action = OrderAction.BUY_TO_OPEN  # Fallback

            # --- NEW: DYNAMIC ASSET CLASS ROUTING ---
            if symbol.startswith('/'):
                asset_class = InstrumentType.FUTURE
            else:
                asset_class = InstrumentType.EQUITY
            # ----------------------------------------

            # BUILD THE LEG LOCALLY
            leg = Leg(
                instrument_type=asset_class,  # <--- Pass the dynamic asset class
                symbol=symbol,
                action=action,
                quantity=Decimal(str(qty))
            )

            if price:
                order = NewOrder(time_in_force=OrderTimeInForce.DAY, order_type=OrderType.LIMIT, legs=[leg],
                                 price=Decimal(str(price)))
            else:
                order = NewOrder(time_in_force=OrderTimeInForce.DAY, order_type=OrderType.MARKET, legs=[leg])

            response = self.loop.run_until_complete(self.account.place_order(self.session, order, dry_run=False))
            return response

        except Exception as e:
            return f"EXECUTION FAILED: {str(e)}"

    def get_portfolio_status(self):
        """Fetches live account balances and open positions from the clearinghouse."""
        try:
            # We use the persistent event loop to cleanly fetch the ledgers
            balances = self.loop.run_until_complete(self.account.get_balances(self.session))
            positions = self.loop.run_until_complete(self.account.get_positions(self.session))
            return balances, positions
        except Exception as e:
            return f"ERROR: {str(e)}", None

    def get_historical_candles(self, symbol: str, interval: str = '1m'):
        """Fetches historical candles from Yahoo Finance with dynamic lookback."""
        try:
            import yfinance as yf
            import pandas as pd
            import re

            # --- TICKER TRANSLATION MATRIX ---
            yf_symbol = symbol.upper().lstrip('/')  # Remove Tastytrade's leading slash if present

            # Regex to detect Futures expiration codes (e.g., H6 for March 2026)
            # Matches a base ticker followed by a standard month code (F,G,H,J,K,M,N,Q,U,V,X,Z) and 1-2 digits
            match = re.match(r'^([A-Z]+)[FGHJKMNQUVXZ]\d{1,2}$', yf_symbol)

            if match:
                base = match.group(1)
                yf_symbol = f"{base}=F"  # Convert MCDH6 -> MCD=F
            elif yf_symbol in ["ES", "MES", "NQ", "MNQ", "RTY", "M2K", "YM", "MYM", "GC", "MGC", "CL", "MCL"]:
                yf_symbol = f"{yf_symbol}=F"  # Catch raw base symbols just in case
            # ---------------------------------

            # Dynamic Lookback: Ensure we always get > 200 candles for the Alpha Engine
            if interval == '1m':
                days_back = 3
            elif interval == '5m':
                days_back = 5
            elif interval == '15m':
                days_back = 15
            elif interval == '1h':
                days_back = 45
            else:
                days_back = 5

            # Download the tape using the TRANSLATED symbol
            df = yf.download(yf_symbol, period=f"{days_back}d", interval=interval, progress=False)

            if df.empty:
                return f"ERROR: No historical data found for YF symbol {yf_symbol}."

            # Flatten multi-index columns (handles newer yfinance versions)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            formatted_candles = []
            for _, row in df.iterrows():
                formatted_candles.append({
                    'open': float(row['Open']),
                    'high': float(row['High']),
                    'low': float(row['Low']),
                    'close': float(row['Close']),
                    'volume': float(row['Volume'])
                })

            return formatted_candles

        except ImportError:
            return "ERROR: Missing yfinance. Run: pip install yfinance pandas"
        except Exception as e:
            return f"ERROR: {str(e)}"