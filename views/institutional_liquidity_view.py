"""
Institutional Liquidity view.

Tracks any symbol on the 1-minute timeframe.  Maintains a 4-hour rolling
buffer per symbol (cached across switches), detects liquidity grabs and
reversals, and renders a dark candlestick chart with 50/200 SMA overlays
and arrow signals.
"""

import asyncio
import json
import logging
import random
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import flet as ft
import flet.canvas as cv

from api.dxlink_streamer import DXLinkStreamer
from views.nav import nav_app_bar

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
DEFAULT_SYMBOL = "MES"

# Quick-access chips shown in the picker bar
QUICK_SYMBOLS = ["MES", "MNQ", "M2K", "MYM", "MGC"]

# ── Futures instrument registry ────────────────────────────────────────────────
# Single source of truth for all supported futures instruments.
# symbol    – canonical code, upper-case, no leading slash
# desc      – human-readable full name
# base      – approximate current price used for demo-mode candle generation
# sector    – instrument category

from dataclasses import dataclass as _dc

@_dc(frozen=True)
class FuturesInstrument:
    symbol: str
    desc:   str
    base:   float
    sector: str


def _reg(*args) -> tuple[str, "FuturesInstrument"]:
    inst = FuturesInstrument(*args)
    return inst.symbol, inst


FUTURES_REGISTRY: dict[str, FuturesInstrument] = dict([
    # ── Equity Index ────────────────────────────────────────────────────────
    _reg("MES",  "Micro E-mini S&P 500",          5220.0,   "Equity Index"),
    _reg("ES",   "E-mini S&P 500",                5220.0,   "Equity Index"),
    _reg("MNQ",  "Micro E-mini Nasdaq-100",       18200.0,  "Equity Index"),
    _reg("NQ",   "E-mini Nasdaq-100",             18200.0,  "Equity Index"),
    _reg("MYM",  "Micro E-mini Dow Jones",        38500.0,  "Equity Index"),
    _reg("YM",   "E-mini Dow Jones",              38500.0,  "Equity Index"),
    _reg("M2K",  "Micro E-mini Russell 2000",      2100.0,  "Equity Index"),
    _reg("RTY",  "E-mini Russell 2000",            2100.0,  "Equity Index"),
    # ── Metals ──────────────────────────────────────────────────────────────
    _reg("MGC",  "Micro Gold",                     2620.0,  "Metals"),
    _reg("GC",   "Gold",                           2620.0,  "Metals"),
    _reg("MSI",  "Micro Silver",                     30.5,  "Metals"),
    _reg("SI",   "Silver",                           30.5,  "Metals"),
    _reg("HG",   "Copper",                            4.5,  "Metals"),
    _reg("PL",   "Platinum",                        960.0,  "Metals"),
    _reg("PA",   "Palladium",                      1000.0,  "Metals"),
    # ── Energy ──────────────────────────────────────────────────────────────
    _reg("MCL",  "Micro Crude Oil (WTI)",            75.0,  "Energy"),
    _reg("CL",   "Crude Oil (WTI)",                  75.0,  "Energy"),
    _reg("NG",   "Natural Gas",                       2.5,  "Energy"),
    _reg("HO",   "Heating Oil",                       2.6,  "Energy"),
    _reg("RB",   "RBOB Gasoline",                     2.4,  "Energy"),
    # ── Interest Rates ──────────────────────────────────────────────────────
    _reg("ZB",   "30-Year U.S. T-Bond",             115.0,  "Rates"),
    _reg("ZN",   "10-Year U.S. T-Note",             109.0,  "Rates"),
    _reg("ZF",   "5-Year U.S. T-Note",              107.0,  "Rates"),
    _reg("ZT",   "2-Year U.S. T-Note",              102.0,  "Rates"),
    _reg("SR3",  "3-Month SOFR",                      94.8,  "Rates"),
    # ── FX ──────────────────────────────────────────────────────────────────
    _reg("6E",   "Euro FX",                           1.08,  "FX"),
    _reg("6J",   "Japanese Yen",                    0.0067,  "FX"),
    _reg("6B",   "British Pound",                     1.27,  "FX"),
    _reg("6A",   "Australian Dollar",                 0.65,  "FX"),
    _reg("6C",   "Canadian Dollar",                   0.74,  "FX"),
    _reg("6S",   "Swiss Franc",                       1.12,  "FX"),
    _reg("6N",   "New Zealand Dollar",                0.60,  "FX"),
    _reg("6M",   "Mexican Peso",                     0.058,  "FX"),
    # ── Agricultural ────────────────────────────────────────────────────────
    _reg("ZC",   "Corn",                             430.0,  "Ag"),
    _reg("ZW",   "Wheat (SRW)",                      550.0,  "Ag"),
    _reg("ZS",   "Soybeans",                        1000.0,  "Ag"),
    _reg("ZM",   "Soybean Meal",                     310.0,  "Ag"),
    _reg("ZL",   "Soybean Oil",                       44.0,  "Ag"),
    _reg("ZO",   "Oats",                             370.0,  "Ag"),
    _reg("KC",   "Coffee",                           195.0,  "Ag"),
    _reg("CT",   "Cotton",                            82.0,  "Ag"),
    _reg("SB",   "Sugar #11",                         20.0,  "Ag"),
    _reg("CC",   "Cocoa",                           8500.0,  "Ag"),
    # ── Livestock ───────────────────────────────────────────────────────────
    _reg("LE",   "Live Cattle",                      190.0,  "Livestock"),
    _reg("GF",   "Feeder Cattle",                    270.0,  "Livestock"),
    _reg("HE",   "Lean Hogs",                         85.0,  "Livestock"),
])

POLL_INTERVAL  = 60    # seconds between polls
BUFFER_MINUTES = 240   # 4-hour candle buffer per symbol
SWING_LOOKBACK = 3     # candles each side for swing identification
SIGNAL_LOOKBACK = 10   # recent candles scanned for grabs
SWING_WINDOW   = 30    # only swings within this many candles are "live"

VISIBLE_CANDLES = 80
CANDLE_STEP     = 9
CANDLE_BODY_W   = 5

CHART_H    = 370
PAD_TOP    = 18
PAD_BOTTOM = 28
PAD_LEFT   = 62
PAD_RIGHT  = 15
LIVE_EDGE_PAD = 3   # empty candle slots reserved at right when at live position
CHART_W    = PAD_LEFT + VISIBLE_CANDLES * CANDLE_STEP + PAD_RIGHT  # ~797 px

# Volume profile
VP_N_BINS = 24   # price buckets
VP_MAX_W  = 54   # max bar width in pixels (stays inside PAD_LEFT=62)

# Colours
COL_BG       = "#111111"
COL_GRID     = "#252525"
COL_LABEL    = "#666666"
COL_WICK     = "#555555"
COL_BULL     = "#26a69a"
COL_BEAR     = "#ef5350"
COL_SMA50    = "#FF9800"
COL_SMA200   = "#42A5F5"
COL_SIG_BULL = "#00E676"
COL_SIG_BEAR = "#FF1744"
COL_CHIP_ACT = "#FF9800"   # active chip colour
COL_VP_BAR   = "#1e3a50"   # volume profile bar (non-POC)
COL_VP_POC   = "#FF9800"   # point of control

# Simulated trade level line colours
COL_TRADE_ENTRY = "#BBBBBB"   # entry price line
COL_TRADE_SL    = "#FF5555"   # stop loss line
COL_TRADE_TP    = "#44DD88"   # take profit line

# Trade simulation parameters
SL_BUFFER = 0.0    # extra points beyond wick tip placed on stop (0 = exact wick tip)
RR_RATIO  = 2.0    # take-profit Risk:Reward multiplier (1:2)


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
    candle_index: int          # absolute index in the buffer list
    direction:    str          # "BULL" or "BEAR"
    level:        float        # grabbed price level
    source:       str = "SWING"  # "SWING" | "4HH" | "4HL" | "PDH" | "PDL"


@dataclass
class SimTrade:
    """One simulated liquidity-grab reversal trade."""
    id:          str
    symbol:      str
    direction:   str            # "BULL" | "BEAR"
    source:      str            # signal source tag
    entry:       float          # signal candle close price
    sl:          float          # stop loss price (at wick tip ± SL_BUFFER)
    tp:          float          # take profit price (entry ± risk * RR_RATIO)
    risk:        float          # |entry − sl| in price points
    opened_at:   float          # unix timestamp of signal candle
    opened_idx:  int            # absolute buffer index of the signal candle
    status:      str = "OPEN"   # "OPEN" | "WIN" | "LOSS"
    closed_at:   Optional[float] = None
    closed_idx:  Optional[int]   = None
    pnl:         float = 0.0    # realised P&L in price points


