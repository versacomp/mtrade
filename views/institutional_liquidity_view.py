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
from typing import Callable, Optional

import flet as ft
import flet.canvas as cv

import api.connection_status as cs
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
COL_TRADE_ENTRY   = "#BBBBBB"   # entry price line
COL_TRADE_SL      = "#FF5555"   # stop loss — stage 0 (original)
COL_TRADE_SL_BE   = "#FFD700"   # stop loss — stage 1 (breakeven)
COL_TRADE_SL_TRAIL= "#44DD88"   # stop loss — stage 2 (trailing profit lock)
COL_TRADE_TP      = "#44DD88"   # take profit line

# Trade simulation parameters
SL_BUFFER       = 0.0    # extra points beyond wick tip placed on stop (0 = exact wick tip)
RR_RATIO        = 2.0    # take-profit Risk:Reward multiplier (1:2)
MAX_OPEN_TRADES = 1      # hard cap on concurrent open sim trades
RE_ENTRY_DELAY  = 120    # seconds to wait before checking re-entry after an opposing flip

# Ratcheting stop — profit-protection thresholds (fraction of TP distance from entry)
BE_TRIGGER    = 0.50   # move SL to breakeven when price reaches 50 % of the way to TP
TRAIL_TRIGGER = 0.75   # start trailing when price reaches 75 % of the way to TP
TRAIL_OFFSET  = 0.50   # trailing SL sits risk × TRAIL_OFFSET below/above the peak price

# DXLink reconnect / exponential back-off
RECONNECT_BASE_DELAY = 5    # seconds before the first retry
RECONNECT_MAX_DELAY  = 60   # ceiling on back-off delay
RECONNECT_MAX_TRIES  = 10   # attempts before giving up and switching to demo

# RSI sub-panel
RSI_PERIOD  = 14
RSI_PANEL_H = 75   # pixel height of RSI sub-panel
RSI_GAP     = 6    # pixel gap between candle area and RSI panel

COL_RSI    = "#9C27B0"   # RSI line (purple)
COL_RSI_OB = "#FF5555"   # overbought level (≥70)
COL_RSI_OS = "#44DD88"   # oversold level (≤30)

# ADX / Range Rotation
ADX_PERIOD    = 14
ADX_THRESHOLD = 25       # below → ranging; above → trending
RANGE_PERIOD  = 20       # rolling window for range band (swing high/low lookback)
RANGE_PROX    = 0.25     # grabbed level must be within this fraction of range width from band

COL_ADX        = "#00BCD4"   # ADX line (cyan)
COL_ADX_THRESH = "#1A5050"   # ADX threshold line at 25
COL_RANGE      = "#1A4040"   # range band curve colour


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
    source:       str  = "SWING"  # "SWING" | "4HH" | "4HL" | "PDH" | "PDL"
    divergence:   bool = False    # True when RSI divergence confirms the signal
    pro_trend:    bool = False    # True when signal is aligned with the SMA200 macro trend
    in_range:     bool = False    # True when ADX < threshold and level is at range boundary


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
    # Ratcheting stop state
    sl_stage:    int            = 0     # 0=original  1=breakeven  2=trailing
    peak_price:  Optional[float] = None  # best price seen since entry (ratchet anchor)
    # Live order tracking (empty for sim-only trades)
    live_order_ids: list = field(default_factory=list)


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
    contract_sym: str   = ""   # front-month streamer symbol (e.g. /MESU26:XCME) for live orders
    key_levels:   KeyLevels = field(default_factory=KeyLevels)
    sim_trades:       list = field(default_factory=list)  # list[SimTrade]
    re_entry_pending: bool = False   # True while waiting for the RE_ENTRY_DELAY cooldown
    # Cached render inputs — reused by lightweight pan/zoom redraws
    cached_sma50:        list = field(default_factory=list)
    cached_sma200:       list = field(default_factory=list)
    cached_signals:      list = field(default_factory=list)
    cached_rsi:          list = field(default_factory=list)
    cached_adx:          list = field(default_factory=list)
    cached_range_upper:  list = field(default_factory=list)
    cached_range_lower:  list = field(default_factory=list)


# Module-level symbol cache – persists across symbol switches within a session
_symbol_cache: dict[str, SymbolState] = {}

# ── Background stream / sim state (survives view navigation) ───────────────────
# Keyed by symbol — prevents duplicate streams when user navigates back
_active_stream_tasks: dict[str, "asyncio.Task"] = {}

# The active view registers its _update_ui function here so the stream loop
# always calls the *current* view's renderer, not a stale closure.
# Set to None when no view is mounted (UI updates suppressed; trade logic pauses).
_ui_refresh_hook: list[Optional[Callable]] = [None]

# Global sim on/off toggle — False by default so trades only open when explicitly enabled
_sim_enabled: bool = False

# Global live trading toggle — False by default; when True real orders are placed
_live_enabled:  bool      = False
_live_account:  list[str] = [""]   # cached account number, fetched on first live enable


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
                sl_stage=d.get("sl_stage", 0), peak_price=d.get("peak_price"),
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
                "sl_stage": t.sl_stage, "peak_price": t.peak_price,
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


def _compute_rsi(candles: list, period: int = RSI_PERIOD) -> list:
    """
    Wilder's smoothed RSI.  Returns list[Optional[float]] of the same length as candles.
    Values are None until the first full period + 1 candles are available.
    """
    closes = [c.close for c in candles]
    n = len(closes)
    result: list = [None] * n
    if n < period + 1:
        return result

    # Seed with simple average of first period
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - 100.0 / (1.0 + rs)

    # Wilder's smoothing for the rest
    for i in range(period + 1, n):
        diff     = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(diff, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-diff, 0.0)) / period
        if avg_loss == 0:
            result[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i] = 100.0 - 100.0 / (1.0 + rs)

    return result


def _check_rsi_divergence(sig: "Signal", candles: list, rsi: list) -> bool:
    """
    Returns True when RSI divergence confirms the signal direction.

    Bullish divergence: price made a lower-low at the grab candle, but RSI made a
                        higher-low → buyers are exhausted on the sell side.
    Bearish divergence: price made a higher-high at the grab candle, but RSI made a
                        lower-high → buyers are running out of steam.

    We scan back SWING_WINDOW candles to find at least one prior bar where
    price was in the opposite extreme compared to the grab candle.
    """
    ci = sig.candle_index
    if ci >= len(candles) or ci >= len(rsi):
        return False
    rsi_grab = rsi[ci]
    if rsi_grab is None:
        return False

    lookback_start = max(0, ci - SWING_WINDOW)

    if sig.direction == "BULL":
        grab_low = candles[ci].low
        for ri in range(ci - 2, lookback_start - 1, -1):
            if rsi[ri] is None:
                continue
            # Price lower-low AND RSI higher-low
            if grab_low < candles[ri].low and rsi_grab > rsi[ri]:
                return True
    else:  # BEAR
        grab_high = candles[ci].high
        for ri in range(ci - 2, lookback_start - 1, -1):
            if rsi[ri] is None:
                continue
            # Price higher-high AND RSI lower-high
            if grab_high > candles[ri].high and rsi_grab < rsi[ri]:
                return True

    return False


def _check_pro_trend(sig: "Signal", candles: list, sma200: list) -> bool:
    """
    Returns True when the signal direction is aligned with the SMA200 macro trend.

    BULL signals: close > SMA200  (price above the 200 MA → uptrend)
    BEAR signals: close < SMA200  (price below the 200 MA → downtrend)
    """
    ci = sig.candle_index
    if ci >= len(candles) or ci >= len(sma200) or sma200[ci] is None:
        return False
    close = candles[ci].close
    return (close > sma200[ci]) if sig.direction == "BULL" else (close < sma200[ci])


def _compute_adx(candles: list, period: int = ADX_PERIOD) -> list:
    """
    Wilder-smoothed ADX(14).

    ADX < ADX_THRESHOLD → ranging market (mean-reversion favoured).
    ADX ≥ ADX_THRESHOLD → trending market (momentum favoured).
    Returns list[Optional[float]] aligned to the candle list.
    """
    n = len(candles)
    result: list = [None] * n
    if n < period * 2:
        return result

    # True Range and raw Directional Movement per candle
    tr_vals, pdm_vals, mdm_vals = [], [], []
    for i in range(1, n):
        h, l   = candles[i].high, candles[i].low
        h_prev = candles[i - 1].high
        l_prev = candles[i - 1].low
        c_prev = candles[i - 1].close
        tr       = max(h - l, abs(h - c_prev), abs(l - c_prev))
        up_move  = h - h_prev
        dn_move  = l_prev - l
        pdm = up_move if (up_move > dn_move and up_move > 0) else 0.0
        mdm = dn_move if (dn_move > up_move and dn_move > 0) else 0.0
        tr_vals.append(tr)
        pdm_vals.append(pdm)
        mdm_vals.append(mdm)

    m = len(tr_vals)   # = n - 1
    if m < period:
        return result

    def _dx(apdm_v: float, amdm_v: float, atr_v: float) -> float:
        if atr_v == 0:
            return 0.0
        pdi = apdm_v / atr_v * 100
        mdi = amdm_v / atr_v * 100
        return abs(pdi - mdi) / (pdi + mdi) * 100 if (pdi + mdi) > 0 else 0.0

    # Wilder seed — first period values correspond to candle[period]
    atr  = sum(tr_vals[:period])
    apdm = sum(pdm_vals[:period])
    amdm = sum(mdm_vals[:period])
    dx_vals = [_dx(apdm, amdm, atr)]   # dx_vals[k] → candle[period + k]

    for k in range(period, m):
        atr  = atr  - atr  / period + tr_vals[k]
        apdm = apdm - apdm / period + pdm_vals[k]
        amdm = amdm - amdm / period + mdm_vals[k]
        dx_vals.append(_dx(apdm, amdm, atr))

    if len(dx_vals) < period:
        return result

    # Smooth DX into ADX
    adx_val = sum(dx_vals[:period]) / period
    ci = period + period - 1          # first valid ADX index in candles
    if ci < n:
        result[ci] = adx_val
    for j in range(period, len(dx_vals)):
        adx_val = (adx_val * (period - 1) + dx_vals[j]) / period
        ci = period + j
        if ci < n:
            result[ci] = adx_val

    return result


