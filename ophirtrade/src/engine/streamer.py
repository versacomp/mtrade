import os
import asyncio
from PyQt6.QtCore import QThread, pyqtSignal
import traceback
from dotenv import load_dotenv

from tastytrade.session import Session
from tastytrade import DXLinkStreamer
from tastytrade.dxfeed import Quote, Candle
from datetime import datetime, timedelta, timezone  # <--- Added timezone

load_dotenv()


class MarketDataStreamer(QThread):
    """
    Dedicated background thread for streaming live WebSocket data.
    Uses an independent authentication session to prevent event loop collisions.
    """
    tick_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(self, symbol="SPY", is_live=False, session=None):
        super().__init__()
        self.symbol = symbol
        self.is_live = is_live
        self._is_running = True
        self._session = session

    def run(self):
        """Creates a dedicated event loop just for the data firehose."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._stream_data())
        except Exception as e:
            self.error_signal.emit(f"[STREAMER FATAL] {traceback.format_exc()}")
        finally:
            loop.close()

    async def _stream_data(self):
        """The core async WebSocket connection."""

        self.tick_signal.emit({"type": "status",
                               "msg": f"[SYSTEM] Initiating dxfeed stream for {self.symbol}."})

        try:
            # 1. Initialize an independent Session bound STRICTLY to this thread
            if self.is_live:
                client_secret = os.getenv("TASTYTRADE_CLIENT_SECRET")
                refresh_token = os.getenv("TASTYTRADE_REFRESH_TOKEN")
                session = Session(client_secret, refresh_token)
            else:
                client_secret = os.getenv("TASTYTRADE_CLIENT_SECRET_SANDBOX")
                refresh_token = os.getenv("TASTYTRADE_REFRESH_TOKEN_SANDBOX")
                session = Session(client_secret, refresh_token, is_test=True)

            # --- NEW: THE DXLINK TRANSLATION MATRIX ---
            # DXLink requires a 2-digit year and exchange suffix (e.g., /MESH26:XCME).
            dxlink_symbol = self.symbol
            if self.symbol.startswith('/'):
                from tastytrade.instruments import Future
                try:
                    # THE FIX: We must 'await' the async .get() method!
                    future = await Future.get(session, self.symbol)

                    # Safely extract the instrument (in case the SDK returns a list)
                    if isinstance(future, list):
                        future = future[0]

                    dxlink_symbol = future.streamer_symbol
                    self.tick_signal.emit({"type": "status",
                                           "msg": f"[SYSTEM] Translated {self.symbol} -> {dxlink_symbol} for dxFeed."})
                except Exception as e:
                    self.tick_signal.emit({"type": "status", "msg": f"[WARN] Symbol translation failed: {str(e)}"})
            # ------------------------------------------

            # --- PHASE 1: DXLink Historical Seeder ---
            # Open a temporary connection just for history
            async with DXLinkStreamer(session) as history_streamer:
                self.tick_signal.emit(
                    {"type": "status", "msg": f"[STREAMER] Requesting DXLink history for {dxlink_symbol}..."})

                # STRICT UTC TIMEZONE: Look back 7 days
                start_date = datetime.now(timezone.utc) - timedelta(days=7)
                await history_streamer.subscribe_candle([dxlink_symbol], '1m', start_date)

                history_candles = []

                async def fetch_history():
                    async for event in history_streamer.listen(Candle):
                        if not self._is_running: break

                        history_candles.append({
                            'open': float(event.open),
                            'high': float(event.high),
                            'low': float(event.low),
                            'close': float(event.close),
                            'volume': float(event.volume)
                        })

                        if len(history_candles) >= 250:
                            break

                try:
                    await asyncio.wait_for(fetch_history(), timeout=5.0)
                except asyncio.TimeoutError:
                    self.tick_signal.emit({"type": "status",
                                           "msg": f"[STREAMER] History burst completed early. Caught {len(history_candles)} candles."})

            # (The history_streamer automatically, gracefully closes here!)

            self.tick_signal.emit({
                "type": "history",
                "data": history_candles[-250:]
            })

            # --- PHASE 2: Live Quote Streamer ---
            self.tick_signal.emit({"type": "status",
                                   "msg": f"[STREAMER] History injected. Locking onto live {dxlink_symbol} tape..."})

            # Open a FRESH connection for the infinite live feed
            async with DXLinkStreamer(session) as live_streamer:
                await live_streamer.subscribe(Quote, [dxlink_symbol])

                async for event in live_streamer.listen(Quote):
                    if not self._is_running: break

                    tick_data = {
                        "type": "tick",
                        # Force the original symbol so the UI can match it
                        "symbol": getattr(event, 'event_symbol', self.symbol),
                        "event_type": type(event).__name__,
                        "bid": getattr(event, 'bid_price', None),
                        "ask": getattr(event, 'ask_price', None)
                    }
                    self.tick_signal.emit(tick_data)

        except Exception as e:
            self.error_signal.emit(f"[STREAMER DISCONNECT] \n{traceback.format_exc()}")

    def stop(self):
        """Gracefully shuts down the WebSocket."""
        self._is_running = False