@dataclass
class KeyLevels:
    """Key price levels used for liquidity-grab detection."""
    h4_high: Optional[float] = None   # 4-hour session high
    h4_low:  Optional[float] = None   # 4-hour session low
    pd_high: Optional[float] = None   # previous day high
    pd_low:  Optional[float] = None   # previous day low


@dataclass
class SymbolState:
    """All per-symbol runtime state.  Lives in the module-level cache."""
    buffer:       deque     = field(default_factory=lambda: deque(maxlen=BUFFER_MINUTES))
    cur_open:     Optional[float] = None
    cur_high:     Optional[float] = None
    cur_low:      Optional[float] = None
    min_start:    float = 0.0
    demo_mode:    bool  = False
    last_sig_key: tuple = ()
    last_update:  float = 0.0  # Unix timestamp of last UI update (throttle)
    key_levels:   KeyLevels = field(default_factory=KeyLevels)
    sim_trades:   list = field(default_factory=list)  # list[SimTrade]


# Module-level symbol cache – persists across symbol switches within a session
_symbol_cache: dict[str, SymbolState] = {}


# ── Filesystem candle cache ─────────────────────────────────────────────────────
_CACHE_DIR = Path.home() / ".mtrade" / "cache" / "candles"
_last_flush: dict[str, float] = {}   # symbol → last flush Unix time


def _cache_path(symbol: str) -> Path:
    return _CACHE_DIR / f"{symbol.upper().lstrip('/')}.json"


def _load_cache(symbol: str) -> list:
    """Load candles from filesystem, filtered to last 4 hours. Returns [] on miss."""
    try:
        raw    = json.loads(_cache_path(symbol).read_text(encoding="utf-8"))
        cutoff = time.time() - BUFFER_MINUTES * 60
        return [
            Candle(timestamp=c["timestamp"], open=c["open"],
                   high=c["high"], low=c["low"], close=c["close"])
            for c in raw if c.get("timestamp", 0) >= cutoff
        ]
    except Exception:
        return []