def _compute_range_bands(candles: list, period: int = RANGE_PERIOD) -> tuple:
    """
    Rolling N-period high (resistance) and low (support) curves.

    Returns (upper, lower) — both list[Optional[float]] aligned to candles.
    Useful for identifying the current trading range for mean-reversion entries.
    """
    n     = len(candles)
    upper = [None] * n
    lower = [None] * n
    for i in range(period - 1, n):
        window  = candles[i - period + 1 : i + 1]
        upper[i] = max(c.high for c in window)
        lower[i] = min(c.low  for c in window)
    return upper, lower


def _check_range_rotation(
    sig: "Signal",
    candles: list,
    adx: list,
    range_upper: list,
    range_lower: list,
) -> bool:
    """
    Returns True when all three conditions hold:
      1. ADX < ADX_THRESHOLD  — market is ranging, not trending.
      2. The grabbed price level is within RANGE_PROX of the relevant range boundary.
         BULL grab: level near the rolling low (support).
         BEAR grab: level near the rolling high (resistance).
      3. Range width is non-zero (instrument is actually moving).

    Together this confirms a mean-reversion setup at a range boundary, as described
    in the Range Rotation concept — sell near the top, buy near the bottom.
    """
    ci = sig.candle_index
    if ci >= len(candles) or ci >= len(adx):
        return False
    adx_val = adx[ci]
    if adx_val is None or adx_val >= ADX_THRESHOLD:
        return False   # trending — range rotation does not apply

    if ci >= len(range_upper) or ci >= len(range_lower):
        return False
    upper = range_upper[ci]
    lower = range_lower[ci]
    if upper is None or lower is None:
        return False
    rng = upper - lower
    if rng <= 0:
        return False

    proximity = RANGE_PROX * rng
    if sig.direction == "BULL":
        return sig.level <= lower + proximity   # grabbed level near support
    else:
        return sig.level >= upper - proximity   # grabbed level near resistance


def _compute_heavy(candles: list, key_levels: "KeyLevels") -> tuple:
    """SMA + RSI + ADX + range bands + signal detection — safe to call from a thread executor."""
    sma50                = _compute_sma(candles, 50)
    sma200               = _compute_sma(candles, 200)
    rsi                  = _compute_rsi(candles)
    adx                  = _compute_adx(candles)
    range_upper, range_lower = _compute_range_bands(candles)
    kl_sigs = detect_key_level_signals(candles, key_levels)
    kl_idxs = {s.candle_index for s in kl_sigs}
    sw_sigs = [s for s in detect_signals(candles) if s.candle_index not in kl_idxs]
    all_sigs = kl_sigs + sw_sigs
    for sig in all_sigs:
        sig.divergence = _check_rsi_divergence(sig, candles, rsi)
        sig.pro_trend  = _check_pro_trend(sig, candles, sma200)
        sig.in_range   = _check_range_rotation(sig, candles, adx, range_upper, range_lower)
    return sma50, sma200, rsi, adx, range_upper, range_lower, all_sigs


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


# ── Full-buffer signal detection (for back-test) ───────────────────────────────
def detect_all_signals(candles: list) -> list:
    """
    Slide the liquidity-grab detector across the ENTIRE candle buffer.

    Unlike detect_signals(), which only scans the last SIGNAL_LOOKBACK candles,
    this version scans every candle from index SWING_LOOKBACK*2 onward so that
    back-test results cover the full history window.  No look-ahead: each candle
    only sees swings that existed before it.
    """
    if len(candles) < SWING_LOOKBACK * 2 + 2:
        return []

    completed = candles[:-1]
    n         = len(completed)
    sh        = _swing_highs(completed, SWING_LOOKBACK)
    sl_pts    = _swing_lows(completed,  SWING_LOOKBACK)
    signals: list = []
    seen: set     = set()

    for ci in range(SWING_LOOKBACK * 2, n):
        if ci in seen:
            continue
        c = completed[ci]

        valid_sh = [(i, p) for i, p in sh    if i < ci and i > ci - SWING_WINDOW]
        valid_sl = [(i, p) for i, p in sl_pts if i < ci and i > ci - SWING_WINDOW]

        for _, level in valid_sh:
            wick_above = c.high - level
            reversal   = level  - c.close
            if wick_above > 0 and c.close < level and reversal >= wick_above * 0.30:
                signals.append(Signal(candle_index=ci, direction="BEAR", level=level))
                seen.add(ci)
                break
        if ci in seen:
            continue
        for _, level in valid_sl:
            wick_below = level   - c.low
            reversal   = c.close - level
            if wick_below > 0 and c.close > level and reversal >= wick_below * 0.30:
                signals.append(Signal(candle_index=ci, direction="BULL", level=level))
                seen.add(ci)
                break

    return signals


