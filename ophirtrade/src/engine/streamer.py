import os
import asyncio
from PyQt6.QtCore import QThread, pyqtSignal
import traceback
from dotenv import load_dotenv

from tastytrade import Session
from tastytrade.streamer import DXLinkStreamer
from tastytrade.dxfeed import Quote

load_dotenv()


class MarketDataStreamer(QThread):
    """
    Dedicated background thread for streaming live WebSocket data.
    Uses an independent authentication session to prevent event loop collisions.
    """
    tick_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(self, symbol="SPY", is_live=False):
        super().__init__()
        self.symbol = symbol
        self.is_live = is_live
        self._is_running = True

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
        try:
            # 1. Initialize an independent Session bound STRICTLY to this thread's event loop
            if self.is_live:
                client_secret = os.getenv("TASTYTRADE_CLIENT_SECRET")
                refresh_token = os.getenv("TASTYTRADE_REFRESH_TOKEN")
                session = Session(client_secret, refresh_token)
            else:
                client_secret = os.getenv("TASTYTRADE_CLIENT_SECRET_SANDBOX")
                refresh_token = os.getenv("TASTYTRADE_REFRESH_TOKEN_SANDBOX")
                session = Session(client_secret, refresh_token, is_test=True)

                # 2. Open the WebSocket using the dedicated session
                async with DXLinkStreamer(session) as streamer:

                    # Subscribe ONLY to the Quote (Bid/Ask) stream
                    await streamer.subscribe(Quote, [self.symbol])

                    self.tick_signal.emit({"type": "status", "msg": f"[STREAMER] WebSocket locked onto {self.symbol}."})

                    async for event in streamer.listen(Quote):
                        if not self._is_running:
                            break

                        tick_data = {
                            "type": "tick",
                            "symbol": getattr(event, 'event_symbol', self.symbol),
                            "event_type": type(event).__name__,
                            "bid": getattr(event, 'bid_price', None),
                            "ask": getattr(event, 'ask_price', None)
                        }

                        self.tick_signal.emit(tick_data)


        except Exception as e:

            # Unpack the ExceptionGroup to reveal the true network error

            self.error_signal.emit(f"[STREAMER DISCONNECT] \n{traceback.format_exc()}")

    def stop(self):
        """Gracefully shuts down the WebSocket."""
        self._is_running = False