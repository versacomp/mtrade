"""
DXLink WebSocket streamer for tastytrade market data.

Protocol documentation:
https://developer.tastytrade.com/streaming-market-data/

Flow:
  1. Connect to dxlink-url from /api-quote-tokens
  2. Send SETUP
  3. Receive AUTH_STATE(UNAUTHORIZED) → send AUTH{token}
  4. Receive AUTH_STATE(AUTHORIZED) → send CHANNEL_REQUEST
  5. Receive CHANNEL_OPENED → send FEED_SETUP (COMPACT format)
  6. Receive FEED_CONFIG (actual field order) → send FEED_SUBSCRIPTION
  7. Receive FEED_DATA (COMPACT) → parse and dispatch
  8. Send KEEPALIVE every 30 seconds
"""

import asyncio
import json
import logging

import websockets
import websockets.exceptions

log = logging.getLogger(__name__)

KEEPALIVE_INTERVAL = 30  # seconds


class DXLinkStreamer:
    """
    Streams 1-minute candle data via DXLink WebSocket.

    Usage:
        streamer = DXLinkStreamer(dxlink_url, token)
        # Run as a task; cancel the task to stop.
        await streamer.stream_candles("MES", from_time_ms, on_candle_callback)
    """

    CHANNEL_ID = 1

    def __init__(self, dxlink_url: str, token: str) -> None:
        """
        Initialise the streamer.

        Args:
            dxlink_url: The WebSocket URL from the ``/api-quote-tokens`` response.
            token:      The DXLink authentication token from the same response.
        """
        self.dxlink_url = dxlink_url
        self.token = token

    async def stream_candles(
        self,
        symbol: str,
        from_time_ms: int,
        on_candle,  # Callable[[dict], None]
        interval: str = "1m",
    ) -> None:
        """
        Connect, authenticate, and stream candle events for *symbol* at *interval*.

        *interval* follows the dxFeed aggregation-period syntax:
          ``1s`` ``5s`` ``15s`` ``30s`` ``1m`` ``3m`` ``5m`` ``15m`` ``30m``
          ``1h`` ``4h`` ``1d``  (default: ``1m``)

        Calls on_candle(dict) for every candle received.  The dict contains
        keys matching the fields negotiated in FEED_SETUP (eventSymbol, time,
        open, high, low, close, volume).

        Runs until the enclosing asyncio Task is cancelled, or the connection
        drops (raises ConnectionError in that case so the caller can fall back).
        """
        # symbol should already be the full streamer-symbol (e.g. /MESU26:XCME)
        # Append the candle aggregation period suffix requested by the caller
        dxlink_symbol = f"{symbol}{{={interval}}}"
        log.info("DXLink connecting — symbol=%s url=%s", dxlink_symbol, self.dxlink_url)

        field_order: list[str] = []
        auth_sent  = False
        subscribed = False  # guard against duplicate FEED_SUBSCRIPTION
        keepalive_task: asyncio.Task | None = None

        try:
            async with websockets.connect(
                self.dxlink_url,
                open_timeout=15,
                ping_interval=None,  # We handle keepalive manually
                additional_headers={"User-Agent": "mtrade/1.0"},
            ) as ws:

                # ── SETUP ────────────────────────────────────────────────────
                await ws.send(json.dumps({
                    "type": "SETUP",
                    "channel": 0,
                    "version": "0.1-DXF-JS/0.3.0",
                    "keepaliveTimeout": 60,
                    "acceptKeepaliveTimeout": 60,
                }))
                log.debug("DXLink → SETUP")

                async def _keepalive_loop() -> None:
                    while True:
                        await asyncio.sleep(KEEPALIVE_INTERVAL)
                        try:
                            await ws.send(json.dumps({"type": "KEEPALIVE", "channel": 0}))
                            log.debug("DXLink → KEEPALIVE")
                        except Exception:
                            return

                # ── Message loop ─────────────────────────────────────────────
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    except asyncio.TimeoutError:
                        continue
                    except websockets.exceptions.ConnectionClosed as exc:
                        log.warning("DXLink connection closed: %s", exc)
                        raise ConnectionError(f"DXLink connection closed: {exc}") from exc

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        log.warning("DXLink: non-JSON frame: %s", raw[:120])
                        continue

                    msg_type = msg.get("type")
                    log.debug("DXLink ← %s", msg_type)

                    if msg_type == "AUTH_STATE":
                        state_val = msg.get("state")
                        log.info("DXLink AUTH_STATE: %s", state_val)
                        if state_val == "UNAUTHORIZED":
                            if not auth_sent:
                                auth_sent = True
                                await ws.send(json.dumps({
                                    "type": "AUTH",
                                    "channel": 0,
                                    "token": self.token,
                                }))
                                log.debug("DXLink → AUTH")
                            else:
                                raise RuntimeError("DXLink: AUTH token rejected")
                        elif state_val == "AUTHORIZED":
                            await ws.send(json.dumps({
                                "type": "CHANNEL_REQUEST",
                                "channel": self.CHANNEL_ID,
                                "service": "FEED",
                                "parameters": {"contract": "AUTO"},
                            }))
                            log.debug("DXLink → CHANNEL_REQUEST")

                    elif msg_type == "CHANNEL_OPENED":
                        await ws.send(json.dumps({
                            "type": "FEED_SETUP",
                            "channel": self.CHANNEL_ID,
                            "acceptAggregationPeriod": 10,
                            "acceptDataFormat": "COMPACT",
                            "acceptEventFields": {
                                "Candle": [
                                    "eventSymbol", "time",
                                    "open", "high", "low", "close", "volume",
                                ],
                            },
                        }))
                        log.debug("DXLink → FEED_SETUP")

                    elif msg_type == "FEED_CONFIG":
                        # Server sends FEED_CONFIG twice:
                        #   1st — in response to FEED_SETUP (send subscription here)
                        #   2nd — to acknowledge FEED_SUBSCRIPTION (skip)
                        event_fields = msg.get("eventFields", {})
                        new_fields   = event_fields.get("Candle")
                        if new_fields:
                            field_order = new_fields
                        log.info("DXLink FEED_CONFIG Candle fields: %s", field_order)

                        if not subscribed:
                            subscribed = True
                            if not field_order:
                                field_order = [
                                    "eventSymbol", "time",
                                    "open", "high", "low", "close", "volume",
                                ]
                            await ws.send(json.dumps({
                                "type": "FEED_SUBSCRIPTION",
                                "channel": self.CHANNEL_ID,
                                "add": [{
                                    "type": "Candle",
                                    "symbol": dxlink_symbol,
                                    "fromTime": from_time_ms,
                                }],
                            }))
                            log.info(
                                "DXLink → FEED_SUBSCRIPTION symbol=%s fromTime=%d",
                                dxlink_symbol, from_time_ms,
                            )
                            if keepalive_task is None:
                                keepalive_task = asyncio.create_task(_keepalive_loop())
                        else:
                            log.debug("DXLink: ignoring duplicate FEED_CONFIG (subscription ack)")

                    elif msg_type == "FEED_DATA":
                        raw_data = msg.get("data", [])
                        log.debug("DXLink FEED_DATA raw: %s", str(raw_data)[:300])
                        if field_order:
                            self._parse_compact(raw_data, field_order, on_candle)
                        else:
                            log.warning("DXLink: FEED_DATA before FEED_CONFIG — skipping")

                    elif msg_type == "KEEPALIVE":
                        await ws.send(json.dumps({"type": "KEEPALIVE", "channel": 0}))

                    elif msg_type == "ERROR":
                        log.error("DXLink server error: %s", msg.get("error"))

        finally:
            if keepalive_task is not None:
                keepalive_task.cancel()
            log.info("DXLink stream ended for %s", symbol)

    @staticmethod
    def _parse_compact(
        data: list,
        field_order: list[str],
        on_candle,
    ) -> None:
        """
        Parse COMPACT-format FEED_DATA payload.

        Layout: [eventType, [flat_values...], eventType, [flat_values...], ...]
        The flat_values array holds N_fields * N_events values in row-major order.
        """
        n = len(field_order)
        if n == 0 or not data:
            return

        i = 0
        while i + 1 < len(data):
            event_type = data[i]
            values = data[i + 1]
            i += 2

            if event_type != "Candle" or not isinstance(values, list):
                continue

            for j in range(0, len(values), n):
                record = values[j: j + n]
                if len(record) < n:
                    break
                candle_dict = dict(zip(field_order, record))
                try:
                    on_candle(candle_dict)
                except Exception as exc:
                    log.warning("DXLink on_candle callback error: %s", exc)
