"""
Global API connection status — shared across all views.

Call set_status() from any view when the connection state changes.
The single registered listener (the active nav bar) is notified immediately.
"""

from enum import Enum
from threading import Lock
from typing import Callable, Optional


class ConnState(Enum):
    LIVE    = "CONNECTED"  # REST authenticated + DXLink streaming real data
    DEMO    = "DEMO"     # DXLink unavailable — running on simulated data
    OFFLINE = "OFFLINE"  # Auth failed or REST API unreachable


# Colour for each state — shared so nav bar and any future widget stay in sync
COLORS: dict[ConnState, str] = {
    ConnState.LIVE:    "#44DD88",
    ConnState.DEMO:    "#FFA726",
    ConnState.OFFLINE: "#FF5555",
}

# ── Module-level state ─────────────────────────────────────────────────────────
_lock:     Lock               = Lock()
_state:    ConnState          = ConnState.OFFLINE
_detail:   str                = "Not connected"
_callback: Optional[Callable] = None


def get() -> tuple[ConnState, str]:
    """Return the current (state, detail) snapshot."""
    with _lock:
        return _state, _detail


def set_status(state: ConnState, detail: str = "") -> None:
    """
    Update connection state and push to the registered UI listener.

    Safe to call from any thread.  The callback runs synchronously in the
    calling thread, so callers on the Flet event loop (async tasks) are fine;
    callers on background threads should only update non-Flet state or use
    page.run_task / asyncio.run_coroutine_threadsafe if needed.
    """
    global _state, _detail
    with _lock:
        _state  = state
        _detail = detail or state.value
        cb      = _callback
    if cb is not None:
        try:
            cb(state, _detail)
        except Exception:
            pass


def register_listener(fn: Callable) -> None:
    """
    Register the active UI update callback.

    Called once per nav_app_bar() build — replaces any previous listener,
    which handles route changes automatically (old AppBar is unmounted).
    """
    global _callback
    with _lock:
        _callback = fn


def clear_listener() -> None:
    """Deregister the listener (e.g. on logout)."""
    global _callback
    with _lock:
        _callback = None
