"""
Institutional Liquidity view.

Tracks MES on the 1-minute timeframe.  Maintains a 4-hour rolling buffer of
OHLC candles, detects liquidity grabs (stop hunts) and their reversals, and
renders a dark candlestick chart with 50/200 SMA overlays and arrow signals.
"""

import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import flet as ft
import flet.canvas as cv

from views.nav import nav_app_bar

# ── Constants ──────────────────────────────────────────────────────────────────
SYMBOL = "MES"
POLL_INTERVAL = 60          # seconds between price polls
BUFFER_MINUTES = 240        # 4 hours of 1-min candles
SWING_LOOKBACK = 3          # candles each side to confirm swing high/low
SIGNAL_LOOKBACK = 10        # recent candles to scan for grabs
SWING_WINDOW = 30           # only look at swings within this many recent candles

VISIBLE_CANDLES = 80        # candles rendered on chart
CANDLE_STEP = 9             # px allocated per candle
CANDLE_BODY_W = 5           # px candle body width

CHART_H = 370
PAD_TOP = 18
PAD_BOTTOM = 28
PAD_LEFT = 62
PAD_RIGHT = 15
CHART_W = PAD_LEFT + VISIBLE_CANDLES * CANDLE_STEP + PAD_RIGHT  # ~797 px

# Colours
COL_BG = "#111111"
COL_GRID = "#252525"
COL_LABEL = "#666666"
COL_WICK = "#555555"
COL_BULL = "#26a69a"
COL_BEAR = "#ef5350"
COL_SMA50 = "#FF9800"
COL_SMA200 = "#42A5F5"
COL_SIG_BULL = "#00E676"
COL_SIG_BEAR = "#FF1744"


# ── Data classes ───────────────────────────────────────────────────────────────
@dataclass
class Candle:
    timestamp: float
    open: float
    high: float
    low: float
    close: float


@dataclass
class Signal:
    candle_index: int   # absolute index in the full buffer list
    direction: str      # "BULL" or "BEAR"
    level: float        # price level that was grabbed