def _save_cache(symbol: str, candles: list) -> None:
    """Write candles atomically, merging with existing file to retain 48 hours."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(symbol)
        # Merge existing file + new buffer; buffer wins on same timestamp
        merged: dict[float, Candle] = {}
        try:
            for c in json.loads(path.read_text(encoding="utf-8")):
                merged[c["timestamp"]] = Candle(
                    timestamp=c["timestamp"], open=c["open"],
                    high=c["high"], low=c["low"], close=c["close"],
                )
        except Exception:
            pass
        for c in candles:
            merged[c.timestamp] = c
        cutoff  = time.time() - 48 * 3600
        ordered = sorted(
            (c for c in merged.values() if c.timestamp >= cutoff),
            key=lambda c: c.timestamp,
        )
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps([
                {"timestamp": c.timestamp, "open": c.open,
                 "high": c.high, "low": c.low, "close": c.close}
                for c in ordered
            ]),
            encoding="utf-8",
        )
        tmp.replace(path)
    except Exception as exc:
        log.warning("Candle cache write failed for %s: %s", symbol, exc)


def _load_cache_full(symbol: str) -> list:
    """Load all cached candles (up to 48 h) — used for key-level computation."""
    try:
        raw    = json.loads(_cache_path(symbol).read_text(encoding="utf-8"))
        cutoff = time.time() - 48 * 3600
        return [
            Candle(timestamp=c["timestamp"], open=c["open"],
                   high=c["high"], low=c["low"], close=c["close"])
            for c in raw if c.get("timestamp", 0) >= cutoff
        ]
    except Exception:
        return []


def _schedule_flush(symbol: str, candles: list) -> None:
    """Queue background flush, throttled to at most once per 30 s per symbol."""
    now = time.time()
    if now - _last_flush.get(symbol, 0) < 30:
        return
    _last_flush[symbol] = now
    threading.Thread(target=_save_cache, args=(symbol, candles), daemon=True).start()


# ── Simulated trade filesystem cache ───────────────────────────────────────────
_TRADES_DIR = Path.home() / ".mtrade" / "cache" / "sim_trades"


def _trades_path(symbol: str) -> Path:
    return _TRADES_DIR / f"{symbol.upper().lstrip('/')}.json"


def _load_sim_trades(symbol: str) -> list:
    """Load all persisted SimTrade records for *symbol*.  Returns [] on miss."""
    try:
        raw = json.loads(_trades_path(symbol).read_text(encoding="utf-8"))
        trades = []
        for d in raw:
            trades.append(SimTrade(
                id=d["id"], symbol=d["symbol"], direction=d["direction"],
                source=d["source"], entry=d["entry"], sl=d["sl"], tp=d["tp"],
                risk=d["risk"], opened_at=d["opened_at"], opened_idx=d["opened_idx"],
                status=d.get("status", "OPEN"), closed_at=d.get("closed_at"),
                closed_idx=d.get("closed_idx"), pnl=d.get("pnl", 0.0),
            ))
        return trades
    except Exception:
        return []


def _save_sim_trades(symbol: str, trades: list) -> None:
    """Persist all SimTrade records atomically."""
    try:
        _TRADES_DIR.mkdir(parents=True, exist_ok=True)
        data = [
            {
                "id": t.id, "symbol": t.symbol, "direction": t.direction,
                "source": t.source, "entry": t.entry, "sl": t.sl, "tp": t.tp,
                "risk": t.risk, "opened_at": t.opened_at, "opened_idx": t.opened_idx,
                "status": t.status, "closed_at": t.closed_at,
                "closed_idx": t.closed_idx, "pnl": t.pnl,
            }
            for t in trades
        ]
        tmp = _trades_path(symbol).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(_trades_path(symbol))
    except Exception:
        pass


# ── Demo data ──────────────────────────────────────────────────────────────────
def _demo_base(symbol: str) -> float:
    key  = symbol.upper().lstrip("/")
    inst = FUTURES_REGISTRY.get(key)
    return inst.base if inst else 100.0


def _generate_demo_candles(symbol: str, n: int = BUFFER_MINUTES) -> list[Candle]:
    """Produce realistic 1-minute candles scaled to the symbol's price."""
    base = _demo_base(symbol)
    vol  = max(0.25, base * 0.0001)          # ~0.01 % per candle
    rng  = random.Random(int(time.time() // 3600) ^ hash(symbol.upper()))
    candles: list[Candle] = []
    price = base
    t     = time.time() - n * 60
    trend = 0.0
    for i in range(n):
        if i % 45 == 0:
            trend = rng.uniform(-vol * 0.3, vol * 0.3)
        change  = rng.gauss(trend, vol)
        open_   = round(price, 2)
        close   = round(price + change, 2)
        wick_up = abs(rng.gauss(0, vol * 0.5))
        wick_dn = abs(rng.gauss(0, vol * 0.5))
        high    = round(max(open_, close) + wick_up, 2)
        low     = round(min(open_, close) - wick_dn, 2)
        candles.append(Candle(timestamp=t, open=open_, high=high, low=low, close=close))
        price = close
        t += 60
    return candles


def _parse_api_candles(raw: list[dict]) -> list[Candle]:
    result: list[Candle] = []
    for c in raw:
        try:
            t = c.get("time") or c.get("datetime") or c.get("timestamp") or 0
            if isinstance(t, str):
                t = datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
            t = float(t)
            if t > 1e12:
                t /= 1000.0
            result.append(Candle(
                timestamp=t,
                open=float(c.get("open",  0)),
                high=float(c.get("high",  0)),
                low =float(c.get("low",   0)),
                close=float(c.get("close", 0)),
            ))
        except (ValueError, TypeError, KeyError):
            continue
    return result


# ── Algorithm ──────────────────────────────────────────────────────────────────
def _compute_key_levels(all_candles: list) -> KeyLevels:
    """
    Compute 4H High/Low and Previous Day High/Low from candle history.

    The extended (48h) filesystem cache is the data source.  On first run the
    cache may not yet span a full day, in which case pd_high / pd_low are None.
    """
    if not all_candles:
        return KeyLevels()

    now       = time.time()
    prev_date = datetime.fromtimestamp(now - 86400).date()
    h4_cut    = now - 4 * 3600

    h4_candles = [c for c in all_candles if c.timestamp >= h4_cut]
    pd_candles = [c for c in all_candles
                  if datetime.fromtimestamp(c.timestamp).date() == prev_date]

    return KeyLevels(
        h4_high = max((c.high for c in h4_candles), default=None),
        h4_low  = min((c.low  for c in h4_candles), default=None),
        pd_high = max((c.high for c in pd_candles), default=None),
        pd_low  = min((c.low  for c in pd_candles), default=None),
    )


def detect_key_level_signals(candles: list, kl: KeyLevels) -> list:
    """
    Detect liquidity grabs at 4H and Previous Day High/Low levels.

    BEAR grab: wick pierces above level, body closes back below it,
               reversal ≥ 30 % of the wick above the level.
    BULL grab: wick pierces below level, body closes back above it,
               reversal ≥ 30 % of the wick below the level.
    Only the last SIGNAL_LOOKBACK completed candles are scanned.
    """
    if len(candles) < 3:
        return []

    levels = []
    if kl.h4_high is not None: levels.append(("BEAR", kl.h4_high, "4HH"))
    if kl.h4_low  is not None: levels.append(("BULL", kl.h4_low,  "4HL"))
    if kl.pd_high is not None: levels.append(("BEAR", kl.pd_high, "PDH"))
    if kl.pd_low  is not None: levels.append(("BULL", kl.pd_low,  "PDL"))
    if not levels:
        return []

    completed = candles[:-1]
    signals: list = []
    seen: set = set()

    for ci in range(max(0, len(completed) - SIGNAL_LOOKBACK), len(completed)):
        if ci in seen:
            continue
        c = completed[ci]
        for direction, level, src in levels:
            if direction == "BEAR":
                wick_above = c.high - level
                reversal   = level  - c.close
                if wick_above > 0 and c.close < level and reversal >= wick_above * 0.30:
                    signals.append(Signal(candle_index=ci, direction="BEAR",
                                         level=level, source=src))
                    seen.add(ci)
                    break
            else:
                wick_below = level  - c.low
                reversal   = c.close - level
                if wick_below > 0 and c.close > level and reversal >= wick_below * 0.30:
                    signals.append(Signal(candle_index=ci, direction="BULL",
                                         level=level, source=src))
                    seen.add(ci)
                    break

    return signals


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

    Bearish grab – wick swept above a recent swing high, body closed below it.
    Bullish grab – wick swept below a recent swing low, body closed above it.
    """
    if len(candles) < SWING_LOOKBACK * 2 + SIGNAL_LOOKBACK + 2:
        return []

    completed = candles[:-1]
    n = len(completed)

    sh = _swing_highs(completed, SWING_LOOKBACK)
    sl = _swing_lows(completed,  SWING_LOOKBACK)

    recent_sh = [(i, p) for i, p in sh if i > n - SWING_WINDOW]
    recent_sl = [(i, p) for i, p in sl if i > n - SWING_WINDOW]

    signals: list[Signal] = []
    seen:    set[int]     = set()

    for ci in range(max(0, n - SIGNAL_LOOKBACK), n):
        if ci in seen:
            continue
        c = completed[ci]

        for si, level in recent_sh:
            if si >= ci:
                continue
            wick_above = c.high - level
            reversal   = level  - c.close
            if wick_above > 0 and c.close < level and reversal >= wick_above * 0.30:
                signals.append(Signal(candle_index=ci, direction="BEAR", level=level))
                seen.add(ci)
                break

        if ci in seen:
            continue

        for si, level in recent_sl:
            if si >= ci:
                continue
            wick_below = level    - c.low
            reversal   = c.close  - level
            if wick_below > 0 and c.close > level and reversal >= wick_below * 0.30:
                signals.append(Signal(candle_index=ci, direction="BULL", level=level))
                seen.add(ci)
                break

    return signals


# ── Volume profile ─────────────────────────────────────────────────────────────
def _build_volume_profile(
    visible: list,
    mn: float,
    mx: float,
    plot_h: float,
) -> list:
    """
    Build left-anchored volume-profile horizontal bars.

    Proxy volume: each candle distributes weight=1 evenly across every price bin
    that falls within its [low, high] range.  The highest-volume bin (POC) is
    highlighted in orange; all others use a dark teal.
    """
    if not visible or mx <= mn:
        return []

    price_range = mx - mn
    bin_size    = price_range / VP_N_BINS
    bins        = [0.0] * VP_N_BINS

    for c in visible:
        lo = max(c.low,  mn)
        hi = min(c.high, mx)
        if hi < lo:
            continue
        b0 = max(0,           int((lo - mn) / bin_size))
        b1 = min(VP_N_BINS - 1, int((hi - mn) / bin_size))
        n  = b1 - b0 + 1
        w  = 1.0 / n if n > 0 else 0.0
        for b in range(b0, b1 + 1):
            bins[b] += w

    max_vol = max(bins) or 1.0
    poc_idx = bins.index(max_vol)
    bin_h   = plot_h / VP_N_BINS

    shapes = []
    for b in range(VP_N_BINS):
        if bins[b] < 0.01:
            continue
        bar_w = VP_MAX_W * bins[b] / max_vol
        # bin 0 → lowest price → bottom of plot area
        y_top = PAD_TOP + plot_h - (b + 1) * bin_h
        color = COL_VP_POC if b == poc_idx else COL_VP_BAR
        shapes.append(cv.Rect(
            x=0, y=y_top,
            width=bar_w, height=max(1.5, bin_h - 0.5),
            paint=ft.Paint(color=color, style=ft.PaintingStyle.FILL),
        ))

    return shapes


# ── Chart builder ──────────────────────────────────────────────────────────────
def _build_chart(
    candles:     list[Candle],
    sma50:       list[Optional[float]],
    sma200:      list[Optional[float]],
    signals:     list[Signal],
    chart_w:     int                  = CHART_W,
    chart_h:     int                  = CHART_H,
    n_visible:   int                  = VISIBLE_CANDLES,
    key_levels:  Optional[KeyLevels]  = None,
    candle_step: int                  = CANDLE_STEP,
    price_scale: float                = 1.0,
    buf_start:   Optional[int]        = None,
    sim_trades:  Optional[list]       = None,
) -> ft.Control:
    """Render the dark candlestick canvas with SMA lines and signal arrows."""

    if not candles:
        return ft.Container(
            width=chart_w, height=chart_h, bgcolor=COL_BG,
            content=ft.Text("No data yet", color=COL_LABEL),
            alignment=ft.Alignment(0, 0),
        )

    total  = len(candles)
    start  = max(0, min(
        buf_start if buf_start is not None else max(0, total - n_visible),
        max(0, total - n_visible),
    ))
    end        = min(total, start + n_visible)
    visible    = candles[start:end]
    vis_sma50  = (sma50  or [])[start:end]
    vis_sma200 = (sma200 or [])[start:end]
    buf_offset = start

    all_prices: list[float] = []
    for c in visible:
        all_prices += [c.high, c.low]
    for v in vis_sma50:
        if v is not None:
            all_prices.append(v)
    for v in vis_sma200:
        if v is not None:
            all_prices.append(v)

    mn, mx     = min(all_prices), max(all_prices)
    pad        = (mx - mn) * 0.08 / price_scale or 2.0
    mn -= pad;  mx += pad
    price_range = mx - mn
    plot_h      = chart_h - PAD_TOP - PAD_BOTTOM

    def py(price: float) -> float:
        return PAD_TOP + plot_h * (mx - price) / price_range

    def cx(i: int) -> float:
        return PAD_LEFT + i * candle_step + candle_step / 2

    shapes: list[cv.Shape] = []

    # Background
    shapes.append(cv.Rect(
        x=0, y=0, width=chart_w, height=chart_h,
        paint=ft.Paint(color=COL_BG, style=ft.PaintingStyle.FILL),
    ))

    # Volume profile (left-anchored, drawn under grid/labels)
    shapes.extend(_build_volume_profile(visible, mn, mx, float(plot_h)))

    # Grid + price labels
    for gi in range(6):
        level = mn + price_range * gi / 5
        y = py(level)
        shapes.append(cv.Line(
            x1=PAD_LEFT, y1=y, x2=chart_w - PAD_RIGHT, y2=y,
            paint=ft.Paint(color=COL_GRID, stroke_width=0.5),
        ))
        shapes.append(cv.Text(
            x=2, y=y - 7,
            spans=[ft.TextSpan(f"{level:,.1f}", style=ft.TextStyle(size=9, color=COL_LABEL))],
        ))

    # Time labels spaced ~200 px apart
    lbl_every = max(5, 200 // candle_step)
    for i, c in enumerate(visible):
        if i == 0 or i % lbl_every == 0 or i == len(visible) - 1:
            label = datetime.fromtimestamp(c.timestamp).strftime("%H:%M")
            shapes.append(cv.Text(
                x=cx(i) - 13, y=chart_h - PAD_BOTTOM + 5,
                spans=[ft.TextSpan(label, style=ft.TextStyle(size=8, color=COL_LABEL))],
            ))

    # Key levels: dashed horizontal lines for 4H H/L (gray) and PDH/PDL (gold)
    if key_levels is not None:
        for kl_attr, kl_color, kl_label in [
            ("h4_high", "#909090", "4H H"),
            ("h4_low",  "#909090", "4H L"),
            ("pd_high", "#FFD700", "PDH"),
            ("pd_low",  "#FFD700", "PDL"),
        ]:
            kl_price = getattr(key_levels, kl_attr, None)
            if kl_price is None or not (mn <= kl_price <= mx):
                continue
            ky = py(kl_price)
            # Dashed line: 8px on / 5px off
            kx = float(PAD_LEFT)
            while kx < chart_w - PAD_RIGHT:
                kx2 = min(kx + 8, float(chart_w - PAD_RIGHT))
                shapes.append(cv.Line(
                    x1=kx, y1=ky, x2=kx2, y2=ky,
                    paint=ft.Paint(color=kl_color, stroke_width=1.0),
                ))
                kx += 13.0
            # Label at right edge of plot
            shapes.append(cv.Text(
                x=chart_w - PAD_RIGHT - 32, y=ky - 8,
                spans=[ft.TextSpan(kl_label, style=ft.TextStyle(size=8, color=kl_color))],
            ))

    # SMA 200 (blue)
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

    # SMA 50 (orange)
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

    # Candles
    body_w = max(1, int(candle_step * 0.55))
    for i, c in enumerate(visible):
        x     = cx(i)
        color = COL_BULL if c.close >= c.open else COL_BEAR
        shapes.append(cv.Line(
            x1=x, y1=py(c.high), x2=x, y2=py(c.low),
            paint=ft.Paint(color=COL_WICK, stroke_width=1),
        ))
        body_top = py(max(c.open, c.close))
        body_bot = py(min(c.open, c.close))
        body_h   = max(1.5, body_bot - body_top)
        shapes.append(cv.Rect(
            x=x - body_w / 2, y=body_top,
            width=body_w, height=body_h,
            paint=ft.Paint(color=color, style=ft.PaintingStyle.FILL),
        ))

    # Signal arrows
    for sig in signals:
        vi = sig.candle_index - buf_offset
        if not 0 <= vi < len(visible):
            continue
        c = visible[vi]
        x = cx(vi)
        if sig.direction == "BULL":
            tip_y = py(c.low) + 14
            shapes.append(cv.Path(
                elements=[
                    cv.Path.MoveTo(x,     tip_y - 11),
                    cv.Path.LineTo(x - 7, tip_y),
                    cv.Path.LineTo(x + 7, tip_y),
                    cv.Path.Close(),
                ],
                paint=ft.Paint(color=COL_SIG_BULL, style=ft.PaintingStyle.FILL),
            ))
        else:
            tip_y = py(c.high) - 3
            shapes.append(cv.Path(
                elements=[
                    cv.Path.MoveTo(x,     tip_y + 11),
                    cv.Path.LineTo(x - 7, tip_y),
                    cv.Path.LineTo(x + 7, tip_y),
                    cv.Path.Close(),
                ],
                paint=ft.Paint(color=COL_SIG_BEAR, style=ft.PaintingStyle.FILL),
            ))

    # ── Simulated trade levels: entry / SL / TP horizontal dashed lines ───────
    if sim_trades:
        for trade in sim_trades:
            vi_open = trade.opened_idx - buf_offset
            # Show trade if its open candle is visible on screen
            if vi_open < 0 or vi_open >= len(visible):
                continue
            x_start = cx(vi_open)

            # x_end: stop at the close candle (if resolved and visible) else right edge
            if trade.closed_idx is not None:
                vi_cl   = trade.closed_idx - buf_offset
                x_end   = cx(vi_cl) if 0 <= vi_cl < len(visible) else float(chart_w - PAD_RIGHT)
            else:
                x_end   = float(chart_w - PAD_RIGHT)
            x_end = min(x_end, float(chart_w - PAD_RIGHT))

            sw = 1.5 if trade.status == "OPEN" else 0.8   # thinner once closed

            for price_val, col, lbl in (
                (trade.entry, COL_TRADE_ENTRY, "E"),
                (trade.sl,    COL_TRADE_SL,    "SL"),
                (trade.tp,    COL_TRADE_TP,    "TP"),
            ):
                if not (mn <= price_val <= mx):
                    continue
                y  = py(price_val)
                kx = x_start
                while kx < x_end:
                    kx2 = min(kx + 6.0, x_end)
                    shapes.append(cv.Line(
                        x1=kx, y1=y, x2=kx2, y2=y,
                        paint=ft.Paint(color=col, stroke_width=sw),
                    ))
                    kx += 10.0
                # Small label anchored at the open candle
                shapes.append(cv.Text(
                    x=x_start + 2, y=y - 7,
                    spans=[ft.TextSpan(lbl, style=ft.TextStyle(size=7, color=col))],
                ))

            # Result badge near the close candle
            if trade.status in ("WIN", "LOSS") and trade.closed_idx is not None:
                vi_cl = trade.closed_idx - buf_offset
                if 0 <= vi_cl < len(visible):
                    badge_price = trade.tp  if trade.status == "WIN" else trade.sl
                    badge_col   = COL_TRADE_TP if trade.status == "WIN" else COL_TRADE_SL
                    pnl_sign    = "+" if trade.pnl >= 0 else ""
                    badge_lbl   = f"{'W' if trade.status == 'WIN' else 'L'} {pnl_sign}{trade.pnl:.1f}"
                    if mn <= badge_price <= mx:
                        shapes.append(cv.Text(
                            x=cx(vi_cl) - 10, y=py(badge_price) - 14,
                            spans=[ft.TextSpan(
                                badge_lbl,
                                style=ft.TextStyle(size=8, color=badge_col,
                                                   weight=ft.FontWeight.BOLD),
                            )],
                        ))

    # In-chart legend (row 1: SMAs)
    lx, ly = PAD_LEFT + 4, PAD_TOP + 4
    shapes += [
        cv.Line(x1=lx,      y1=ly + 4, x2=lx + 12, y2=ly + 4,
                paint=ft.Paint(color=COL_SMA50,  stroke_width=2)),
        cv.Text(x=lx + 14,  y=ly - 2,
                spans=[ft.TextSpan("SMA 50",  style=ft.TextStyle(size=9, color=COL_SMA50))]),
        cv.Line(x1=lx + 62, y1=ly + 4, x2=lx + 74, y2=ly + 4,
                paint=ft.Paint(color=COL_SMA200, stroke_width=2)),
        cv.Text(x=lx + 76,  y=ly - 2,
                spans=[ft.TextSpan("SMA 200", style=ft.TextStyle(size=9, color=COL_SMA200))]),
    ]
    # In-chart legend (row 2: key levels)
    ly2 = ly + 14
    shapes += [
        cv.Line(x1=lx,      y1=ly2 + 4, x2=lx + 12, y2=ly2 + 4,
                paint=ft.Paint(color="#FFD700", stroke_width=1.5)),
        cv.Text(x=lx + 14,  y=ly2 - 2,
                spans=[ft.TextSpan("PDH/PDL", style=ft.TextStyle(size=9, color="#FFD700"))]),
        cv.Line(x1=lx + 62, y1=ly2 + 4, x2=lx + 74, y2=ly2 + 4,
                paint=ft.Paint(color="#909090", stroke_width=1.5)),
        cv.Text(x=lx + 76,  y=ly2 - 2,
                spans=[ft.TextSpan("4H H/L",  style=ft.TextStyle(size=9, color="#909090"))]),
    ]

    return cv.Canvas(shapes=shapes, width=chart_w, height=chart_h)


def _symbol_desc(sym: str) -> str:
    """Return the human-readable description for a futures instrument, or empty string."""
    key  = sym.upper().lstrip("/")
    inst = FUTURES_REGISTRY.get(key)
    return inst.desc if inst else ""


# ── Main view builder ──────────────────────────────────────────────────────────
def build_institutional_liquidity_view(client, page: ft.Page) -> ft.View:
    """Institutional Liquidity view with per-symbol caching and picker."""

    # ── Active symbol ─────────────────────────────────────────────────────────
    active_symbol: list[str] = [DEFAULT_SYMBOL]

    # ── Stream task reference (cancel to stop/switch) ─────────────────────────
    stream_task: list = [None]  # list[asyncio.Task | None]

    # ── Alert / sound state ───────────────────────────────────────────────────
    alert_enabled: list[bool] = [True]
    sound_enabled: list[bool] = [True]
    alert_task:    list        = [None]  # list[asyncio.Task | None]

    # ── Pan / zoom state ──────────────────────────────────────────────────────
    view_offset: list[int]   = [0]            # candles offset from right edge (0 = live)
    candle_w:    list[int]   = [CANDLE_STEP]  # horizontal px per candle (zoom X)
    price_scale: list[float] = [1.0]          # Y zoom factor (>1 = tighter range)
    pan_accum:   list[float] = [0.0]          # fractional sub-candle pan accumulator

    # ── UI refs ───────────────────────────────────────────────────────────────
    chart_container_ref = ft.Ref[ft.Container]()
    chart_area_ref      = ft.Ref[ft.Container]()
    alert_ref           = ft.Ref[ft.Container]()
    alert_text_ref      = ft.Ref[ft.Text]()
    price_ref           = ft.Ref[ft.Text]()
    signal_ref          = ft.Ref[ft.Text]()
    status_ref          = ft.Ref[ft.Text]()
    symbol_label_ref    = ft.Ref[ft.Text]()
    desc_ref            = ft.Ref[ft.Text]()
    demo_banner_ref     = ft.Ref[ft.Container]()
    chip_row_ref        = ft.Ref[ft.Container]()
    symbol_field_ref    = ft.Ref[ft.TextField]()
    live_btn_ref        = ft.Ref[ft.TextButton]()
    zoom_lbl_ref        = ft.Ref[ft.Text]()
    stats_ref           = ft.Ref[ft.Text]()

    # ── Dynamic chart dimensions ───────────────────────────────────────────────
    # Overhead: AppBar ~56px + padding 32px + header 70px + picker 40px +
    #           spacing 60px + legend 28px + stats 18px + status 20px ≈ 324px
    _OVERHEAD = 324

    def _get_chart_dims() -> tuple[int, int, int]:
        """Return (chart_w, chart_h, n_visible) based on current page size."""
        pw = max(480, int(page.width or 800))
        ph = max(400, int(page.height or 600))
        cw = pw - 32   # 16px padding each side
        ch = max(200, ph - _OVERHEAD)
        nv = max(20, (cw - PAD_LEFT - PAD_RIGHT) // candle_w[0])
        return cw, ch, nv

    def _compute_buf_start(total: int, nv: int) -> int:
        """Clamp view_offset and return the starting candle index for the viewport.

        LIVE_EDGE_PAD empty slots are kept on the right when at the live edge,
        preventing the newest candle from bleeding to the right edge.
        """
        slots = nv - LIVE_EDGE_PAD          # data slots; right PAD slots stay empty at live
        max_off = max(0, total - slots)
        view_offset[0] = min(view_offset[0], max_off)
        return max(0, total - slots - view_offset[0])

    # ── Cache helpers ─────────────────────────────────────────────────────────
    def _state() -> SymbolState:
        sym = active_symbol[0]
        if sym not in _symbol_cache:
            _symbol_cache[sym] = SymbolState()
        return _symbol_cache[sym]

    def _load_for_symbol(sym: str) -> None:
        """Ensure a SymbolState exists for sym.  Data is loaded by the stream loop."""
        if sym not in _symbol_cache:
            state = SymbolState()
            state.sim_trades = _load_sim_trades(sym)
            _symbol_cache[sym] = state

    # ── Demo banner ───────────────────────────────────────────────────────────
    def _demo_banner_widget(is_demo: bool) -> ft.Control:
        if not is_demo:
            return ft.Container(height=0)
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.WARNING_ROUNDED, color="#1C1917", size=16),
                    ft.Text(
                        "DEMO DATA  ·  API unavailable  ·  Prices are simulated",
                        size=12,
                        color="#1C1917",
                        weight=ft.FontWeight.W_700,
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor="#FBBF24",
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=12, vertical=8),
        )

    # ── Resize-only update ────────────────────────────────────────────────────
    def _rebuild_chart_dims() -> None:
        """Recompute chart dimensions and redraw canvas.  Safe to call with no data."""
        chart_w, chart_h, n_visible = _get_chart_dims()

        if chart_area_ref.current:
            chart_area_ref.current.height = chart_h + 4
            chart_area_ref.current.update()

        state   = _state()
        candles = list(state.buffer)
        if chart_container_ref.current:
            sma50   = _compute_sma(candles, 50)   if candles else []
            sma200  = _compute_sma(candles, 200)  if candles else []
            kl_sigs = detect_key_level_signals(candles, state.key_levels) if candles else []
            kl_idxs = {s.candle_index for s in kl_sigs}
            sw_sigs = [s for s in (detect_signals(candles) if candles else [])
                       if s.candle_index not in kl_idxs]
            signals = kl_sigs + sw_sigs
            buf_start = _compute_buf_start(len(candles), n_visible)
            chart_container_ref.current.content = _build_chart(
                candles, sma50, sma200, signals, chart_w, chart_h, n_visible,
                key_levels=state.key_levels,
                candle_step=candle_w[0],
                price_scale=price_scale[0],
                buf_start=buf_start,
                sim_trades=state.sim_trades,
            )
            chart_container_ref.current.update()

        page.update()

    # ── Alert helpers ─────────────────────────────────────────────────────────
    def _trigger_sound() -> None:
        """Play a short beep in a daemon thread (Windows only, silent on others)."""
        if not sound_enabled[0]:
            return
        def _beep() -> None:
            try:
                import winsound
                winsound.Beep(880, 120)
            except Exception:
                pass
        threading.Thread(target=_beep, daemon=True).start()

    async def _flash_alert(is_bull: bool, label: str, level: float) -> None:
        """Show the alert overlay, blink 3×, then fade out."""
        if not alert_enabled[0]:
            return
        if alert_ref.current is None or alert_text_ref.current is None:
            return

        bg_color = ft.Colors.GREEN_700 if is_bull else ft.Colors.RED_700
        alert_text_ref.current.value = f"Liquidity Grab: {label}  @ {level:.2f}"
        alert_ref.current.bgcolor    = bg_color
        alert_ref.current.opacity    = 1.0
        alert_ref.current.visible    = True
        alert_ref.current.update()

        # Blink 3×
        for _ in range(3):
            await asyncio.sleep(0.20)
            alert_ref.current.opacity = 0.0
            alert_ref.current.update()
            await asyncio.sleep(0.20)
            alert_ref.current.opacity = 1.0
            alert_ref.current.update()

        # Hold, then fade out
        await asyncio.sleep(1.5)
        alert_ref.current.opacity = 0.0
        alert_ref.current.update()
        await asyncio.sleep(0.40)   # wait for animated fade
        alert_ref.current.visible = False
        alert_ref.current.update()

    # ── Trade simulation helpers ───────────────────────────────────────────────
    def _open_sim_trade(sig: Signal, candles: list) -> None:
        """Create a SimTrade for *sig* if one doesn't already exist for that candle."""
        state = _state()
        # Guard: skip if a trade is already tracked for this signal candle
        if any(t.opened_idx == sig.candle_index for t in state.sim_trades):
            return
        if sig.candle_index >= len(candles):
            return
        c     = candles[sig.candle_index]
        entry = c.close
        if sig.direction == "BULL":
            sl   = round(c.low  - SL_BUFFER, 4)
            risk = round(entry - sl,  4)
            tp   = round(entry + risk * RR_RATIO, 4)
        else:
            sl   = round(c.high + SL_BUFFER, 4)
            risk = round(sl - entry, 4)
            tp   = round(entry - risk * RR_RATIO, 4)
        if risk <= 0:
            return  # degenerate (flat) candle — skip
        sym   = active_symbol[0]
        trade = SimTrade(
            id=uuid.uuid4().hex, symbol=sym,
            direction=sig.direction, source=sig.source,
            entry=entry, sl=sl, tp=tp, risk=risk,
            opened_at=c.timestamp, opened_idx=sig.candle_index,
        )
        state.sim_trades.append(trade)
        threading.Thread(
            target=_save_sim_trades, args=(sym, state.sim_trades), daemon=True,
        ).start()

    def _resolve_open_trades(candles: list) -> None:
        """Check every OPEN trade against completed candles and close if SL/TP hit."""
        sym   = active_symbol[0]
        state = _state()
        changed = False
        for trade in state.sim_trades:
            if trade.status != "OPEN":
                continue
            # Only scan completed candles (exclude the last in-progress one)
            for ci in range(trade.opened_idx + 1, len(candles) - 1):
                c = candles[ci]
                if trade.direction == "BULL":
                    if c.low <= trade.sl:          # SL hit (takes priority)
                        trade.status    = "LOSS"
                        trade.pnl       = -trade.risk
                        trade.closed_at  = c.timestamp
                        trade.closed_idx = ci
                        changed = True
                        break
                    if c.high >= trade.tp:         # TP hit
                        trade.status    = "WIN"
                        trade.pnl       = round(trade.risk * RR_RATIO, 4)
                        trade.closed_at  = c.timestamp
                        trade.closed_idx = ci
                        changed = True
                        break
                else:  # BEAR
                    if c.high >= trade.sl:         # SL hit (takes priority)
                        trade.status    = "LOSS"
                        trade.pnl       = -trade.risk
                        trade.closed_at  = c.timestamp
                        trade.closed_idx = ci
                        changed = True
                        break
                    if c.low <= trade.tp:          # TP hit
                        trade.status    = "WIN"
                        trade.pnl       = round(trade.risk * RR_RATIO, 4)
                        trade.closed_at  = c.timestamp
                        trade.closed_idx = ci
                        changed = True
                        break
        if changed:
            threading.Thread(
                target=_save_sim_trades, args=(sym, state.sim_trades), daemon=True,
            ).start()

    def _update_stats() -> None:
        """Refresh the sim-trade stats row."""
        if not stats_ref.current:
            return
        trades = _state().sim_trades
        wins   = sum(1 for t in trades if t.status == "WIN")
        losses = sum(1 for t in trades if t.status == "LOSS")
        opens  = sum(1 for t in trades if t.status == "OPEN")
        closed = wins + losses
        acc    = (wins / closed * 100) if closed else 0.0
        total_pnl = sum(t.pnl for t in trades)
        pnl_str   = f"{total_pnl:+.2f}" if trades else "—"
        acc_str   = f"{acc:.0f}%" if closed else "—"
        stats_ref.current.value = (
            f"Sim Trades:  {wins}W / {losses}L"
            + (f" / {opens} open" if opens else "")
            + f"    Accuracy: {acc_str}    P&L: {pnl_str} pts"
        )
        stats_ref.current.update()

    # ── UI update ─────────────────────────────────────────────────────────────
    def _update_ui() -> None:
        sym     = active_symbol[0]
        state   = _state()
        candles = list(state.buffer)
        if not candles:
            return

        sma50   = _compute_sma(candles, 50)
        sma200  = _compute_sma(candles, 200)

        # Key-level grabs take priority; swing signals fill in non-overlapping candles
        kl_sigs = detect_key_level_signals(candles, state.key_levels)
        kl_idxs = {s.candle_index for s in kl_sigs}
        sw_sigs = [s for s in detect_signals(candles) if s.candle_index not in kl_idxs]
        signals = kl_sigs + sw_sigs

        # Open a simulated trade for every signal that doesn't have one yet
        for sig in signals:
            _open_sim_trade(sig, candles)

        # Resolve any open trades against completed candles
        _resolve_open_trades(candles)

        chart_w, chart_h, n_visible = _get_chart_dims()
        buf_start = _compute_buf_start(len(candles), n_visible)

        # Chart canvas
        if chart_container_ref.current:
            chart_container_ref.current.content = _build_chart(
                candles, sma50, sma200, signals, chart_w, chart_h, n_visible,
                key_levels=state.key_levels,
                candle_step=candle_w[0],
                price_scale=price_scale[0],
                buf_start=buf_start,
                sim_trades=state.sim_trades,
            )
            chart_container_ref.current.update()

        # Stats bar
        _update_stats()

        # Chart area height (tracks dynamic chart_h)
        if chart_area_ref.current:
            chart_area_ref.current.height = chart_h + 4
            chart_area_ref.current.update()

        # Live button visibility
        if live_btn_ref.current:
            live_btn_ref.current.visible = view_offset[0] > 0
            live_btn_ref.current.update()

        # Price
        if price_ref.current:
            price_ref.current.value = f"${candles[-1].close:,.2f}"
            price_ref.current.update()

        # Signal text + snackbar on new signal
        if signals:
            latest  = signals[-1]
            key     = (latest.candle_index, latest.direction)
            is_bull  = latest.direction == "BULL"
            src_tag  = f" [{latest.source}]" if latest.source != "SWING" else ""
            label    = f"▲ BULL reversal{src_tag}" if is_bull else f"▼ BEAR reversal{src_tag}"
            color    = COL_SIG_BULL if is_bull else COL_SIG_BEAR

            if signal_ref.current:
                signal_ref.current.value = f"SIGNAL: {label}  @ {latest.level:.2f}"
                signal_ref.current.color = color
                signal_ref.current.update()

            if key != state.last_sig_key:
                state.last_sig_key = key
                snack_bg = ft.Colors.GREEN_700 if is_bull else ft.Colors.RED_700
                page.snack_bar = ft.SnackBar(
                    content=ft.Text(
                        f"[{sym}] Liquidity Grab: {label} near {latest.level:.2f}",
                        color="white",
                    ),
                    bgcolor=snack_bg,
                    open=True,
                )
                page.update()
                # Cancel any running alert animation, then start a new one
                if alert_task[0] is not None:
                    alert_task[0].cancel()
                alert_task[0] = asyncio.create_task(
                    _flash_alert(is_bull, label, latest.level)
                )
                _trigger_sound()
        else:
            if signal_ref.current:
                signal_ref.current.value = "Scanning for liquidity grabs…"
                signal_ref.current.color = COL_LABEL
                signal_ref.current.update()

        if status_ref.current:
            mode = "Demo" if state.demo_mode else "Live"
            ts   = datetime.fromtimestamp(candles[-1].timestamp).strftime("%H:%M")
            status_ref.current.value = (
                f"{sym}  ·  {mode}  ·  {len(candles)} candles  ·  last {ts}  ·  "
                f"4h buffer  ·  1m"
            )
            status_ref.current.update()

        page.update()

    # ── Demo tick (used when DXLink unavailable) ──────────────────────────────
    def _tick_demo() -> None:
        """Advance a random-walk price candle for the current demo symbol."""
        sym   = active_symbol[0]
        state = _state()
        if not state.demo_mode or not state.buffer:
            return
        now   = time.time()
        vol   = max(0.25, _demo_base(sym) * 0.0001)
        price = round(state.buffer[-1].close + random.gauss(0, vol), 2)
        current_min = now - (now % 60)
        if state.min_start == 0.0:
            state.min_start = current_min
            state.cur_open  = price
            state.cur_high  = price
            state.cur_low   = price
        elif current_min > state.min_start:
            state.buffer.append(Candle(
                timestamp=state.min_start,
                open =state.cur_open,
                high =state.cur_high,
                low  =state.cur_low,
                close=price,
            ))
            state.min_start = current_min
            state.cur_open  = price
            state.cur_high  = price
            state.cur_low   = price
        else:
            state.cur_high = max(state.cur_high or price, price)
            state.cur_low  = min(state.cur_low  or price, price)
        _update_ui()

    # ── DXLink candle event handler ───────────────────────────────────────────
    def _safe_float(v) -> Optional[float]:
        """Convert a value to float, returning None for NaN / null / 0."""
        if v is None:
            return None
        try:
            f = float(v)
            return None if f != f else f   # f != f is True only for NaN
        except (TypeError, ValueError):
            return None

    def _process_candle_event(sym: str, candle_dict: dict) -> None:
        """Process one Candle event from DXLink and update the buffer."""
        if sym != active_symbol[0]:
            return  # stale event from a previous symbol, ignore
        state = _state()
        try:
            t_ms = float(candle_dict.get("time") or 0)
            if t_ms <= 0:
                return
            t = t_ms / 1000.0
            o = _safe_float(candle_dict.get("open"))
            h = _safe_float(candle_dict.get("high"))
            l = _safe_float(candle_dict.get("low"))
            c = _safe_float(candle_dict.get("close"))
            if not all(v is not None and v > 0 for v in (o, h, l, c)):
                return  # skip NaN / in-flight zero candles
        except (TypeError, ValueError):
            return

        candle = Candle(timestamp=t, open=o, high=h, low=l, close=c)

        # Update existing candle for the same minute, or append new one
        if state.buffer and abs(state.buffer[-1].timestamp - t) < 1.0:
            state.buffer[-1] = candle   # same-minute update
        elif not state.buffer or t > state.buffer[-1].timestamp:
            state.buffer.append(candle)
        # else: older historical candle already in buffer — skip

        # Throttle UI redraws to ≤2 per second during history replay
        now = time.time()
        if now - state.last_update >= 0.5:
            state.last_update = now
            _update_ui()

        # Background filesystem flush (live candles only, throttled ≤ 1/30 s)
        if not state.demo_mode:
            _schedule_flush(sym, list(state.buffer))

    # ── Stream loop (DXLink → demo fallback) ─────────────────────────────────
    async def _stream_loop() -> None:
        sym   = active_symbol[0]
        state = _state()
        live_ok = False
        try:
            token_data  = client.get_quote_token()
            token       = token_data.get("token") or token_data.get("dxlink-token", "")
            dxlink_url  = (
                token_data.get("dxlink-url")
                or token_data.get("websocket-url", "")
            )
            if not token or not dxlink_url:
                raise ValueError(f"Missing token/URL in quote token response: {token_data}")

            # Look up the exact DXLink streamer-symbol (e.g. /MESU26:XCME)
            streamer_sym = client.get_futures_streamer_symbol(sym)
            log.info("DXLink streamer-symbol for %s → %s", sym, streamer_sym)

            # ── Seed buffer from filesystem cache ──────────────────────────────
            state.buffer.clear()
            cached = _load_cache(sym)
            if cached:
                for c in cached:
                    state.buffer.append(c)
                from_time_ms = int(cached[-1].timestamp * 1000) + 1
                log.info("Seeded %d candles from cache for %s; DXLink gap-fill from %d",
                         len(cached), sym, from_time_ms)
            else:
                from_time_ms = int((time.time() - BUFFER_MINUTES * 60) * 1000)
                log.info("No cache for %s; requesting full 4h from DXLink", sym)

            state.demo_mode = False
            _refresh_demo_banner()

            # Compute key levels from the extended (48h) cache
            all_cached = _load_cache_full(sym)
            if all_cached:
                state.key_levels = _compute_key_levels(all_cached)
                log.info(
                    "Key levels for %s: 4HH=%s 4HL=%s PDH=%s PDL=%s",
                    sym,
                    state.key_levels.h4_high, state.key_levels.h4_low,
                    state.key_levels.pd_high, state.key_levels.pd_low,
                )

            if state.buffer:
                _update_ui()   # render cached data immediately while DXLink connects

            streamer = DXLinkStreamer(dxlink_url, token)
            live_ok  = True

            def on_candle(candle_dict: dict) -> None:
                _process_candle_event(sym, candle_dict)

            await streamer.stream_candles(
                symbol=streamer_sym,
                from_time_ms=from_time_ms,
                on_candle=on_candle,
            )

        except asyncio.CancelledError:
            raise  # propagate — task was cancelled by symbol switch or page close
        except Exception as exc:
            log.warning("DXLink stream failed for %s: %s — switching to demo", sym, exc)

        # ── Demo fallback ──────────────────────────────────────────────────
        if not live_ok and sym == active_symbol[0]:
            state.demo_mode = True
            if not state.buffer:
                cached = _load_cache(sym)
                if cached:
                    for c in cached:
                        state.buffer.append(c)
                    log.info("Demo mode seeded from cache: %d candles for %s", len(cached), sym)
                else:
                    for c in _generate_demo_candles(sym):
                        state.buffer.append(c)
                now = time.time()
                state.min_start = now - (now % 60)
            _refresh_demo_banner()
            _update_ui()

            while sym == active_symbol[0]:
                await asyncio.sleep(float(POLL_INTERVAL))
                if sym == active_symbol[0]:
                    _tick_demo()

    # ── Demo banner refresh helper ─────────────────────────────────────────────
    def _refresh_demo_banner() -> None:
        state = _state()
        if demo_banner_ref.current:
            demo_banner_ref.current.content = _demo_banner_widget(state.demo_mode)
            demo_banner_ref.current.update()

    # ── Symbol switch ─────────────────────────────────────────────────────────
    def _switch_symbol(new_sym: str) -> None:
        new_sym = new_sym.strip().upper()
        if not new_sym or new_sym == active_symbol[0]:
            return

        # Cancel the current stream / demo loop
        if stream_task[0] is not None:
            stream_task[0].cancel()

        # Switch active symbol
        active_symbol[0] = new_sym

        # Ensure a SymbolState exists (no-op if already cached)
        _load_for_symbol(new_sym)

        # Clear last-signal key so a snackbar fires for this symbol's signals
        _state().last_sig_key = ()

        # Start fresh stream loop
        task = asyncio.create_task(_stream_loop())
        stream_task[0] = task

        # Update symbol label and description in header
        if symbol_label_ref.current:
            symbol_label_ref.current.value = f"{new_sym}  /  1m"
            symbol_label_ref.current.update()
        if desc_ref.current:
            desc_ref.current.value = _symbol_desc(new_sym)
            desc_ref.current.update()

        # Update demo banner visibility
        _refresh_demo_banner()

        # Refresh chip row (highlight new active)
        if chip_row_ref.current:
            chip_row_ref.current.content = _build_chip_row()
            chip_row_ref.current.update()

        # Clear text field
        if symbol_field_ref.current:
            symbol_field_ref.current.value = ""
            symbol_field_ref.current.update()

        _update_ui()

    # ── Gesture handlers ──────────────────────────────────────────────────────
    def _on_pan_start(e) -> None:
        pan_accum[0] = 0.0

    def _on_pan_update(e) -> None:
        dx = e.local_delta.x if e.local_delta is not None else 0.0
        pan_accum[0] += -dx                  # drag left → positive → older data
        delta = int(pan_accum[0] / candle_w[0])
        if delta != 0:
            pan_accum[0] -= delta * candle_w[0]
            total = len(list(_state().buffer))
            _, _, nv = _get_chart_dims()
            view_offset[0] = max(0, min(view_offset[0] + delta, max(0, total - nv)))
            _update_ui()

    def _on_scroll(e) -> None:
        sdx = e.scroll_delta.x if e.scroll_delta is not None else 0.0
        sdy = e.scroll_delta.y if e.scroll_delta is not None else 0.0
        if abs(sdx) >= abs(sdy):
            # Horizontal component → pan time axis
            total = len(list(_state().buffer))
            _, _, nv = _get_chart_dims()
            change = -1 if sdx > 0 else 1   # right = newer = offset decreases
            view_offset[0] = max(0, min(view_offset[0] + change, max(0, total - nv)))
        else:
            # Vertical component → zoom X  (up = zoom in = wider candles)
            candle_w[0] = max(3, min(30, candle_w[0] + (1 if sdy < 0 else -1)))
            if zoom_lbl_ref.current:
                zoom_lbl_ref.current.value = str(candle_w[0])
                zoom_lbl_ref.current.update()
        _update_ui()

    # ── Zoom / live-edge helpers ───────────────────────────────────────────────
    def _zoom_x(delta: int) -> None:
        candle_w[0] = max(3, min(30, candle_w[0] + delta))
        if zoom_lbl_ref.current:
            zoom_lbl_ref.current.value = str(candle_w[0])
            zoom_lbl_ref.current.update()
        _update_ui()

    def _zoom_y(delta: int) -> None:
        price_scale[0] = (min(8.0, price_scale[0] * 1.3)
                          if delta > 0 else max(0.2, price_scale[0] / 1.3))
        _update_ui()

    def _jump_to_live() -> None:
        view_offset[0] = 0
        _update_ui()

    # ── Chip row builder ──────────────────────────────────────────────────────
    def _build_chip_row() -> ft.Control:
        active  = active_symbol[0]
        chips: list[ft.Control] = []
        for sym in QUICK_SYMBOLS:
            if sym == active:
                chips.append(ft.FilledButton(
                    sym,
                    on_click=lambda e, s=sym: _switch_symbol(s),
                    style=ft.ButtonStyle(
                        bgcolor=COL_CHIP_ACT,
                        color="black",
                        padding=ft.padding.symmetric(horizontal=10, vertical=4),
                    ),
                ))
            else:
                chips.append(ft.OutlinedButton(
                    sym,
                    on_click=lambda e, s=sym: _switch_symbol(s),
                    style=ft.ButtonStyle(
                        color=COL_LABEL,
                        side=ft.BorderSide(1, "#3a3a3a"),
                        padding=ft.padding.symmetric(horizontal=10, vertical=4),
                    ),
                ))
        return ft.Row(chips, spacing=6, wrap=True)

    # ── Bootstrap ─────────────────────────────────────────────────────────────
    _load_for_symbol(DEFAULT_SYMBOL)
    task = asyncio.create_task(_stream_loop())
    stream_task[0] = task

    # Wire up resize handler — rebuilds chart dimensions whenever window changes
    page.on_resized = lambda _: _rebuild_chart_dims()

    # Initial display — buffer is empty until DXLink delivers first candles
    init_chart_w, init_chart_h, init_n_visible = _get_chart_dims()
    candles_init = list(_state().buffer)
    sma50_init   = _compute_sma(candles_init, 50)
    sma200_init  = _compute_sma(candles_init, 200)
    signals_init = detect_signals(candles_init)

    # ── Initial display values ────────────────────────────────────────────────
    last_price_str = f"${candles_init[-1].close:,.2f}" if candles_init else "Connecting…"
    if signals_init:
        latest  = signals_init[-1]
        is_bull = latest.direction == "BULL"
        sig_txt   = f"SIGNAL: {'▲ BULL reversal' if is_bull else '▼ BEAR reversal'}  @ {latest.level:.2f}"
        sig_color = COL_SIG_BULL if is_bull else COL_SIG_BEAR
        _state().last_sig_key = (latest.candle_index, latest.direction)
    else:
        sig_txt   = "Connecting to DXLink stream…"
        sig_color = COL_LABEL

    mode_txt   = "Demo" if _state().demo_mode else "Live"
    status_txt = (
        f"{DEFAULT_SYMBOL}  ·  {mode_txt}  ·  {len(candles_init)} candles  ·  "
        f"4h buffer  ·  1m"
    )

    # ── Layout ────────────────────────────────────────────────────────────────
    user_info = client.user or {}
    username  = user_info.get("username") or user_info.get("email") or "User"

    # Symbol picker: quick chips + custom text field
    symbol_picker = ft.Row(
        [
            ft.Container(
                ref=chip_row_ref,
                content=_build_chip_row(),
            ),
            ft.Container(expand=True),
            ft.TextField(
                ref=symbol_field_ref,
                hint_text="Symbol…",
                width=90,
                text_size=13,
                height=36,
                content_padding=ft.padding.symmetric(horizontal=8, vertical=4),
                bgcolor="#1e1e1e",
                border_color="#3a3a3a",
                focused_border_color=COL_CHIP_ACT,
                color="white",
                hint_style=ft.TextStyle(color=COL_LABEL),
                cursor_color="white",
                on_submit=lambda e: _switch_symbol(e.control.value),
            ),
            ft.IconButton(
                icon=ft.Icons.SEARCH,
                icon_color=COL_CHIP_ACT,
                icon_size=20,
                tooltip="Load symbol",
                on_click=lambda e: _switch_symbol(
                    symbol_field_ref.current.value if symbol_field_ref.current else ""
                ),
            ),
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        wrap=False,
    )

    # Alert overlay — positioned top-right inside the chart Stack
    alert_overlay = ft.Container(
        ref=alert_ref,
        content=ft.Text(
            ref=alert_text_ref,
            value="",
            size=13,
            color="white",
            weight=ft.FontWeight.W_600,
        ),
        visible=False,
        opacity=1.0,
        animate_opacity=ft.Animation(200, ft.AnimationCurve.EASE_IN_OUT),
        bgcolor=ft.Colors.GREEN_700,
        border_radius=6,
        padding=ft.padding.symmetric(horizontal=12, vertical=8),
        right=8,
        top=8,
    )

    # Chart area — height tracks page height dynamically via _update_ui / on_resized
    chart_area = ft.Container(
        ref=chart_area_ref,
        content=ft.Stack(
            [
                ft.Container(
                    ref=chart_container_ref,
                    content=_build_chart(
                        candles_init, sma50_init, sma200_init, signals_init,
                        init_chart_w, init_chart_h, init_n_visible,
                        candle_step=candle_w[0],
                        price_scale=price_scale[0],
                        buf_start=max(0, len(candles_init) - init_n_visible),
                        sim_trades=_state().sim_trades,
                    ),
                ),
                alert_overlay,
            ],
        ),
        height=init_chart_h + 4,
        bgcolor=COL_BG,
        border_radius=8,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        border=ft.border.all(1, "#2a2a2a"),
    )

    # Legend
    legend = ft.Row(
        [
            ft.Container(width=16, height=3, bgcolor=COL_SMA50,  border_radius=2),
            ft.Text("SMA 50",  size=12, color=COL_SMA50),
            ft.Container(width=10),
            ft.Container(width=16, height=3, bgcolor=COL_SMA200, border_radius=2),
            ft.Text("SMA 200", size=12, color=COL_SMA200),
            ft.Container(width=10),
            ft.Text("▲", size=14, color=COL_SIG_BULL, weight=ft.FontWeight.BOLD),
            ft.Text("Bull grab reversal", size=12, color=COL_SIG_BULL),
            ft.Container(width=10),
            ft.Text("▼", size=14, color=COL_SIG_BEAR, weight=ft.FontWeight.BOLD),
            ft.Text("Bear grab reversal", size=12, color=COL_SIG_BEAR),
            ft.Container(expand=True),
            ft.Checkbox(
                label="Alert",
                value=True,
                active_color=COL_CHIP_ACT,
                check_color="white",
                label_style=ft.TextStyle(size=12, color=COL_LABEL),
                on_change=lambda e: alert_enabled.__setitem__(0, e.control.value),
            ),
            ft.Container(width=4),
            ft.Checkbox(
                label="Sound",
                value=True,
                active_color=COL_CHIP_ACT,
                check_color="white",
                label_style=ft.TextStyle(size=12, color=COL_LABEL),
                on_change=lambda e: sound_enabled.__setitem__(0, e.control.value),
            ),
        ],
        spacing=4,
        wrap=False,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    body = ft.Column(
        controls=[
            ft.Container(ref=demo_banner_ref, content=_demo_banner_widget(_state().demo_mode)),
            ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(
                                ref=symbol_label_ref,
                                value=f"{DEFAULT_SYMBOL}  /  1m",
                                size=13,
                                color=COL_LABEL,
                                weight=ft.FontWeight.W_500,
                            ),
                            ft.Text(
                                ref=desc_ref,
                                value=_symbol_desc(DEFAULT_SYMBOL),
                                size=11,
                                color="#4a4a4a",
                                weight=ft.FontWeight.W_400,
                            ),
                            ft.Text(
                                ref=price_ref,
                                value=last_price_str,
                                size=30,
                                weight=ft.FontWeight.BOLD,
                                color="white",
                            ),
                        ],
                        spacing=1,
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

            # Symbol picker
            symbol_picker,

            # Chart toolbar
            ft.Row([
                ft.Text("X:", size=11, color=COL_LABEL),
                ft.IconButton(ft.Icons.REMOVE, icon_size=16, icon_color=COL_LABEL,
                              tooltip="Zoom out candles", on_click=lambda e: _zoom_x(-1)),
                ft.Text(ref=zoom_lbl_ref, value=str(CANDLE_STEP), size=11, color=COL_LABEL),
                ft.IconButton(ft.Icons.ADD, icon_size=16, icon_color=COL_LABEL,
                              tooltip="Zoom in candles", on_click=lambda e: _zoom_x(1)),
                ft.Container(width=6),
                ft.Text("Y:", size=11, color=COL_LABEL),
                ft.IconButton(ft.Icons.UNFOLD_MORE, icon_size=16, icon_color=COL_LABEL,
                              tooltip="Zoom out price", on_click=lambda e: _zoom_y(-1)),
                ft.IconButton(ft.Icons.UNFOLD_LESS, icon_size=16, icon_color=COL_LABEL,
                              tooltip="Zoom in price", on_click=lambda e: _zoom_y(1)),
                ft.Container(expand=True),
                ft.TextButton(
                    ref=live_btn_ref,
                    content=ft.Text("● LIVE", size=11, color=COL_CHIP_ACT,
                                    weight=ft.FontWeight.W_600),
                    visible=False,
                    tooltip="Jump to live edge",
                    on_click=lambda e: _jump_to_live(),
                ),
            ], spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER, height=28),

            # Candlestick chart wrapped in gesture detector
            ft.GestureDetector(
                content=chart_area,
                on_pan_start=_on_pan_start,
                on_pan_update=_on_pan_update,
                on_scroll=_on_scroll,
            ),

            # Legend
            legend,

            # Sim trade stats
            ft.Text(
                ref=stats_ref,
                value="Sim Trades:  0W / 0L    Accuracy: —    P&L: — pts",
                size=11,
                color=COL_LABEL,
            ),

            # Status line
            ft.Text(
                ref=status_ref,
                value=status_txt,
                size=11,
                color=COL_LABEL,
            ),
        ],
        spacing=10,
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