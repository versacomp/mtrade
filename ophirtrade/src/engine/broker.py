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

    def route_order(self, symbol: str, side: str, qty: int, price: float = None):
        """Dynamically routes Equities or Futures with strict Decimal ledger math."""
        try:
            # 1. Detect the Asset Class
            if symbol.startswith('/'):
                instrument = self.loop.run_until_complete(Future.get(self.session, symbol))
            else:
                instrument = self.loop.run_until_complete(Equity.get(self.session, symbol))

            action = OrderAction.BUY_TO_OPEN if side.upper() == "BUY" else OrderAction.SELL_TO_OPEN
            order_type = OrderType.LIMIT if price else OrderType.MARKET

            # 2. The v12 SDK strictly requires Decimal objects for quantity
            dec_qty = Decimal(str(qty))
            leg = instrument.build_leg(dec_qty, action)

            # 3. Ledger Math Translation
            # Debits (Buys) MUST be negative. Credits (Sells) MUST be positive.
            if price is not None:
                dec_price = Decimal(str(price))
                if side.upper() == "BUY":
                    dec_price = -dec_price
            else:
                dec_price = None

            order = NewOrder(
                time_in_force=OrderTimeInForce.DAY,
                order_type=order_type,
                legs=[leg],
                price=dec_price
            )

            response = self.loop.run_until_complete(
                self.account.place_order(self.session, order, dry_run=False)
            )
            return response

        except Exception as e:
            return f"EXECUTION FAILED: {traceback.format_exc()}"