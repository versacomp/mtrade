"""Unit tests for api/dxlink_streamer.py."""

import pytest

from api.dxlink_streamer import DXLinkStreamer, KEEPALIVE_INTERVAL


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_keepalive_interval_positive(self):
        assert KEEPALIVE_INTERVAL > 0

    def test_channel_id(self):
        assert DXLinkStreamer.CHANNEL_ID == 1


# ---------------------------------------------------------------------------
# DXLinkStreamer._parse_compact
# ---------------------------------------------------------------------------

class TestParseCompact:
    """Tests for the static COMPACT-format parser."""

    def _collect(self, data, field_order):
        candles = []
        DXLinkStreamer._parse_compact(data, field_order, candles.append)
        return candles

    def test_single_candle(self):
        fields = ["eventSymbol", "time", "open", "high", "low", "close", "volume"]
        data = ["Candle", ["/MES{=1m}", 1_000_000, 100, 105, 99, 102, 500]]
        candles = self._collect(data, fields)
        assert len(candles) == 1
        c = candles[0]
        assert c["eventSymbol"] == "/MES{=1m}"
        assert c["time"] == 1_000_000
        assert c["open"] == 100
        assert c["close"] == 102

    def test_multiple_candles_in_one_batch(self):
        fields = ["eventSymbol", "time", "open", "high", "low", "close", "volume"]
        row1 = ["/MES{=1m}", 1_000, 100, 105, 99, 102, 500]
        row2 = ["/MES{=1m}", 2_000, 103, 108, 101, 107, 300]
        data = ["Candle", row1 + row2]
        candles = self._collect(data, fields)
        assert len(candles) == 2
        assert candles[0]["time"] == 1_000
        assert candles[1]["time"] == 2_000

    def test_multiple_event_types(self):
        """Non-Candle event types should be skipped."""
        fields = ["eventSymbol", "time", "open", "high", "low", "close", "volume"]
        data = [
            "Trade", ["/MES{=1m}", 1_000, 99, 100, 98, 99.5, 100],
            "Candle", ["/MES{=1m}", 2_000, 100, 105, 99, 103, 200],
        ]
        candles = self._collect(data, fields)
        assert len(candles) == 1
        assert candles[0]["time"] == 2_000

    def test_empty_data_returns_no_candles(self):
        fields = ["eventSymbol", "time", "open", "high", "low", "close", "volume"]
        candles = self._collect([], fields)
        assert candles == []

    def test_empty_field_order_returns_no_candles(self):
        data = ["Candle", ["/MES{=1m}", 1_000, 100, 105, 99, 102, 500]]
        candles = self._collect(data, [])
        assert candles == []

    def test_incomplete_record_skipped(self):
        """If a record has fewer values than the field count, skip it."""
        fields = ["eventSymbol", "time", "open", "high", "low", "close", "volume"]
        # Only 3 values instead of 7
        data = ["Candle", ["/MES{=1m}", 1_000, 100]]
        candles = self._collect(data, fields)
        assert candles == []

    def test_non_list_values_skipped(self):
        fields = ["eventSymbol", "time"]
        data = ["Candle", "not-a-list"]
        candles = self._collect(data, fields)
        assert candles == []

    def test_callback_exception_is_swallowed(self):
        """Errors in the on_candle callback should not propagate."""
        fields = ["eventSymbol", "time", "open", "high", "low", "close", "volume"]
        data = ["Candle", ["/MES{=1m}", 1_000, 100, 105, 99, 102, 500]]

        def bad_callback(candle):
            raise RuntimeError("callback error")

        # Should not raise
        DXLinkStreamer._parse_compact(data, fields, bad_callback)

    def test_fields_mapped_correctly(self):
        fields = ["eventSymbol", "time", "open", "high", "low", "close", "volume"]
        values = ["/NQ{=1m}", 5_000, 15000, 15200, 14900, 15100, 1000]
        data = ["Candle", values]
        candles = self._collect(data, fields)
        c = candles[0]
        assert c == dict(zip(fields, values))

    def test_odd_length_data_handles_trailing_single(self):
        """data list with odd number of elements: last unpaired entry is ignored."""
        fields = ["eventSymbol", "time", "open", "high", "low", "close", "volume"]
        data = ["Candle", ["/MES{=1m}", 1_000, 100, 105, 99, 102, 500], "orphan"]
        # Should not raise; candle from the valid pair is still parsed
        candles = self._collect(data, fields)
        assert len(candles) == 1

    def test_partial_batch_last_record_dropped(self):
        """A batch with values not divisible by field count drops the last partial record."""
        fields = ["eventSymbol", "time", "open", "high", "low", "close", "volume"]
        row1 = ["/MES{=1m}", 1_000, 100, 105, 99, 102, 500]
        # Append 3 extra values — one partial record
        data = ["Candle", row1 + ["/MES{=1m}", 2_000, 101]]
        candles = self._collect(data, fields)
        assert len(candles) == 1
        assert candles[0]["time"] == 1_000


# ---------------------------------------------------------------------------
# DXLinkStreamer construction
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_stores_url_and_token(self):
        streamer = DXLinkStreamer("wss://example.com/feed", "auth-token")
        assert streamer.dxlink_url == "wss://example.com/feed"
        assert streamer.token == "auth-token"
