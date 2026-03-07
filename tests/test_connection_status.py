"""Unit tests for api/connection_status.py."""

import threading

import pytest

import api.connection_status as cs
from api.connection_status import ConnState, get, set_status, register_listener, clear_listener


@pytest.fixture(autouse=True)
def reset_state():
    """Restore module-level state after every test."""
    yield
    cs._state    = ConnState.OFFLINE
    cs._detail   = "Not connected"
    cs._callback = None


# ---------------------------------------------------------------------------
# ConnState enum
# ---------------------------------------------------------------------------

class TestConnState:
    def test_live_value(self):
        assert ConnState.LIVE.value == "CONNECTED"

    def test_demo_value(self):
        assert ConnState.DEMO.value == "DEMO"

    def test_offline_value(self):
        assert ConnState.OFFLINE.value == "OFFLINE"

    def test_all_states_in_colors(self):
        for state in ConnState:
            assert state in cs.COLORS

    def test_colors_are_hex_strings(self):
        for color in cs.COLORS.values():
            assert color.startswith("#")
            assert len(color) == 7


# ---------------------------------------------------------------------------
# get / set_status
# ---------------------------------------------------------------------------

class TestGetSetStatus:
    def test_initial_state_is_offline(self):
        state, detail = get()
        assert state == ConnState.OFFLINE

    def test_set_status_live(self):
        set_status(ConnState.LIVE)
        state, detail = get()
        assert state == ConnState.LIVE

    def test_set_status_demo(self):
        set_status(ConnState.DEMO, "running on simulated data")
        state, detail = get()
        assert state == ConnState.DEMO
        assert detail == "running on simulated data"

    def test_set_status_uses_enum_value_when_detail_empty(self):
        set_status(ConnState.LIVE, "")
        _, detail = get()
        assert detail == ConnState.LIVE.value

    def test_set_status_preserves_custom_detail(self):
        set_status(ConnState.OFFLINE, "API unreachable")
        _, detail = get()
        assert detail == "API unreachable"

    def test_get_returns_tuple_of_two(self):
        result = get()
        assert len(result) == 2


# ---------------------------------------------------------------------------
# register_listener / clear_listener
# ---------------------------------------------------------------------------

class TestListener:
    def test_listener_called_on_set_status(self):
        calls = []
        register_listener(lambda state, detail: calls.append((state, detail)))
        set_status(ConnState.LIVE, "streaming")
        assert len(calls) == 1
        assert calls[0] == (ConnState.LIVE, "streaming")

    def test_listener_not_called_after_clear(self):
        calls = []
        register_listener(lambda state, detail: calls.append((state, detail)))
        clear_listener()
        set_status(ConnState.DEMO)
        assert calls == []

    def test_register_listener_replaces_previous(self):
        calls_a, calls_b = [], []
        register_listener(lambda s, d: calls_a.append(s))
        register_listener(lambda s, d: calls_b.append(s))
        set_status(ConnState.LIVE)
        assert calls_a == []
        assert calls_b == [ConnState.LIVE]

    def test_listener_exception_is_swallowed(self):
        register_listener(lambda s, d: (_ for _ in ()).throw(RuntimeError("boom")))
        # Should not propagate the exception
        set_status(ConnState.LIVE)
        state, _ = get()
        assert state == ConnState.LIVE

    def test_set_status_thread_safety(self):
        """State after concurrent writes is valid ConnState."""
        errors = []

        def writer(state):
            try:
                set_status(state)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(s,))
                   for s in [ConnState.LIVE, ConnState.DEMO, ConnState.OFFLINE] * 10]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        final_state, _ = get()
        assert final_state in ConnState