# ── Strategy KPI computation ────────────────────────────────────────────────────
def compute_kpis(trades: list) -> dict:
    """
    Compute all strategy performance KPIs from a list of SimTrade objects.
    Safe to call with an empty list — all metrics return sensible defaults.
    """
    import statistics as _stat

    closed = [t for t in trades if t.status in ("WIN", "LOSS")]
    wins   = [t for t in closed if t.status == "WIN"]
    losses = [t for t in closed if t.status == "LOSS"]
    opens  = [t for t in trades if t.status == "OPEN"]

    n_closed = len(closed)
    n_wins   = len(wins)
    n_losses = len(losses)

    win_rate     = (n_wins / n_closed * 100) if n_closed else 0.0
    gross_wins   = sum(t.pnl for t in wins)
    gross_losses = abs(sum(t.pnl for t in losses))
    total_pnl    = sum(t.pnl for t in closed)
    avg_pnl      = total_pnl / n_closed if n_closed else 0.0
    avg_win      = gross_wins / n_wins   if n_wins   else 0.0
    avg_loss_raw = sum(t.pnl for t in losses) / n_losses if n_losses else 0.0  # negative
    wl_ratio     = avg_win / abs(avg_loss_raw) if avg_loss_raw != 0 else float("inf")
    profit_factor= gross_wins / gross_losses   if gross_losses > 0 else float("inf")
    expectancy   = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss_raw)
    max_win      = max((t.pnl for t in wins),   default=0.0)
    max_loss     = min((t.pnl for t in losses), default=0.0)

    # Equity curve (chronological)
    sorted_closed = sorted(closed, key=lambda t: t.closed_at or 0)
    equity: list[float] = []
    running = 0.0
    for t in sorted_closed:
        running += t.pnl
        equity.append(running)

    # Max drawdown
    max_dd = 0.0
    peak_eq = float("-inf")
    for v in equity:
        if v > peak_eq:
            peak_eq = v
        dd = peak_eq - v
        if dd > max_dd:
            max_dd = dd

    # Sharpe (simplified — mean / std of per-trade P&L, no risk-free rate)
    if n_closed > 1:
        pnls   = [t.pnl for t in closed]
        mean   = sum(pnls) / n_closed
        std    = _stat.stdev(pnls)
        sharpe = mean / std if std > 0 else 0.0
    else:
        sharpe = 0.0

    recovery = total_pnl / max_dd if max_dd > 0 else float("inf")

    # Ratchet stop exit breakdown
    tp_closed    = sum(1 for t in closed
                       if abs(t.pnl - round(t.risk * RR_RATIO, 4)) < 0.001)
    trail_closed = sum(1 for t in closed
                       if t.sl_stage == 2
                       and abs(t.pnl - round(t.risk * RR_RATIO, 4)) >= 0.001)
    be_closed    = sum(1 for t in closed if t.sl_stage >= 1 and abs(t.pnl) < 0.001)
    orig_sl      = sum(1 for t in closed if t.sl_stage == 0 and t.status == "LOSS")
    protected    = sum(1 for t in closed
                       if t.sl_stage >= 1
                       and abs(t.pnl - round(t.risk * RR_RATIO, 4)) >= 0.001)

    # Source breakdown
    from collections import defaultdict as _dd
    src_stats: dict = _dd(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
    for t in closed:
        src = t.source.replace("+RE", "").strip()
        if t.status == "WIN":
            src_stats[src]["wins"] += 1
        else:
            src_stats[src]["losses"] += 1
        src_stats[src]["pnl"] += t.pnl

    return {
        "total": len(trades), "closed": n_closed,
        "wins": n_wins, "losses": n_losses, "opens": len(opens),
        "win_rate": win_rate, "profit_factor": profit_factor,
        "expectancy": expectancy, "total_pnl": total_pnl, "avg_pnl": avg_pnl,
        "avg_win": avg_win, "avg_loss": avg_loss_raw,
        "wl_ratio": wl_ratio, "max_win": max_win, "max_loss": max_loss,
        "max_drawdown": max_dd, "sharpe": sharpe, "recovery": recovery,
        "equity_curve": equity,
        "tp_closed": tp_closed, "trail_closed": trail_closed,
        "be_closed": be_closed, "orig_sl": orig_sl, "protected": protected,
        "source_stats": dict(src_stats),
    }


# ── Back-test trade simulator ──────────────────────────────────────────────────
def simulate_trades(
    signals: list,
    candles: list,
    trend_filter_on: bool = True,
    range_filter_on: bool = True,
) -> list:
    """
    Pure back-test engine.  Replays signals over candles using the same rules as
    live trading (divergence gate, pro-trend filter, range filter, ratcheting stop)
    but with no side effects — returns a new list of SimTrade objects.
    """
    import uuid as _uuid

    sim: list = []

    for sig in sorted(signals, key=lambda s: s.candle_index):
        if not sig.divergence:
            continue
        if trend_filter_on and not sig.pro_trend:
            continue
        if range_filter_on and not sig.in_range:
            continue
        if sig.candle_index >= len(candles):
            continue

        open_trades = [t for t in sim if t.status == "OPEN"]
        if len(open_trades) >= MAX_OPEN_TRADES:
            existing = open_trades[0]
            if existing.direction == sig.direction:
                continue
            # Opposing signal — close at market, no re-entry in back-test
            mc = candles[sig.candle_index]
            existing.pnl    = round(mc.close - existing.entry, 4) if existing.direction == "BULL" \
                              else round(existing.entry - mc.close, 4)
            existing.status    = "WIN" if existing.pnl >= 0 else "LOSS"
            existing.closed_at = mc.timestamp
            continue

        c = candles[sig.candle_index]
        if any(abs(t.opened_at - c.timestamp) < 1.0 for t in sim):
            continue

        entry = c.close
        if sig.direction == "BULL":
            sl   = round(c.low  - SL_BUFFER, 4)
            risk = round(entry - sl, 4)
            tp   = round(entry + risk * RR_RATIO, 4)
        else:
            sl   = round(c.high + SL_BUFFER, 4)
            risk = round(sl - entry, 4)
            tp   = round(entry - risk * RR_RATIO, 4)
        if risk <= 0:
            continue

        sim.append(SimTrade(
            id=_uuid.uuid4().hex, symbol="BT",
            direction=sig.direction, source=sig.source,
            entry=entry, sl=sl, tp=tp, risk=risk,
            opened_at=c.timestamp, opened_idx=sig.candle_index,
        ))

    # Resolve all trades with ratcheting stop (O(n) per trade via running peak)
    ts_to_idx = {c.timestamp: i for i, c in enumerate(candles)}
    for trade in sim:
        if trade.status != "OPEN":
            continue
        open_pos = ts_to_idx.get(trade.opened_at)
        if open_pos is None:
            continue

        tp_dist = abs(trade.tp - trade.entry)
        if tp_dist == 0:
            continue

        peak = candles[open_pos].high if trade.direction == "BULL" \
               else candles[open_pos].low

        for c in candles[open_pos + 1:]:
            # Update running peak
            if trade.direction == "BULL":
                if c.high > peak:
                    peak = c.high
                progress = peak - trade.entry
                if progress >= TRAIL_TRIGGER * tp_dist:
                    trail_sl = round(peak - trade.risk * TRAIL_OFFSET, 4)
                    new_sl   = max(trade.sl, trail_sl)
                    if new_sl > trade.sl:
                        trade.sl = new_sl; trade.sl_stage = 2; trade.peak_price = peak
                elif progress >= BE_TRIGGER * tp_dist and trade.sl_stage < 1:
                    trade.sl = trade.entry; trade.sl_stage = 1; trade.peak_price = peak
                if c.low <= trade.sl:
                    trade.status    = "WIN" if trade.sl > trade.entry else "LOSS"
                    trade.pnl       = round(trade.sl - trade.entry, 4)
                    trade.closed_at = c.timestamp
                    break
                if c.high >= trade.tp:
                    trade.status    = "WIN"
                    trade.pnl       = round(trade.risk * RR_RATIO, 4)
                    trade.closed_at = c.timestamp
                    break
            else:
                if c.low < peak:
                    peak = c.low
                progress = trade.entry - peak
                if progress >= TRAIL_TRIGGER * tp_dist:
                    trail_sl = round(peak + trade.risk * TRAIL_OFFSET, 4)
                    new_sl   = min(trade.sl, trail_sl)
                    if new_sl < trade.sl:
                        trade.sl = new_sl; trade.sl_stage = 2; trade.peak_price = peak
                elif progress >= BE_TRIGGER * tp_dist and trade.sl_stage < 1:
                    trade.sl = trade.entry; trade.sl_stage = 1; trade.peak_price = peak
                if c.high >= trade.sl:
                    trade.status    = "WIN" if trade.sl < trade.entry else "LOSS"
                    trade.pnl       = round(trade.entry - trade.sl, 4)
                    trade.closed_at = c.timestamp
                    break
                if c.low <= trade.tp:
                    trade.status    = "WIN"
                    trade.pnl       = round(trade.risk * RR_RATIO, 4)
                    trade.closed_at = c.timestamp
                    break

    return sim


def prepare_backtest(sym: str) -> tuple:
    """
    Load the full candle cache for *sym*, run full-buffer signal detection with
    divergence and pro-trend flags set, and return all inputs needed by simulate_trades.

    Returns (candles, signals) — both are new lists, no shared state.
    """
    candles = _load_cache_full(sym)
    if not candles:
        return [], []

    kl = _compute_key_levels(candles)
    sma50, sma200, rsi, adx, range_upper, range_lower, kl_sigs = _compute_heavy(candles, kl)

    # Full-buffer swing signals (not limited to SIGNAL_LOOKBACK)
    sw_all   = detect_all_signals(candles)
    kl_idxs  = {s.candle_index for s in kl_sigs}
    sw_extra = [s for s in sw_all if s.candle_index not in kl_idxs]
    for s in sw_extra:
        s.divergence = _check_rsi_divergence(s, candles, rsi)
        s.pro_trend  = _check_pro_trend(s, candles, sma200)
        s.in_range   = _check_range_rotation(s, candles, adx, range_upper, range_lower)

    all_sigs = sorted(kl_sigs + sw_extra, key=lambda s: s.candle_index)
    return candles, all_sigs


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
    rsi:         Optional[list]       = None,
    adx:         Optional[list]       = None,
    range_upper: Optional[list]       = None,
    range_lower: Optional[list]       = None,
) -> ft.Control:
    """Render the dark candlestick canvas with SMA lines, signal arrows, RSI and ADX panels."""

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
    vis_sma50       = (sma50       or [])[start:end]
    vis_sma200      = (sma200      or [])[start:end]
    vis_rsi         = (rsi         or [])[start:end]
    vis_adx         = (adx         or [])[start:end]
    vis_range_upper = (range_upper or [])[start:end]
    vis_range_lower = (range_lower or [])[start:end]
    buf_offset      = start

    all_prices: list[float] = []
    for c in visible:
        all_prices += [c.high, c.low]
    for v in vis_sma50:
        if v is not None:
            all_prices.append(v)
    for v in vis_sma200:
        if v is not None:
            all_prices.append(v)

    mn, mx      = min(all_prices), max(all_prices)
    pad         = (mx - mn) * 0.08 / price_scale or 2.0
    mn -= pad;  mx += pad
    price_range = mx - mn
    # Reserve space at the bottom for the RSI/ADX sub-panel when data is present
    rsi_visible = rsi is not None
    adx_visible = adx is not None and any(v is not None for v in (adx or []))
    rsi_reserve = (RSI_PANEL_H + RSI_GAP) if (rsi_visible or adx_visible) else 0
    plot_h      = chart_h - PAD_TOP - PAD_BOTTOM - rsi_reserve

    # RSI panel y-coordinates (valid only when rsi_visible)
    rsi_top = float(chart_h - PAD_BOTTOM - RSI_PANEL_H)
    rsi_bot = float(chart_h - PAD_BOTTOM)

    def ry(v: float) -> float:
        """Map RSI value 0–100 → pixel y inside the RSI sub-panel."""
        return rsi_top + (100.0 - v) / 100.0 * RSI_PANEL_H

    def py(price: float) -> float:
        return PAD_TOP + plot_h * (mx - price) / price_range

    def cx(i: int) -> float:
        return PAD_LEFT + i * candle_step + candle_step / 2

    # Determine macro trend from last visible close vs last visible SMA200
    _last_sma200 = next((v for v in reversed(vis_sma200) if v is not None), None)
    _last_close  = visible[-1].close if visible else None
    if _last_close is not None and _last_sma200 is not None:
        _trend_bull = _last_close > _last_sma200
        bg_col      = "#0D1510" if _trend_bull else "#150D0D"
        sma200_col  = "#44DD88" if _trend_bull else "#FF5555"
    else:
        _trend_bull = None
        bg_col      = COL_BG
        sma200_col  = COL_SMA200

    shapes: list[cv.Shape] = []

    # Background (tinted green/red based on SMA200 trend)
    shapes.append(cv.Rect(
        x=0, y=0, width=chart_w, height=chart_h,
        paint=ft.Paint(color=bg_col, style=ft.PaintingStyle.FILL),
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

    # SMA 200 (green when above, red when below — reflects macro trend)
    prev200: Optional[tuple[float, float]] = None
    for i, v in enumerate(vis_sma200):
        if v is not None:
            pt = (cx(i), py(v))
            if prev200 is not None:
                shapes.append(cv.Line(
                    x1=prev200[0], y1=prev200[1], x2=pt[0], y2=pt[1],
                    paint=ft.Paint(color=sma200_col, stroke_width=1.5),
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

    # Range bands — rolling 20-period high/low (semi-transparent teal curves)
    prev_ru: Optional[tuple[float, float]] = None
    prev_rl: Optional[tuple[float, float]] = None
    for i in range(len(vis_range_upper)):
        ru = vis_range_upper[i] if i < len(vis_range_upper) else None
        rl = vis_range_lower[i] if i < len(vis_range_lower) else None
        if ru is not None and mn <= ru <= mx:
            pt = (cx(i), py(ru))
            if prev_ru is not None:
                shapes.append(cv.Line(
                    x1=prev_ru[0], y1=prev_ru[1], x2=pt[0], y2=pt[1],
                    paint=ft.Paint(color=COL_RANGE, stroke_width=1.0),
                ))
            prev_ru = pt
        else:
            prev_ru = None
        if rl is not None and mn <= rl <= mx:
            pt = (cx(i), py(rl))
            if prev_rl is not None:
                shapes.append(cv.Line(
                    x1=prev_rl[0], y1=prev_rl[1], x2=pt[0], y2=pt[1],
                    paint=ft.Paint(color=COL_RANGE, stroke_width=1.0),
                ))
            prev_rl = pt
        else:
            prev_rl = None

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

    # Signal arrows — 3-tier quality system:
    #   Tier 1 (Prime):    divergence=True  AND pro_trend=True  → bright, large, filled
    #   Tier 2 (Filtered): divergence=True  AND pro_trend=False → bright, medium, hollow (stroke)
    #   Tier 3 (Weak):     divergence=False                     → dim, small, filled
    for sig in signals:
        vi = sig.candle_index - buf_offset
        if not 0 <= vi < len(visible):
            continue
        c = visible[vi]
        x = cx(vi)

        if sig.divergence and sig.pro_trend:
            # Tier 1: prime signal — bright, large filled arrow
            hw, ht = 7, 11
            style  = ft.PaintingStyle.FILL
            col    = COL_SIG_BULL if sig.direction == "BULL" else COL_SIG_BEAR
        elif sig.divergence:
            # Tier 2: filtered (counter-trend) — bright but hollow, slightly smaller
            hw, ht = 6, 9
            style  = ft.PaintingStyle.STROKE
            col    = COL_SIG_BULL if sig.direction == "BULL" else COL_SIG_BEAR
        else:
            # Tier 3: weak (no divergence) — dim small filled arrow
            hw, ht = 5, 8
            style  = ft.PaintingStyle.FILL
            col    = "#007A3D" if sig.direction == "BULL" else "#882222"

        stroke_w = 1.5 if style == ft.PaintingStyle.STROKE else 1.0
        if sig.direction == "BULL":
            tip_y = py(c.low) + 14
            shapes.append(cv.Path(
                elements=[
                    cv.Path.MoveTo(x,      tip_y - ht),
                    cv.Path.LineTo(x - hw, tip_y),
                    cv.Path.LineTo(x + hw, tip_y),
                    cv.Path.Close(),
                ],
                paint=ft.Paint(color=col, style=style, stroke_width=stroke_w),
            ))
        else:
            tip_y = py(c.high) - 3
            shapes.append(cv.Path(
                elements=[
                    cv.Path.MoveTo(x,      tip_y + ht),
                    cv.Path.LineTo(x - hw, tip_y),
                    cv.Path.LineTo(x + hw, tip_y),
                    cv.Path.Close(),
                ],
                paint=ft.Paint(color=col, style=style, stroke_width=stroke_w),
            ))

    # ── Simulated trade levels: entry / SL / TP horizontal dashed lines ───────
    if sim_trades:
        # Timestamp → visible-index map; immune to deque index shifts
        ts_to_vi = {c.timestamp: i for i, c in enumerate(visible)}
        for trade in sim_trades:
            vi_open = ts_to_vi.get(trade.opened_at)
            if vi_open is None:
                continue  # open candle not in current viewport
            x_start = cx(vi_open)

            # x_end: stop at the close candle (by timestamp) if visible, else right edge
            vi_cl = ts_to_vi.get(trade.closed_at) if trade.closed_at is not None else None
            x_end = cx(vi_cl) if vi_cl is not None else float(chart_w - PAD_RIGHT)
            x_end = min(x_end, float(chart_w - PAD_RIGHT))

            sw = 1.5 if trade.status == "OPEN" else 0.8   # thinner once closed

            # SL line color and label depend on the ratchet stage
            sl_col = (COL_TRADE_SL_TRAIL if trade.sl_stage == 2
                      else COL_TRADE_SL_BE if trade.sl_stage == 1
                      else COL_TRADE_SL)
            sl_lbl = ("TR" if trade.sl_stage == 2
                      else "BE" if trade.sl_stage == 1
                      else "SL")

            for price_val, col, lbl in (
                (trade.entry, COL_TRADE_ENTRY, "E"),
                (trade.sl,    sl_col,          sl_lbl),
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
            if trade.status in ("WIN", "LOSS") and vi_cl is not None:
                badge_price = trade.tp  if trade.status == "WIN" else trade.sl
                badge_col   = (COL_TRADE_TP  if trade.status == "WIN"
                               else sl_col)  # use stage color for SL-hit badges
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

    # ── RSI / ADX sub-panel ───────────────────────────────────────────────────────
    if rsi_visible or adx_visible:
        # Panel background
        shapes.append(cv.Rect(
            x=PAD_LEFT, y=rsi_top, width=float(chart_w - PAD_LEFT - PAD_RIGHT), height=RSI_PANEL_H,
            paint=ft.Paint(color="#181818", style=ft.PaintingStyle.FILL),
        ))
        # OB / OS / mid reference lines
        for ref_val, ref_col, ref_lbl in [
            (70.0, COL_RSI_OB, "70"),
            (50.0, "#333333",  "50"),
            (30.0, COL_RSI_OS, "30"),
        ]:
            ry_ref = ry(ref_val)
            # dashed
            kx = float(PAD_LEFT)
            while kx < chart_w - PAD_RIGHT:
                kx2 = min(kx + 5.0, float(chart_w - PAD_RIGHT))
                shapes.append(cv.Line(
                    x1=kx, y1=ry_ref, x2=kx2, y2=ry_ref,
                    paint=ft.Paint(color=ref_col, stroke_width=0.6),
                ))
                kx += 9.0
            shapes.append(cv.Text(
                x=float(PAD_LEFT) - 20, y=ry_ref - 5,
                spans=[ft.TextSpan(ref_lbl, style=ft.TextStyle(size=8, color=ref_col))],
            ))

        if rsi_visible:
            # RSI line segments (colored by level)
            def _rsi_seg_color(v: float) -> str:
                if v >= 70:
                    return COL_RSI_OB
                if v <= 30:
                    return COL_RSI_OS
                return COL_RSI

            prev_rsi: Optional[tuple[float, float]] = None
            for vi, rsi_v in enumerate(vis_rsi):
                if rsi_v is None:
                    prev_rsi = None
                    continue
                pt = (cx(vi), ry(rsi_v))
                if prev_rsi is not None:
                    shapes.append(cv.Line(
                        x1=prev_rsi[0], y1=prev_rsi[1], x2=pt[0], y2=pt[1],
                        paint=ft.Paint(color=_rsi_seg_color(rsi_v), stroke_width=1.2),
                    ))
                prev_rsi = pt

            # Divergence dots — small filled squares at confirmed-signal candle RSI positions
            div_sig_vis_indices = {
                sig.candle_index - buf_offset
                for sig in signals
                if sig.divergence and 0 <= (sig.candle_index - buf_offset) < len(vis_rsi)
            }
            for vi in div_sig_vis_indices:
                if vi < len(vis_rsi) and vis_rsi[vi] is not None:
                    dot_x = cx(vi) - 3
                    dot_y = ry(vis_rsi[vi]) - 3
                    shapes.append(cv.Rect(
                        x=dot_x, y=dot_y, width=6, height=6,
                        paint=ft.Paint(color="#FFD700", style=ft.PaintingStyle.FILL),
                    ))

            # RSI label
            shapes.append(cv.Text(
                x=float(PAD_LEFT) + 4, y=rsi_top + 4,
                spans=[ft.TextSpan(f"RSI {RSI_PERIOD}", style=ft.TextStyle(size=8, color=COL_RSI))],
            ))

        # ADX line — overlaid in the same sub-panel (0-100 maps to same ry() scale)
        if vis_adx:
            # ADX threshold line at 25 (dashed, dark teal)
            adx_thresh_y = ry(ADX_THRESHOLD)
            kx = float(PAD_LEFT)
            while kx < chart_w - PAD_RIGHT:
                kx2 = min(kx + 4.0, float(chart_w - PAD_RIGHT))
                shapes.append(cv.Line(
                    x1=kx, y1=adx_thresh_y, x2=kx2, y2=adx_thresh_y,
                    paint=ft.Paint(color=COL_ADX_THRESH, stroke_width=0.8),
                ))
                kx += 7.0
            shapes.append(cv.Text(
                x=float(PAD_LEFT) - 20, y=adx_thresh_y - 5,
                spans=[ft.TextSpan(str(ADX_THRESHOLD), style=ft.TextStyle(size=8, color=COL_ADX_THRESH))],
            ))

            prev_adx: Optional[tuple[float, float]] = None
            for vi, adx_v in enumerate(vis_adx):
                if adx_v is None:
                    prev_adx = None
                    continue
                # Color: cyan when ranging (<threshold), yellow when trending (≥threshold)
                adx_col = "#FFD700" if adx_v >= ADX_THRESHOLD else COL_ADX
                pt = (cx(vi), ry(adx_v))
                if prev_adx is not None:
                    shapes.append(cv.Line(
                        x1=prev_adx[0], y1=prev_adx[1], x2=pt[0], y2=pt[1],
                        paint=ft.Paint(color=adx_col, stroke_width=1.0),
                    ))
                prev_adx = pt

            # ADX label (right side of the RSI label)
            lbl_offset = 56 if rsi_visible else 4
            shapes.append(cv.Text(
                x=float(PAD_LEFT) + lbl_offset, y=rsi_top + 4,
                spans=[ft.TextSpan(f"ADX {ADX_PERIOD}", style=ft.TextStyle(size=8, color=COL_ADX))],
            ))

    # In-chart legend (row 1: SMAs)
    lx, ly = PAD_LEFT + 4, PAD_TOP + 4
    shapes += [
        cv.Line(x1=lx,      y1=ly + 4, x2=lx + 12, y2=ly + 4,
                paint=ft.Paint(color=COL_SMA50,  stroke_width=2)),
        cv.Text(x=lx + 14,  y=ly - 2,
                spans=[ft.TextSpan("SMA 50",  style=ft.TextStyle(size=9, color=COL_SMA50))]),
        cv.Line(x1=lx + 62, y1=ly + 4, x2=lx + 74, y2=ly + 4,
                paint=ft.Paint(color=sma200_col, stroke_width=2)),
        cv.Text(x=lx + 76,  y=ly - 2,
                spans=[ft.TextSpan("SMA 200", style=ft.TextStyle(size=9, color=sma200_col))]),
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
    # In-chart legend (row 3: RSI + ADX)
    if rsi_visible or adx_visible:
        ly3 = ly + 28
        if rsi_visible:
            shapes += [
                cv.Line(x1=lx,      y1=ly3 + 4, x2=lx + 12, y2=ly3 + 4,
                        paint=ft.Paint(color=COL_RSI, stroke_width=1.5)),
                cv.Text(x=lx + 14,  y=ly3 - 2,
                        spans=[ft.TextSpan(f"RSI {RSI_PERIOD}", style=ft.TextStyle(size=9, color=COL_RSI))]),
                cv.Rect(x=lx + 62, y=ly3, width=8, height=8,
                        paint=ft.Paint(color="#FFD700", style=ft.PaintingStyle.FILL)),
                cv.Text(x=lx + 74,  y=ly3 - 2,
                        spans=[ft.TextSpan("Divergence", style=ft.TextStyle(size=9, color="#FFD700"))]),
            ]
        if adx_visible:
            adx_lx = lx + 140 if rsi_visible else lx
            shapes += [
                cv.Line(x1=adx_lx,      y1=ly3 + 4, x2=adx_lx + 12, y2=ly3 + 4,
                        paint=ft.Paint(color=COL_ADX, stroke_width=1.5)),
                cv.Text(x=adx_lx + 14,  y=ly3 - 2,
                        spans=[ft.TextSpan(f"ADX {ADX_PERIOD} (<{ADX_THRESHOLD}=Range)",
                                           style=ft.TextStyle(size=9, color=COL_ADX))]),
            ]
    # In-chart legend (row 4: macro trend from SMA200)
    if _trend_bull is not None:
        ly4 = ly + 42
        trend_lbl = "↑ Uptrend" if _trend_bull else "↓ Downtrend"
        shapes += [
            cv.Line(x1=lx,     y1=ly4 + 4, x2=lx + 12, y2=ly4 + 4,
                    paint=ft.Paint(color=sma200_col, stroke_width=1.5)),
            cv.Text(x=lx + 14, y=ly4 - 2,
                    spans=[ft.TextSpan(f"Trend: {trend_lbl}",
                                       style=ft.TextStyle(size=9, color=sma200_col))]),
        ]

    # In-chart legend (row 5: range bands)
    if vis_range_upper or vis_range_lower:
        ly5 = ly + 56
        shapes += [
            cv.Line(x1=lx,     y1=ly5 + 4, x2=lx + 12, y2=ly5 + 4,
                    paint=ft.Paint(color=COL_RANGE, stroke_width=1.5)),
            cv.Text(x=lx + 14, y=ly5 - 2,
                    spans=[ft.TextSpan(f"Range {RANGE_PERIOD} H/L",
                                       style=ft.TextStyle(size=9, color=COL_RANGE))]),
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

    # ── Alert / sound / filter state ──────────────────────────────────────────
    alert_enabled:        list[bool] = [True]
    sound_enabled:        list[bool] = [True]
    trend_filter_enabled: list[bool] = [True]   # False → allow counter-trend trades
    range_filter_enabled: list[bool] = [True]   # False → allow non-ranging entries
    alert_task:           list        = [None]  # list[asyncio.Task | None]

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
    sim_btn_ref         = ft.Ref[ft.IconButton]()
    sim_lbl_ref         = ft.Ref[ft.Text]()
    trade_live_btn_ref  = ft.Ref[ft.IconButton]()
    trade_live_lbl_ref  = ft.Ref[ft.Text]()
    badge_ref           = ft.Ref[ft.Container]()
    badge_text_ref      = ft.Ref[ft.Text]()
    throb_task: list[Optional[asyncio.Task]] = [None]

    # ── Dynamic chart dimensions ───────────────────────────────────────────────
    # Overhead: AppBar 56px + padding 32px + header 70px + picker 40px +
    #           toolbar 28px + 7×spacing 70px + legend 35px + stats row 40px + status 18px ≈ 389px
    _OVERHEAD = 430

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
    async def _rebuild_chart_dims() -> None:
        """Recompute chart dimensions and redraw canvas.  Safe to call with no data."""
        chart_w, chart_h, n_visible = _get_chart_dims()

        if chart_area_ref.current:
            chart_area_ref.current.height = chart_h + 4
            chart_area_ref.current.update()

        state   = _state()
        candles = list(state.buffer)
        if chart_container_ref.current and candles:
            loop = asyncio.get_running_loop()
            buf_start   = _compute_buf_start(len(candles), n_visible)
            chart_canvas = await loop.run_in_executor(
                None, _build_chart,
                candles,
                state.cached_sma50, state.cached_sma200, state.cached_signals,
                chart_w, chart_h, n_visible,
                state.key_levels, candle_w[0], price_scale[0], buf_start, state.sim_trades,
                state.cached_rsi, state.cached_adx,
                state.cached_range_upper, state.cached_range_lower,
            )
            chart_container_ref.current.content = chart_canvas
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
    def _execute_trade(sig: Signal, candles: list) -> None:
        """
        Mechanical trade creation — no guard logic.
        Computes entry/SL/TP from the signal candle, appends the SimTrade,
        and persists it.  Called by both the normal flow and the re-entry path.
        """
        if sig.candle_index >= len(candles):
            return
        c     = candles[sig.candle_index]
        entry = c.close
        if sig.direction == "BULL":
            sl   = round(c.low  - SL_BUFFER, 4)
            risk = round(entry - sl, 4)
            tp   = round(entry + risk * RR_RATIO, 4)
        else:
            sl   = round(c.high + SL_BUFFER, 4)
            risk = round(sl - entry, 4)
            tp   = round(entry - risk * RR_RATIO, 4)
        if risk <= 0:
            return  # degenerate (flat) candle — skip
        sym   = active_symbol[0]
        state = _state()
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
        # If live trading is active, also submit real orders
        if _live_enabled and state.contract_sym:
            asyncio.create_task(_place_live_orders(trade, state.contract_sym))

    def _close_at_market(trade: SimTrade, candles: list) -> None:
        """
        Close a specific OPEN trade at the current market price (last candle close).
        Updates P&L, persists, and refreshes the stats row.
        """
        if not candles:
            return
        current = candles[-1]
        if trade.direction == "BULL":
            trade.pnl = round(current.close - trade.entry, 4)
        else:
            trade.pnl = round(trade.entry - current.close, 4)
        trade.status    = "WIN" if trade.pnl >= 0 else "LOSS"
        trade.closed_at = current.timestamp
        sym = active_symbol[0]
        threading.Thread(
            target=_save_sim_trades, args=(sym, _state().sim_trades), daemon=True,
        ).start()
        _update_stats()

    async def _re_entry_check(sig: Signal, sym: str) -> None:
        """
        Wait RE_ENTRY_DELAY seconds after an opposing-signal flip, then check
        whether the original signal direction is still viable and, if so, open
        a re-entry trade at the current candle.
        """
        await asyncio.sleep(RE_ENTRY_DELAY)

        state = _state()
        state.re_entry_pending = False   # always clear the flag on exit

        # Abort if the user has switched to a different symbol
        if active_symbol[0] != sym:
            return

        # Abort if a trade was already opened in the interim (e.g. by SL → new signal)
        if any(t.status == "OPEN" for t in state.sim_trades):
            return

        candles = list(state.buffer)
        if not candles:
            return

        last = candles[-1]
        # Viability: price must still be on the correct side of the grabbed level
        if sig.direction == "BULL" and last.close <= sig.level:
            return   # price fell back below the swept low — setup invalidated
        if sig.direction == "BEAR" and last.close >= sig.level:
            return   # price climbed back above the swept high — setup invalidated

        # Build a synthetic signal pointing at the current candle
        re_sig = Signal(
            candle_index=len(candles) - 1,
            direction=sig.direction,
            level=sig.level,
            source=sig.source + "+RE",   # label distinguishes re-entries in chart/stats
            divergence=True,             # inherited from the original RSI-confirmed signal
        )

        # Re-check trend alignment at re-entry time when the filter is active
        if trend_filter_enabled[0]:
            sma200_re = _compute_sma(candles, 200)
            if not _check_pro_trend(re_sig, candles, sma200_re):
                return   # trend flipped during the cooldown window — skip

        _execute_trade(re_sig, candles)
        _update_stats()
        asyncio.create_task(_update_ui())

    def _open_sim_trade(sig: Signal, candles: list) -> None:
        """
        Gate-keeper for new sim trades.

        Rules (in order):
          1. RSI divergence must be confirmed.
          2. Re-entry cooldown blocks any open during the delay window.
          3. Hard cap: at most MAX_OPEN_TRADES open at once.
             - Same direction as existing open → already in trade, skip.
             - Opposing direction → close existing at market, start re-entry timer.
          4. Timestamp duplicate guard (immune to deque index shifts).
          5. Execute the trade.
        """
        # 0. Global sim toggle — no trades when sim is paused
        if not _sim_enabled:
            return

        # 1. Divergence gate
        if not sig.divergence:
            return

        state = _state()

        # 2. Re-entry cooldown gate
        if state.re_entry_pending:
            return

        # 2.5. Pro-trend filter — skip counter-trend signals when filter is on
        if trend_filter_enabled[0] and not sig.pro_trend:
            return

        # 2.75. Range filter — skip non-ranging signals when filter is on
        if range_filter_enabled[0] and not sig.in_range:
            return

        if sig.candle_index >= len(candles):
            return

        # 3. Open-trade cap
        open_trades = [t for t in state.sim_trades if t.status == "OPEN"]
        if len(open_trades) >= MAX_OPEN_TRADES:
            existing = open_trades[0]
            if existing.direction == sig.direction:
                return  # already positioned in this direction — hold
            # Opposing signal: flip the position
            _close_at_market(existing, candles)
            state.re_entry_pending = True
            asyncio.create_task(_re_entry_check(sig, active_symbol[0]))
            return  # do NOT open immediately — wait for re-entry check

        c = candles[sig.candle_index]

        # 4. Timestamp duplicate guard
        if any(abs(t.opened_at - c.timestamp) < 1.0 for t in state.sim_trades):
            return

        # 5. Execute
        _execute_trade(sig, candles)

    def _update_trailing_stops(candles: list) -> None:
        """
        Ratchet stop losses for all OPEN trades based on price progress toward TP.

        Stage 0 → 1 (Breakeven):  price reaches BE_TRIGGER    fraction of TP dist → SL = entry
        Stage 1 → 2 (Trailing):   price reaches TRAIL_TRIGGER fraction of TP dist → trail SL
          Trailing SL = peak ∓ risk × TRAIL_OFFSET, ratcheted so it only moves favorably.
        """
        state     = _state()
        ts_to_idx = {c.timestamp: i for i, c in enumerate(candles)}
        changed   = False

        for trade in state.sim_trades:
            if trade.status != "OPEN":
                continue
            open_pos = ts_to_idx.get(trade.opened_at)
            if open_pos is None:
                continue

            tp_dist         = abs(trade.tp - trade.entry)
            if tp_dist == 0:
                continue
            be_threshold    = BE_TRIGGER    * tp_dist
            trail_threshold = TRAIL_TRIGGER * tp_dist

            post_open = candles[open_pos:]   # includes the open candle itself

            if trade.direction == "BULL":
                peak     = max(c.high for c in post_open)
                progress = peak - trade.entry

                if progress >= trail_threshold:
                    trail_sl = round(peak - trade.risk * TRAIL_OFFSET, 4)
                    new_sl   = max(trade.sl, trail_sl)   # ratchet: only move up
                    if trade.sl_stage < 2 or new_sl > trade.sl:
                        trade.sl        = new_sl
                        trade.sl_stage  = 2
                        trade.peak_price = peak
                        changed = True

                elif progress >= be_threshold and trade.sl_stage < 1:
                    trade.sl        = trade.entry
                    trade.sl_stage  = 1
                    trade.peak_price = peak
                    changed = True

            else:  # BEAR
                peak     = min(c.low for c in post_open)
                progress = trade.entry - peak

                if progress >= trail_threshold:
                    trail_sl = round(peak + trade.risk * TRAIL_OFFSET, 4)
                    new_sl   = min(trade.sl, trail_sl)   # ratchet: only move down
                    if trade.sl_stage < 2 or new_sl < trade.sl:
                        trade.sl        = new_sl
                        trade.sl_stage  = 2
                        trade.peak_price = peak
                        changed = True

                elif progress >= be_threshold and trade.sl_stage < 1:
                    trade.sl        = trade.entry
                    trade.sl_stage  = 1
                    trade.peak_price = peak
                    changed = True

        if changed:
            sym = active_symbol[0]
            threading.Thread(
                target=_save_sim_trades, args=(sym, state.sim_trades), daemon=True,
            ).start()

    def _resolve_open_trades(candles: list) -> None:
        """
        Advance ratcheting stops, then check every OPEN trade for SL/TP hits.

        P&L on an SL hit reflects the actual (possibly ratcheted) SL price rather than
        the original risk, so a breakeven stop closes at 0 pts and a trailing stop
        closes at a positive P&L when price reverses from a protected level.
        """
        _update_trailing_stops(candles)   # must run first so SL is current

        sym   = active_symbol[0]
        state = _state()
        ts_to_idx = {c.timestamp: i for i, c in enumerate(candles)}
        changed = False

        for trade in state.sim_trades:
            if trade.status != "OPEN":
                continue
            open_pos = ts_to_idx.get(trade.opened_at)
            if open_pos is None:
                continue
            # Only scan completed candles after the open (exclude in-progress last candle)
            for ci in range(open_pos + 1, len(candles) - 1):
                c = candles[ci]
                if trade.direction == "BULL":
                    if c.low <= trade.sl:          # SL hit — P&L from actual SL
                        trade.status    = "WIN" if trade.sl > trade.entry else "LOSS"
                        trade.pnl       = round(trade.sl - trade.entry, 4)
                        trade.closed_at = c.timestamp
                        changed = True
                        break
                    if c.high >= trade.tp:         # TP hit
                        trade.status    = "WIN"
                        trade.pnl       = round(trade.risk * RR_RATIO, 4)
                        trade.closed_at = c.timestamp
                        changed = True
                        break
                else:  # BEAR
                    if c.high >= trade.sl:         # SL hit — P&L from actual SL
                        trade.status    = "WIN" if trade.sl < trade.entry else "LOSS"
                        trade.pnl       = round(trade.entry - trade.sl, 4)
                        trade.closed_at = c.timestamp
                        changed = True
                        break
                    if c.low <= trade.tp:          # TP hit
                        trade.status    = "WIN"
                        trade.pnl       = round(trade.risk * RR_RATIO, 4)
                        trade.closed_at = c.timestamp
                        changed = True
                        break
        if changed:
            threading.Thread(
                target=_save_sim_trades, args=(sym, state.sim_trades), daemon=True,
            ).start()

    def _close_all_open_trades() -> None:
        """Manually close all OPEN sim trades at the current price."""
        state   = _state()
        candles = list(state.buffer)
        if not candles:
            return
        open_trades = [t for t in state.sim_trades if t.status == "OPEN"]
        if not open_trades:
            return
        for trade in open_trades:
            _close_at_market(trade, candles)
        # Cancel any pending re-entry — user explicitly flattened the book
        state.re_entry_pending = False
        asyncio.create_task(_redraw_chart())

    # ── Trading mode badge (throbbing) ────────────────────────────────────────
    async def _throb_badge() -> None:
        """Pulse the badge opacity on a 1.55 s interval until cancelled."""
        try:
            while True:
                if badge_ref.current and badge_ref.current.visible:
                    badge_ref.current.opacity = 0.35
                    badge_ref.current.update()
                await asyncio.sleep(1.55)
                if badge_ref.current and badge_ref.current.visible:
                    badge_ref.current.opacity = 1.0
                    badge_ref.current.update()
                await asyncio.sleep(1.55)
        except asyncio.CancelledError:
            pass

    def _show_badge(text: str, color: str) -> None:
        if throb_task[0] is not None:
            throb_task[0].cancel()
        if badge_ref.current:
            badge_ref.current.bgcolor  = color
            badge_ref.current.opacity  = 1.0
            badge_ref.current.visible  = True
            badge_ref.current.update()
        if badge_text_ref.current:
            badge_text_ref.current.value = text
            badge_text_ref.current.update()
        throb_task[0] = asyncio.create_task(_throb_badge())

    def _hide_badge() -> None:
        if throb_task[0] is not None:
            throb_task[0].cancel()
            throb_task[0] = None
        if badge_ref.current:
            badge_ref.current.visible = False
            badge_ref.current.update()

    # ── Sim toggle ─────────────────────────────────────────────────────────────
    def _sync_sim_ui() -> None:
        """Sync sim button + label to the current _sim_enabled state."""
        if sim_btn_ref.current:
            sim_btn_ref.current.icon = (
                ft.Icons.STOP_CIRCLE_OUTLINED if _sim_enabled else ft.Icons.PLAY_CIRCLE_OUTLINED
            )
            sim_btn_ref.current.icon_color = COL_SIG_BULL if _sim_enabled else COL_LABEL
            sim_btn_ref.current.update()
        if sim_lbl_ref.current:
            sim_lbl_ref.current.value = "SIM ON" if _sim_enabled else "SIM OFF"
            sim_lbl_ref.current.color = COL_SIG_BULL if _sim_enabled else COL_LABEL
            sim_lbl_ref.current.update()

    def _toggle_sim() -> None:
        global _sim_enabled
        _sim_enabled = not _sim_enabled
        _sync_sim_ui()
        if _sim_enabled:
            _show_badge("PAPER TRADING", "#F9A825")
        else:
            _hide_badge()

    # ── Live trading toggle ────────────────────────────────────────────────────
    def _sync_live_ui() -> None:
        if trade_live_btn_ref.current:
            trade_live_btn_ref.current.icon = (
                ft.Icons.SENSORS if _live_enabled else ft.Icons.SENSORS_OFF
            )
            trade_live_btn_ref.current.icon_color = "#C62828" if _live_enabled else COL_LABEL
            trade_live_btn_ref.current.update()
        if trade_live_lbl_ref.current:
            trade_live_lbl_ref.current.value = "LIVE ON" if _live_enabled else "LIVE OFF"
            trade_live_lbl_ref.current.color = "#C62828" if _live_enabled else COL_LABEL
            trade_live_lbl_ref.current.update()

    def _do_enable_live() -> None:
        global _live_enabled, _sim_enabled
        _live_enabled = True
        # Pause and lock the sim toggle
        _sim_enabled = False
        _sync_sim_ui()
        if sim_btn_ref.current:
            sim_btn_ref.current.disabled = True
            sim_btn_ref.current.update()
        _sync_live_ui()
        _show_badge("LIVE TRADING", "#C62828")

    def _do_disable_live() -> None:
        global _live_enabled
        _live_enabled = False
        # Re-enable the sim toggle
        if sim_btn_ref.current:
            sim_btn_ref.current.disabled = False
            sim_btn_ref.current.update()
        _sync_live_ui()
        # Restore badge based on sim state
        if _sim_enabled:
            _show_badge("PAPER TRADING", "#F9A825")
        else:
            _hide_badge()

    def _toggle_live() -> None:
        if _live_enabled:
            _do_disable_live()
        else:
            asyncio.create_task(_confirm_and_enable_live())

    async def _confirm_and_enable_live() -> None:
        """Show a confirmation dialog before enabling live trading."""
        confirmed: list[bool] = [False]

        def _on_confirm(e: ft.ControlEvent) -> None:
            confirmed[0] = True
            page.close(dlg)

        def _on_cancel(e: ft.ControlEvent) -> None:
            page.close(dlg)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                [ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color="#C62828"),
                 ft.Text("Enable Live Trading?", color="#C62828", weight=ft.FontWeight.BOLD)],
                spacing=8,
            ),
            content=ft.Text(
                "This will place REAL orders on TastyTrade.\n"
                "Every Prime-tier signal that passes active filters\n"
                "will immediately submit a market entry + SL + TP.",
                size=13,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=_on_cancel),
                ft.ElevatedButton(
                    "Enable Live Trading",
                    bgcolor="#C62828",
                    color=ft.Colors.WHITE,
                    on_click=_on_confirm,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.open(dlg)
        await asyncio.sleep(0)   # yield so the dialog renders
        # Wait for the dialog to be dismissed (poll since page.open is fire-and-forget)
        while dlg in page.overlay:
            await asyncio.sleep(0.05)
        if confirmed[0]:
            _do_enable_live()

    # ── Live order placement ───────────────────────────────────────────────────
    async def _place_live_orders(trade: SimTrade, contract_sym: str) -> None:
        """Submit entry (Market) + SL (Stop) + TP (Limit) orders to TastyTrade."""
        loop = asyncio.get_running_loop()

        # Fetch and cache account number on first live trade
        if not _live_account[0]:
            try:
                accounts = await loop.run_in_executor(None, client.get_accounts)
                acct_items = [
                    a.get("account", {}).get("account-number", "")
                    for a in accounts
                ]
                _live_account[0] = next((a for a in acct_items if a), "")
            except Exception as exc:
                log.error("Live order: cannot fetch accounts — %s", exc)
                return

        acct = _live_account[0]
        if not acct:
            log.error("Live order: no account number available")
            return

        action_open  = "Buy to Open"   if trade.direction == "BULL" else "Sell to Open"
        action_close = "Sell to Close" if trade.direction == "BULL" else "Buy to Close"

        def _leg(action: str) -> dict:
            return {
                "instrument-type": "Future",
                "symbol": contract_sym,
                "quantity": 1,
                "action": action,
            }

        orders_to_place = [
            # 1. Entry — Market
            {"time-in-force": "Day", "order-type": "Market",
             "legs": [_leg(action_open)]},
            # 2. Stop loss — Stop GTC
            {"time-in-force": "GTC", "order-type": "Stop",
             "stop-trigger": str(round(trade.sl, 4)),
             "legs": [_leg(action_close)]},
            # 3. Take profit — Limit GTC
            {"time-in-force": "GTC", "order-type": "Limit",
             "price": str(round(trade.tp, 4)),
             "legs": [_leg(action_close)]},
        ]

        for body in orders_to_place:
            try:
                resp = await loop.run_in_executor(
                    None, lambda b=body: client.place_order(acct, b)
                )
                oid = str(resp.get("order", {}).get("id", ""))
                if oid:
                    trade.live_order_ids.append(oid)
                    log.info("Live order placed: id=%s type=%s", oid, body["order-type"])
            except Exception as exc:
                log.error("Live order failed (%s): %s", body.get("order-type"), exc)

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
            f"Sim Trades:  {wins}W / {losses}L / {opens} open"
            f"    Accuracy: {acc_str}    P&L: {pnl_str} pts"
        )
        stats_ref.current.update()

    # ── UI update ─────────────────────────────────────────────────────────────
    async def _update_ui() -> None:
        # Self-deregister if this view is no longer mounted (chart ref is gone).
        # Prevents an orphaned closure from stealing the hook slot from a new view.
        if chart_container_ref.current is None:
            if _ui_refresh_hook[0] is _update_ui:
                _ui_refresh_hook[0] = None
            return

        sym     = active_symbol[0]
        state   = _state()
        candles = list(state.buffer)
        if not candles:
            return

        loop = asyncio.get_running_loop()

        # Heavy computation off the event loop (SMA + RSI + ADX + range bands + signals)
        sma50, sma200, rsi, adx, range_upper, range_lower, signals = await loop.run_in_executor(
            None, _compute_heavy, candles, state.key_levels,
        )
        state.cached_sma50        = sma50
        state.cached_sma200       = sma200
        state.cached_rsi          = rsi
        state.cached_adx          = adx
        state.cached_range_upper  = range_upper
        state.cached_range_lower  = range_lower
        state.cached_signals      = signals

        # Trade logic (fast — stays on event loop)
        for sig in signals:
            _open_sim_trade(sig, candles)
        _resolve_open_trades(candles)

        chart_w, chart_h, n_visible = _get_chart_dims()
        buf_start = _compute_buf_start(len(candles), n_visible)

        # Build canvas shapes off the event loop
        chart_canvas = await loop.run_in_executor(
            None, _build_chart,
            candles, sma50, sma200, signals, chart_w, chart_h, n_visible,
            state.key_levels, candle_w[0], price_scale[0], buf_start, state.sim_trades,
            rsi, adx, range_upper, range_lower,
        )

        # ── UI updates (back on event loop) ───────────────────────────────────
        if chart_container_ref.current:
            chart_container_ref.current.content = chart_canvas
            chart_container_ref.current.update()

        _update_stats()

        if chart_area_ref.current:
            chart_area_ref.current.height = chart_h + 4
            chart_area_ref.current.update()

        if live_btn_ref.current:
            live_btn_ref.current.visible = view_offset[0] > 0
            live_btn_ref.current.update()

        if price_ref.current:
            price_ref.current.value = f"${candles[-1].close:,.2f}"
            price_ref.current.update()

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
        hook = _ui_refresh_hook[0]
        if hook is not None:
            asyncio.create_task(hook())

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
            hook = _ui_refresh_hook[0]
            if hook is not None:
                asyncio.create_task(hook())

        # Background filesystem flush (live candles only, throttled ≤ 1/30 s)
        if not state.demo_mode:
            _schedule_flush(sym, list(state.buffer))

    # ── Stream loop (DXLink with exponential back-off → demo fallback) ───────
    async def _stream_loop() -> None:
        sym   = active_symbol[0]
        state = _state()

        # ── Phase 1: one-time setup per symbol activation ──────────────────
        # Seed the in-memory buffer from the filesystem cache so the chart
        # shows historical data immediately, before DXLink connects.
        state.buffer.clear()
        cached = _load_cache(sym)
        if cached:
            for c in cached:
                state.buffer.append(c)
            log.info("Seeded %d candles from cache for %s", len(cached), sym)
        else:
            log.info("No cache for %s; will request full 4h from DXLink", sym)

        state.demo_mode = False
        _refresh_demo_banner()

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
            hook = _ui_refresh_hook[0]
            if hook is not None:
                asyncio.create_task(hook())  # show cached candles while connecting

        # Resolve the streamer symbol once — stable for a given contract month.
        # If this fails the stream cannot start; fall straight through to demo.
        streamer_sym: Optional[str] = None
        try:
            streamer_sym = client.get_futures_streamer_symbol(sym)
            log.info("DXLink streamer-symbol for %s → %s", sym, streamer_sym)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Cannot resolve streamer symbol for %s: %s", sym, exc)
            cs.set_status(cs.ConnState.OFFLINE, f"{sym} — cannot resolve symbol")

        # ── Phase 2: exponential back-off reconnect loop ───────────────────
        attempt:      int   = 0      # consecutive failed attempts
        connected_at: float = 0.0   # wall time when the stream last went LIVE

        if streamer_sym is not None:
            while sym == active_symbol[0]:
                stream_exc:    Optional[Exception] = None
                auth_rejected: bool                = False

                try:
                    # Fetch a fresh token every attempt — handles token expiry
                    # that can occur during a prolonged network outage.
                    token_data = client.get_quote_token()
                    token      = (token_data.get("token")
                                  or token_data.get("dxlink-token", ""))
                    dxlink_url = (token_data.get("dxlink-url")
                                  or token_data.get("websocket-url", ""))
                    if not token or not dxlink_url:
                        raise ValueError(
                            f"Missing token/URL in quote token response: {token_data}"
                        )

                    # Gap-fill: request only candles newer than our last buffered one
                    if state.buffer:
                        from_time_ms = int(state.buffer[-1].timestamp * 1000) + 1
                    else:
                        from_time_ms = int((time.time() - BUFFER_MINUTES * 60) * 1000)

                    streamer = DXLinkStreamer(dxlink_url, token)
                    cs.set_status(cs.ConnState.LIVE, f"{sym} — DXLink streaming")
                    connected_at = time.time()

                    def on_candle(candle_dict: dict) -> None:
                        _process_candle_event(sym, candle_dict)

                    await streamer.stream_candles(
                        symbol=streamer_sym,
                        from_time_ms=from_time_ms,
                        on_candle=on_candle,
                    )

                    # stream_candles returned normally — connection dropped
                    # without raising.  Treat as a transient disconnect.
                    stream_exc = ConnectionError("DXLink stream ended unexpectedly")

                except asyncio.CancelledError:
                    raise   # symbol switch or page close — do not retry

                except RuntimeError as exc:
                    if "AUTH token rejected" in str(exc):
                        auth_rejected = True   # hard auth failure — no retry
                    else:
                        stream_exc = exc       # other RuntimeErrors are retryable

                except Exception as exc:
                    stream_exc = exc

                # Hard auth failure: abort immediately, no demo here — the
                # session is genuinely broken; user must re-authenticate.
                if auth_rejected:
                    log.error("DXLink AUTH rejected for %s — aborting", sym)
                    cs.set_status(cs.ConnState.OFFLINE, f"{sym} — auth rejected")
                    return   # leave demo_mode = False; chart shows last data

                # A stream that ran for ≥ 60 s was healthy.  Reset the attempt
                # counter so a brief outage after hours of uptime gets the full
                # RECONNECT_MAX_TRIES retries rather than picking up mid-count.
                if connected_at and time.time() - connected_at >= 60:
                    attempt = 0
                connected_at = 0.0

                attempt += 1

                if attempt > RECONNECT_MAX_TRIES or sym != active_symbol[0]:
                    if attempt > RECONNECT_MAX_TRIES:
                        log.warning(
                            "DXLink exhausted %d retries for %s — demo fallback",
                            RECONNECT_MAX_TRIES, sym,
                        )
                    break   # fall through to demo

                delay = min(
                    RECONNECT_BASE_DELAY * (2 ** (attempt - 1)),
                    RECONNECT_MAX_DELAY,
                )
                delay += random.uniform(0, delay * 0.1)   # ±10 % jitter

                cs.set_status(
                    cs.ConnState.OFFLINE,
                    f"{sym} — reconnecting {attempt}/{RECONNECT_MAX_TRIES}"
                    f" in {delay:.0f}s"
                    + (f" ({stream_exc})" if stream_exc else ""),
                )
                log.warning(
                    "DXLink %s retry %d/%d in %.0fs — %s",
                    sym, attempt, RECONNECT_MAX_TRIES, delay, stream_exc,
                )

                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    raise   # symbol switch during back-off sleep

        # ── Demo fallback (retries exhausted or symbol unresolvable) ──────
        if sym == active_symbol[0]:
            state.demo_mode = True
            cs.set_status(cs.ConnState.DEMO, f"{sym} — demo data (DXLink unavailable)")
            if not state.buffer:
                cached = _load_cache(sym)
                if cached:
                    for c in cached:
                        state.buffer.append(c)
                    log.info("Demo seeded from cache: %d candles for %s", len(cached), sym)
                else:
                    for c in _generate_demo_candles(sym):
                        state.buffer.append(c)
                now = time.time()
                state.min_start = now - (now % 60)
            _refresh_demo_banner()
            hook = _ui_refresh_hook[0]
            if hook is not None:
                asyncio.create_task(hook())

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

        # Cancel the outgoing symbol's stream via the registry (not the closure ref,
        # which may belong to a previous view instance for the same symbol).
        old_task = _active_stream_tasks.pop(active_symbol[0], None)
        if old_task is not None:
            old_task.cancel()
        elif stream_task[0] is not None:
            stream_task[0].cancel()  # fallback: cancel whatever the closure knows about

        # Switch active symbol
        active_symbol[0] = new_sym

        # Ensure a SymbolState exists (no-op if already cached)
        _load_for_symbol(new_sym)

        # Clear last-signal key so a snackbar fires for this symbol's signals
        _state().last_sig_key = ()

        # Start a new stream (or re-attach if one is already running for new_sym)
        existing = _active_stream_tasks.get(new_sym)
        if existing is not None and not existing.done():
            stream_task[0] = existing
        else:
            task = asyncio.create_task(_stream_loop())
            stream_task[0] = task
            _active_stream_tasks[new_sym] = task
            task.add_done_callback(lambda t, s=new_sym: _active_stream_tasks.pop(s, None))

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

        asyncio.create_task(_update_ui())

    # ── Lightweight chart-only redraw (used by pan/zoom) ─────────────────────
    async def _redraw_chart() -> None:
        """Rebuild canvas using cached SMA/RSI/ADX/signals — skips all expensive computation."""
        state   = _state()
        candles = list(state.buffer)
        if not candles:
            return
        chart_w, chart_h, n_visible = _get_chart_dims()
        buf_start = _compute_buf_start(len(candles), n_visible)
        loop = asyncio.get_running_loop()
        chart_canvas = await loop.run_in_executor(
            None, _build_chart,
            candles,
            state.cached_sma50, state.cached_sma200, state.cached_signals,
            chart_w, chart_h, n_visible,
            state.key_levels, candle_w[0], price_scale[0], buf_start, state.sim_trades,
            state.cached_rsi, state.cached_adx,
            state.cached_range_upper, state.cached_range_lower,
        )
        if chart_container_ref.current:
            chart_container_ref.current.content = chart_canvas
            chart_container_ref.current.update()
        if live_btn_ref.current:
            live_btn_ref.current.visible = view_offset[0] > 0
            live_btn_ref.current.update()

    # ── Gesture handlers ──────────────────────────────────────────────────────
    async def _on_pan_start(e) -> None:
        pan_accum[0] = 0.0

    async def _on_pan_update(e) -> None:
        dx = e.local_delta.x if e.local_delta is not None else 0.0
        pan_accum[0] += -dx                  # drag left → positive → older data
        delta = int(pan_accum[0] / candle_w[0])
        if delta != 0:
            pan_accum[0] -= delta * candle_w[0]
            total = len(list(_state().buffer))
            _, _, nv = _get_chart_dims()
            view_offset[0] = max(0, min(view_offset[0] + delta, max(0, total - nv)))
            await _redraw_chart()

    async def _on_scroll(e) -> None:
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
        await _redraw_chart()

    # ── Zoom / live-edge helpers ───────────────────────────────────────────────
    def _zoom_x(delta: int) -> None:
        candle_w[0] = max(3, min(30, candle_w[0] + delta))
        if zoom_lbl_ref.current:
            zoom_lbl_ref.current.value = str(candle_w[0])
            zoom_lbl_ref.current.update()
        asyncio.create_task(_redraw_chart())

    def _zoom_y(delta: int) -> None:
        price_scale[0] = (min(8.0, price_scale[0] * 1.3)
                          if delta > 0 else max(0.2, price_scale[0] / 1.3))
        asyncio.create_task(_redraw_chart())

    def _jump_to_live() -> None:
        view_offset[0] = 0
        asyncio.create_task(_update_ui())

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

    # Register this view's _update_ui as the active UI hook.
    # The stream event handlers call _ui_refresh_hook[0] instead of a closure-
    # specific function, so whichever view is currently mounted gets the update.
    _ui_refresh_hook[0] = _update_ui

    sym0 = DEFAULT_SYMBOL
    existing = _active_stream_tasks.get(sym0)
    if existing is not None and not existing.done():
        # A stream is already running for this symbol (user navigated back).
        # Re-attach: reuse the existing task; trigger a redraw from cached state.
        stream_task[0] = existing
        asyncio.create_task(_update_ui())
    else:
        # No live stream — start one.
        task = asyncio.create_task(_stream_loop())
        stream_task[0] = task
        _active_stream_tasks[sym0] = task
        task.add_done_callback(lambda t, s=sym0: _active_stream_tasks.pop(s, None))

    # Wire up resize handler — rebuilds chart dimensions whenever window changes
    page.on_resized = lambda _: asyncio.create_task(_rebuild_chart_dims())

    # Initial display — buffer is empty until DXLink delivers first candles
    init_chart_w, init_chart_h, init_n_visible = _get_chart_dims()
    candles_init = list(_state().buffer)
    sma50_init   = _compute_sma(candles_init, 50)
    sma200_init  = _compute_sma(candles_init, 200)
    rsi_init     = _compute_rsi(candles_init)
    adx_init     = _compute_adx(candles_init)
    range_upper_init, range_lower_init = _compute_range_bands(candles_init)
    signals_init = detect_signals(candles_init)
    for _s in signals_init:
        _s.divergence = _check_rsi_divergence(_s, candles_init, rsi_init)
        _s.pro_trend  = _check_pro_trend(_s, candles_init, sma200_init)
        _s.in_range   = _check_range_rotation(_s, candles_init, adx_init,
                                               range_upper_init, range_lower_init)

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
                        rsi=rsi_init,
                        adx=adx_init,
                        range_upper=range_upper_init,
                        range_lower=range_lower_init,
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
            ft.Container(width=8),
            ft.Container(width=16, height=3, bgcolor=COL_SMA200, border_radius=2),
            ft.Text("SMA 200", size=12, color=COL_SMA200),
            ft.Container(width=8),
            ft.Container(width=10, height=3, bgcolor=COL_RSI, border_radius=2),
            ft.Text(f"RSI {RSI_PERIOD}", size=12, color=COL_RSI),
            ft.Container(width=8),
            # Tier 1: prime (divergence + trend)
            ft.Text("▲", size=14, color=COL_SIG_BULL, weight=ft.FontWeight.BOLD),
            ft.Text("Prime", size=11, color=COL_SIG_BULL),
            ft.Container(width=4),
            # Tier 2: filtered (divergence, counter-trend) — hollow arrow hint
            ft.Text("▲", size=12, color=COL_SIG_BULL, weight=ft.FontWeight.BOLD),
            ft.Text("Filter", size=11, color="#666666"),
            ft.Container(width=4),
            # Tier 3: weak (no divergence)
            ft.Text("▲", size=11, color="#007A3D", weight=ft.FontWeight.BOLD),
            ft.Text("Weak", size=11, color="#007A3D"),
            ft.Container(width=8),
            ft.Text("▼", size=14, color=COL_SIG_BEAR, weight=ft.FontWeight.BOLD),
            ft.Text("Prime", size=11, color=COL_SIG_BEAR),
            ft.Container(width=4),
            ft.Text("▼", size=12, color=COL_SIG_BEAR, weight=ft.FontWeight.BOLD),
            ft.Text("Filter", size=11, color="#666666"),
            ft.Container(width=4),
            ft.Text("▼", size=11, color="#882222", weight=ft.FontWeight.BOLD),
            ft.Text("Weak", size=11, color="#882222"),
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
            ft.Container(width=4),
            ft.Checkbox(
                label="Trend Filter",
                value=True,
                active_color=COL_CHIP_ACT,
                check_color="white",
                label_style=ft.TextStyle(size=12, color=COL_LABEL),
                on_change=lambda e: trend_filter_enabled.__setitem__(0, e.control.value),
            ),
            ft.Container(width=4),
            ft.Checkbox(
                label="Range Filter",
                value=True,
                active_color=COL_ADX,
                check_color="white",
                label_style=ft.TextStyle(size=12, color=COL_LABEL),
                on_change=lambda e: range_filter_enabled.__setitem__(0, e.control.value),
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

            # Sim trade stats row
            ft.Row(
                [
                    # Sim toggle — default OFF
                    ft.IconButton(
                        ref=sim_btn_ref,
                        icon=ft.Icons.PLAY_CIRCLE_OUTLINED,
                        icon_color=COL_LABEL,
                        icon_size=20,
                        tooltip="Enable / disable simulated trading",
                        on_click=lambda e: _toggle_sim(),
                        style=ft.ButtonStyle(
                            padding=ft.padding.all(4),
                        ),
                    ),
                    ft.Text(
                        ref=sim_lbl_ref,
                        value="SIM OFF",
                        size=11,
                        color=COL_LABEL,
                        weight=ft.FontWeight.W_600,
                    ),
                    ft.Container(width=12),
                    ft.Text(
                        ref=stats_ref,
                        value="Sim Trades:  0W / 0L / 0 open    Accuracy: —    P&L: — pts",
                        size=11,
                        color=COL_LABEL,
                    ),
                    ft.Container(expand=True),
                    ft.TextButton(
                        "Close All",
                        style=ft.ButtonStyle(
                            color=COL_BEAR,
                            padding=ft.padding.symmetric(horizontal=8, vertical=2),
                        ),
                        tooltip="Close all open sim trades at current price",
                        on_click=lambda e: _close_all_open_trades(),
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
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