# ── Demo data ──────────────────────────────────────────────────────────────────
def _generate_demo_candles(n: int = BUFFER_MINUTES, base: float = 5220.0) -> list[Candle]:
    """Produce realistic MES 1-minute candles for demo / API-unavailable mode."""
    rng = random.Random(int(time.time() // 3600))  # stable seed within the hour
    candles: list[Candle] = []
    price = base
    t = time.time() - n * 60
    trend = 0.0
    for i in range(n):
        if i % 45 == 0:
            trend = rng.uniform(-0.12, 0.12)
        change = rng.gauss(trend, 0.40)
        open_ = round(price, 2)
        close = round(price + change, 2)
        wick_up = abs(rng.gauss(0, 0.20))
        wick_dn = abs(rng.gauss(0, 0.20))
        high = round(max(open_, close) + wick_up, 2)
        low = round(min(open_, close) - wick_dn, 2)
        candles.append(Candle(timestamp=t, open=open_, high=high, low=low, close=close))
        price = close
        t += 60
    return candles


def _parse_api_candles(raw: list[dict]) -> list[Candle]:
    """Convert raw API dicts to Candle objects, tolerating various field names."""
    result: list[Candle] = []
    for c in raw:
        try:
            t = c.get("time") or c.get("datetime") or c.get("timestamp") or 0
            if isinstance(t, str):
                t = datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
            t = float(t)
            # Some APIs return milliseconds
            if t > 1e12:
                t /= 1000.0
            result.append(Candle(
                timestamp=t,
                open=float(c.get("open", 0)),
                high=float(c.get("high", 0)),
                low=float(c.get("low", 0)),
                close=float(c.get("close", 0)),
            ))
        except (ValueError, TypeError, KeyError):
            continue
    return result


# ── Algorithm ──────────────────────────────────────────────────────────────────
def _compute_sma(candles: list[Candle], period: int) -> list[Optional[float]]:
    closes = [c.close for c in candles]
    result: list[Optional[float]] = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(closes[i - period + 1: i + 1]) / period)
    return result


def _swing_highs(candles: list[Candle], lookback: int = SWING_LOOKBACK) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    limit = len(candles) - lookback
    for i in range(lookback, limit):
        h = candles[i].high
        if all(h >= candles[j].high for j in range(i - lookback, i + lookback + 1) if j != i):
            out.append((i, h))
    return out


def _swing_lows(candles: list[Candle], lookback: int = SWING_LOOKBACK) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    limit = len(candles) - lookback
    for i in range(lookback, limit):
        l = candles[i].low
        if all(l <= candles[j].low for j in range(i - lookback, i + lookback + 1) if j != i):
            out.append((i, l))
    return out


def detect_signals(candles: list[Candle]) -> list[Signal]:
    """
    Detect institutional liquidity grabs with reversal confirmation.

    Bearish grab  – wick swept above a recent swing high, candle closed
                    back below that level (sell-side stop hunt).
    Bullish grab  – wick swept below a recent swing low, candle closed
                    back above that level (buy-side stop hunt).

    Only returns signals for the most recent SIGNAL_LOOKBACK candles.
    """
    if len(candles) < SWING_LOOKBACK * 2 + SIGNAL_LOOKBACK + 2:
        return []

    # Compute swings on all candles except the final incomplete one
    completed = candles[:-1]
    n = len(completed)

    sh = _swing_highs(completed, SWING_LOOKBACK)
    sl = _swing_lows(completed, SWING_LOOKBACK)

    recent_sh = [(i, p) for i, p in sh if i > n - SWING_WINDOW]
    recent_sl = [(i, p) for i, p in sl if i > n - SWING_WINDOW]

    signals: list[Signal] = []
    seen: set[int] = set()

    for ci in range(max(0, n - SIGNAL_LOOKBACK), n):
        if ci in seen:
            continue
        c = completed[ci]

        # ── Bearish grab: wick above swing high, body closed below ──────────
        for si, level in recent_sh:
            if si >= ci:
                continue
            wick_above = c.high - level
            reversal = level - c.close
            if wick_above > 0 and c.close < level and reversal >= wick_above * 0.30:
                signals.append(Signal(candle_index=ci, direction="BEAR", level=level))
                seen.add(ci)
                break

        if ci in seen:
            continue

        # ── Bullish grab: wick below swing low, body closed above ───────────
        for si, level in recent_sl:
            if si >= ci:
                continue
            wick_below = level - c.low
            reversal = c.close - level
            if wick_below > 0 and c.close > level and reversal >= wick_below * 0.30:
                signals.append(Signal(candle_index=ci, direction="BULL", level=level))
                seen.add(ci)
                break

    return signals


# ── Chart builder ──────────────────────────────────────────────────────────────
def _build_chart(
    candles: list[Candle],
    sma50: list[Optional[float]],
    sma200: list[Optional[float]],
    signals: list[Signal],
) -> ft.Control:
    """Render a dark candlestick canvas chart with SMA lines and signal arrows."""

    if not candles:
        return ft.Container(
            width=CHART_W, height=CHART_H,
            bgcolor=COL_BG,
            content=ft.Text("No data yet", color=COL_LABEL),
            alignment=ft.alignment.center,
        )

    # ── Slice to visible window ────────────────────────────────────────────────
    visible = candles[-VISIBLE_CANDLES:]
    vis_sma50 = (sma50 or [])[-VISIBLE_CANDLES:]
    vis_sma200 = (sma200 or [])[-VISIBLE_CANDLES:]
    buf_offset = len(candles) - len(visible)  # index shift for signals

    # ── Price range for Y axis ────────────────────────────────────────────────
    all_prices: list[float] = []
    for c in visible:
        all_prices += [c.high, c.low]
    for v in vis_sma50:
        if v is not None:
            all_prices.append(v)
    for v in vis_sma200:
        if v is not None:
            all_prices.append(v)

    mn, mx = min(all_prices), max(all_prices)
    pad = (mx - mn) * 0.08 or 2.0
    mn -= pad
    mx += pad
    price_range = mx - mn

    plot_h = CHART_H - PAD_TOP - PAD_BOTTOM

    def py(price: float) -> float:
        """Price → canvas Y (0 = top)."""
        return PAD_TOP + plot_h * (mx - price) / price_range

    def cx(i: int) -> float:
        """Visible candle index → canvas X centre."""
        return PAD_LEFT + i * CANDLE_STEP + CANDLE_STEP / 2

    shapes: list[cv.Shape] = []

    # ── Background ────────────────────────────────────────────────────────────
    shapes.append(cv.Rect(
        x=0, y=0, width=CHART_W, height=CHART_H,
        paint=ft.Paint(color=COL_BG, style=ft.PaintingStyle.FILL),
    ))

    # ── Horizontal grid lines + price labels ──────────────────────────────────
    for gi in range(6):
        level = mn + price_range * gi / 5
        y = py(level)
        shapes.append(cv.Line(
            x1=PAD_LEFT, y1=y, x2=CHART_W - PAD_RIGHT, y2=y,
            paint=ft.Paint(color=COL_GRID, stroke_width=0.5),
        ))
        shapes.append(cv.Text(
            x=2, y=y - 7,
            spans=[ft.TextSpan(f"{level:,.1f}", style=ft.TextStyle(size=9, color=COL_LABEL))],
        ))

    # ── Time labels every 30 candles ──────────────────────────────────────────
    for i, c in enumerate(visible):
        if i == 0 or i % 30 == 0 or i == len(visible) - 1:
            label = datetime.fromtimestamp(c.timestamp).strftime("%H:%M")
            shapes.append(cv.Text(
                x=cx(i) - 13, y=CHART_H - PAD_BOTTOM + 5,
                spans=[ft.TextSpan(label, style=ft.TextStyle(size=8, color=COL_LABEL))],
            ))

    # ── SMA 200 (blue) ────────────────────────────────────────────────────────
    prev200: Optional[tuple[float, float]] = None
    for i, v in enumerate(vis_sma200):
        if v is not None:
            pt = (cx(i), py(v))
            if prev200 is not None:
                shapes.append(cv.Line(
                    x1=prev200[0], y1=prev200[1], x2=pt[0], y2=pt[1],
                    paint=ft.Paint(color=COL_SMA200, stroke_width=1.5),
                ))
            prev200 = pt

    # ── SMA 50 (orange) ───────────────────────────────────────────────────────
    prev50: Optional[tuple[float, float]] = None
    for i, v in enumerate(vis_sma50):
        if v is not None:
            pt = (cx(i), py(v))
            if prev50 is not None:
                shapes.append(cv.Line(
                    x1=prev50[0], y1=prev50[1], x2=pt[0], y2=pt[1],
                    paint=ft.Paint(color=COL_SMA50, stroke_width=1.5),
                ))
            prev50 = pt

    # ── Candles ───────────────────────────────────────────────────────────────
    for i, c in enumerate(visible):
        x = cx(i)
        color = COL_BULL if c.close >= c.open else COL_BEAR

        # Wick (high→low)
        shapes.append(cv.Line(
            x1=x, y1=py(c.high), x2=x, y2=py(c.low),
            paint=ft.Paint(color=COL_WICK, stroke_width=1),
        ))

        # Body (open↔close)
        body_top = py(max(c.open, c.close))
        body_bot = py(min(c.open, c.close))
        body_h = max(1.5, body_bot - body_top)
        shapes.append(cv.Rect(
            x=x - CANDLE_BODY_W / 2, y=body_top,
            width=CANDLE_BODY_W, height=body_h,
            paint=ft.Paint(color=color, style=ft.PaintingStyle.FILL),
        ))

    # ── Signal arrows ─────────────────────────────────────────────────────────
    for sig in signals:
        vi = sig.candle_index - buf_offset
        if not 0 <= vi < len(visible):
            continue
        c = visible[vi]
        x = cx(vi)

        if sig.direction == "BULL":
            # Upward triangle below the candle low
            tip_y = py(c.low) + 14
            shapes.append(cv.Path(
                elements=[
                    cv.Path.MoveTo(x, tip_y - 11),       # apex (up)
                    cv.Path.LineTo(x - 7, tip_y),         # base-left
                    cv.Path.LineTo(x + 7, tip_y),         # base-right
                    cv.Path.Close(),
                ],
                paint=ft.Paint(color=COL_SIG_BULL, style=ft.PaintingStyle.FILL),
            ))
        else:
            # Downward triangle above the candle high
            tip_y = py(c.high) - 3
            shapes.append(cv.Path(
                elements=[
                    cv.Path.MoveTo(x, tip_y + 11),       # apex (down)
                    cv.Path.LineTo(x - 7, tip_y),         # base-left
                    cv.Path.LineTo(x + 7, tip_y),         # base-right
                    cv.Path.Close(),
                ],
                paint=ft.Paint(color=COL_SIG_BEAR, style=ft.PaintingStyle.FILL),
            ))

    # ── In-chart legend ───────────────────────────────────────────────────────
    lx, ly = PAD_LEFT + 4, PAD_TOP + 4
    shapes += [
        cv.Line(x1=lx, y1=ly + 4, x2=lx + 12, y2=ly + 4,
                paint=ft.Paint(color=COL_SMA50, stroke_width=2)),
        cv.Text(x=lx + 14, y=ly - 2,
                spans=[ft.TextSpan("SMA 50", style=ft.TextStyle(size=9, color=COL_SMA50))]),
        cv.Line(x1=lx + 62, y1=ly + 4, x2=lx + 74, y2=ly + 4,
                paint=ft.Paint(color=COL_SMA200, stroke_width=2)),
        cv.Text(x=lx + 76, y=ly - 2,
                spans=[ft.TextSpan("SMA 200", style=ft.TextStyle(size=9, color=COL_SMA200))]),
    ]

    return cv.Canvas(shapes=shapes, width=CHART_W, height=CHART_H)


# ── Main view builder ──────────────────────────────────────────────────────────
def build_institutional_liquidity_view(client, page: ft.Page) -> ft.View:
    """Institutional Liquidity view: MES 1-min candles, liquidity-grab detection."""

    # ── Mutable state (closure vars) ──────────────────────────────────────────
    candle_buffer: deque[Candle] = deque(maxlen=BUFFER_MINUTES)
    cur_open: list[Optional[float]] = [None]
    cur_high: list[Optional[float]] = [None]
    cur_low: list[Optional[float]] = [None]
    min_start: list[float] = [0.0]
    last_sig_key: list[tuple] = [()]
    demo_mode: list[bool] = [False]

    polling_stop = asyncio.Event()

    # ── UI refs ───────────────────────────────────────────────────────────────
    chart_container_ref = ft.Ref[ft.Container]()
    chart_row_ref = ft.Ref[ft.Row]()
    price_ref = ft.Ref[ft.Text]()
    signal_ref = ft.Ref[ft.Text]()
    status_ref = ft.Ref[ft.Text]()

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _get_price() -> Optional[float]:
        try:
            raw = client.get_market_quotes([SYMBOL])
            if isinstance(raw, dict) and SYMBOL in raw:
                v = raw[SYMBOL]
                if isinstance(v, dict):
                    p = v.get("last-price") or v.get("last") or v.get("price")
                    return float(p) if p is not None else None
            if isinstance(raw, list):
                for item in raw:
                    sym = item.get("symbol") or item.get("instrument-symbol")
                    if sym == SYMBOL:
                        p = item.get("last-price") or item.get("last") or item.get("price")
                        return float(p) if p is not None else None
        except Exception:
            pass
        # Demo fallback: random walk from last candle
        if candle_buffer:
            return round(candle_buffer[-1].close + random.gauss(0, 0.40), 2)
        return None

    def _load_initial():
        """Fill the candle buffer with historical data or demo candles."""
        raw = []
        try:
            raw = client.get_candle_history(SYMBOL, n_minutes=BUFFER_MINUTES)
        except Exception:
            pass

        if raw:
            parsed = _parse_api_candles(raw)
            for c in parsed:
                candle_buffer.append(c)
        else:
            demo_mode[0] = True
            for c in _generate_demo_candles():
                candle_buffer.append(c)

        if candle_buffer:
            last = candle_buffer[-1]
            cur_open[0] = last.close
            cur_high[0] = last.close
            cur_low[0] = last.close
            now = time.time()
            min_start[0] = now - (now % 60)

    def _update_ui():
        """Recompute indicators / signals and refresh all UI components."""
        candles = list(candle_buffer)
        if not candles:
            return

        sma50 = _compute_sma(candles, 50)
        sma200 = _compute_sma(candles, 200)
        signals = detect_signals(candles)

        # Chart: replace content (same pattern as chart_view.py)
        if chart_container_ref.current:
            chart_container_ref.current.content = _build_chart(candles, sma50, sma200, signals)
            chart_container_ref.current.update()

        # Price
        if price_ref.current:
            price_ref.current.value = f"${candles[-1].close:,.2f}"
            price_ref.current.update()

        # Signal text + snackbar on new signal
        if signals:
            latest = signals[-1]
            key = (latest.candle_index, latest.direction)
            is_bull = latest.direction == "BULL"
            label = "▲ BULL reversal" if is_bull else "▼ BEAR reversal"
            color = COL_SIG_BULL if is_bull else COL_SIG_BEAR

            if signal_ref.current:
                signal_ref.current.value = f"SIGNAL: {label}  @ {latest.level:.2f}"
                signal_ref.current.color = color
                signal_ref.current.update()

            if key != last_sig_key[0]:
                last_sig_key[0] = key
                snack_color = ft.Colors.GREEN_700 if is_bull else ft.Colors.RED_700
                page.snack_bar = ft.SnackBar(
                    content=ft.Text(
                        f"Liquidity Grab: {label} near {latest.level:.2f}",
                        color="white",
                    ),
                    bgcolor=snack_color,
                    open=True,
                )
                page.update()
        else:
            if signal_ref.current:
                signal_ref.current.value = "Scanning for liquidity grabs…"
                signal_ref.current.color = COL_LABEL
                signal_ref.current.update()

        # Status
        if status_ref.current:
            mode_txt = "Demo" if demo_mode[0] else "Live"
            ts = datetime.fromtimestamp(candles[-1].timestamp).strftime("%H:%M")
            status_ref.current.value = (
                f"{mode_txt}  ·  {len(candles)} candles  ·  last {ts}  ·  "
                f"4h buffer  ·  1m timeframe"
            )
            status_ref.current.update()

        # Scroll chart to newest candles (right edge)
        if chart_row_ref.current:
            chart_row_ref.current.scroll_to(offset=CHART_W, duration=150)

        page.update()

    def _tick():
        """Accumulate intra-minute ticks; close candle on minute boundary."""
        now = time.time()
        price = _get_price()
        if price is None:
            return

        current_min = now - (now % 60)

        if min_start[0] == 0.0:
            min_start[0] = current_min
            cur_open[0] = price
            cur_high[0] = price
            cur_low[0] = price
        elif current_min > min_start[0]:
            # Close the completed 1-minute candle
            candle_buffer.append(Candle(
                timestamp=min_start[0],
                open=cur_open[0],
                high=cur_high[0],
                low=cur_low[0],
                close=price,
            ))
            # Open a new candle
            min_start[0] = current_min
            cur_open[0] = price
            cur_high[0] = price
            cur_low[0] = price
        else:
            cur_high[0] = max(cur_high[0], price)
            cur_low[0] = min(cur_low[0], price)

        _update_ui()

    async def _poll_loop():
        while not polling_stop.is_set():
            try:
                await asyncio.wait_for(polling_stop.wait(), timeout=float(POLL_INTERVAL))
            except asyncio.TimeoutError:
                pass
            if polling_stop.is_set():
                break
            _tick()

    # ── Bootstrap ─────────────────────────────────────────────────────────────
    _load_initial()
    candles_init = list(candle_buffer)
    sma50_init = _compute_sma(candles_init, 50)
    sma200_init = _compute_sma(candles_init, 200)
    signals_init = detect_signals(candles_init)

    asyncio.create_task(_poll_loop())

    # ── Initial display values ────────────────────────────────────────────────
    last_price = candles_init[-1].close if candles_init else 0.0
    if signals_init:
        latest = signals_init[-1]
        is_bull = latest.direction == "BULL"
        sig_txt = f"SIGNAL: {'▲ BULL reversal' if is_bull else '▼ BEAR reversal'}  @ {latest.level:.2f}"
        sig_color = COL_SIG_BULL if is_bull else COL_SIG_BEAR
        last_sig_key[0] = (latest.candle_index, latest.direction)
    else:
        sig_txt = "Scanning for liquidity grabs…"
        sig_color = COL_LABEL

    mode_txt = "Demo" if demo_mode[0] else "Live"
    status_txt = (
        f"{mode_txt}  ·  {len(candles_init)} candles  ·  4h buffer  ·  1m timeframe"
    )

    # ── Layout ────────────────────────────────────────────────────────────────
    user_info = client.user or {}
    username = user_info.get("username") or user_info.get("email") or "User"

    initial_chart = _build_chart(candles_init, sma50_init, sma200_init, signals_init)

    chart_area = ft.Container(
        content=ft.Row(
            ref=chart_row_ref,
            controls=[
                ft.Container(
                    ref=chart_container_ref,
                    content=initial_chart,
                )
            ],
            scroll=ft.ScrollMode.AUTO,
        ),
        height=CHART_H + 4,
        bgcolor=COL_BG,
        border_radius=8,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        border=ft.border.all(1, "#2a2a2a"),
    )

    legend = ft.Row(
        [
            ft.Container(width=16, height=3, bgcolor=COL_SMA50, border_radius=2),
            ft.Text("SMA 50", size=12, color=COL_SMA50),
            ft.Container(width=10),
            ft.Container(width=16, height=3, bgcolor=COL_SMA200, border_radius=2),
            ft.Text("SMA 200", size=12, color=COL_SMA200),
            ft.Container(width=10),
            ft.Text("▲", size=14, color=COL_SIG_BULL, weight=ft.FontWeight.BOLD),
            ft.Text("Bull grab reversal", size=12, color=COL_SIG_BULL),
            ft.Container(width=10),
            ft.Text("▼", size=14, color=COL_SIG_BEAR, weight=ft.FontWeight.BOLD),
            ft.Text("Bear grab reversal", size=12, color=COL_SIG_BEAR),
        ],
        spacing=4,
        wrap=True,
    )

    body = ft.Column(
        controls=[
            # Header: symbol + price + signal
            ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(
                                f"{SYMBOL}  /  1m", size=13,
                                color=COL_LABEL, weight=ft.FontWeight.W_500,
                            ),
                            ft.Text(
                                ref=price_ref,
                                value=f"${last_price:,.2f}",
                                size=30,
                                weight=ft.FontWeight.BOLD,
                                color="white",
                            ),
                        ],
                        spacing=2,
                    ),
                    ft.Container(expand=True),
                    ft.Text(
                        ref=signal_ref,
                        value=sig_txt,
                        size=13,
                        color=sig_color,
                        weight=ft.FontWeight.W_600,
                        text_align=ft.TextAlign.RIGHT,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),

            # Candlestick chart
            chart_area,

            # Legend row
            legend,

            # Status line
            ft.Text(
                ref=status_ref,
                value=status_txt,
                size=11,
                color=COL_LABEL,
            ),
        ],
        spacing=12,
        expand=True,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    return ft.View(
        route="/liquidity",
        bgcolor=COL_BG,
        controls=[
            nav_app_bar(page, "Institutional Liquidity", "/liquidity", username),
            ft.SafeArea(
                content=ft.Container(content=body, expand=True, padding=16),
                expand=True,
            ),
        ],
    )