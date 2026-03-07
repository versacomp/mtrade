"""CME Globex market-hours utilities.

CME Globex equity-index micro-futures (MES, MNQ, M2K, MYM, MGC …) trade
nearly 24 hours, 5 days a week on the following schedule (all times US Eastern):

  Open        : Sunday 6:00 PM ET
  Daily break : 5:00 PM – 6:00 PM ET (Monday through Friday)
  Weekend close: Friday 5:00 PM ET  →  Sunday 6:00 PM ET

Note: CME-observed holidays are not modelled here.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def _et_now() -> datetime:
    return datetime.now(_ET)


def is_market_open(dt: datetime | None = None) -> bool:
    """Return True if CME Globex equity-index futures are currently trading.

    Pass *dt* (timezone-aware or ET-naive) to test a specific moment;
    defaults to the current wall-clock time.
    """
    now = dt if dt is not None else _et_now()
    # Normalise to ET if caller passed a naive datetime
    if now.tzinfo is None:
        now = now.replace(tzinfo=_ET)

    wd = now.weekday()          # 0 = Monday … 6 = Sunday
    t  = now.hour * 60 + now.minute  # minutes since midnight ET

    if wd == 5:                         # Saturday — always closed
        return False
    if wd == 6 and t < 18 * 60:        # Sunday before 6:00 PM
        return False
    if wd == 4 and t >= 17 * 60:       # Friday at/after 5:00 PM
        return False
    if 17 * 60 <= t < 18 * 60:         # Daily maintenance 5–6 PM (Mon–Thu)
        return False
    return True


def seconds_until_open(dt: datetime | None = None) -> float:
    """Return seconds until the next market open (0.0 if already open)."""
    now = dt if dt is not None else _et_now()
    if is_market_open(now):
        return 0.0
    return _seconds_until_transition(now)


def market_status(dt: datetime | None = None) -> tuple[bool, str]:
    """Return ``(is_open, human-readable description)``.

    Examples::

        (True,  "Open · closes in 6h 22m")
        (False, "Closed · opens in 1h 45m")
    """
    now  = dt if dt is not None else _et_now()
    open_ = is_market_open(now)
    secs  = _seconds_until_transition(now)
    h, rem = divmod(int(secs), 3600)
    m = rem // 60
    t_str = f"{h}h {m:02d}m" if h else f"{m}m"
    if open_:
        return True, f"Open \u00b7 closes in {t_str}"
    else:
        return False, f"Closed \u00b7 opens in {t_str}"


def _seconds_until_transition(dt: datetime) -> float:
    """Seconds until the next open/close boundary from *dt*."""
    now_state = is_market_open(dt)
    # Step forward in 1-minute increments; transition always falls on the hour
    probe = dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(8 * 24 * 60):       # cap at 8 days (covers full weekend + holidays)
        if is_market_open(probe) != now_state:
            return max(0.0, (probe - dt).total_seconds())
        probe += timedelta(minutes=1)
    return float(8 * 24 * 3600)        # fallback: 8 days
