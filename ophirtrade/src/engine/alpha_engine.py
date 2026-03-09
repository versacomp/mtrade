import dataclasses
from typing import List, Optional, Tuple


@dataclasses.dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float


@dataclasses.dataclass
class Signal:
    candle_index: int
    direction: str  # "BULL" or "BEAR"
    level: float
    divergence: bool = False
    pro_trend: bool = False
    in_range: bool = False


class AlphaEngine:
    """
    Institutional Liquidity Grab Reversal Engine.
    Extracted from MTrade architecture.
    """

    def __init__(self):
        # Core Strategy Constants
        self.SWING_LOOKBACK = 3
        self.SIGNAL_LOOKBACK = 10
        self.SWING_WINDOW = 30

        # Filter Constants
        self.RSI_PERIOD = 14
        self.ADX_PERIOD = 14
        self.ADX_THRESHOLD = 25
        self.RANGE_PERIOD = 20
        self.RANGE_PROX = 0.25

        # The engine requires at least 200 candles to calculate the SMA 200 trend filter
        self.REQUIRED_BUFFER = 200

    def evaluate(self, raw_candles: List[dict]) -> dict:
        """
        Main entry point. Evaluates the live candle buffer and returns an execution intent.
        Returns: {"action": 0/1/2, "confidence": float, "direction": str, "level": float}
        """
        if len(raw_candles) < self.REQUIRED_BUFFER:
            return {"action": 0, "confidence": 0.0, "direction": "FLAT", "level": 0.0}

        # Convert raw UI dicts to internal Candle objects
        candles = [Candle(c['open'], c['high'], c['low'], c['close']) for c in raw_candles]

        # 1. Compute Heavy Indicators
        sma200 = self._compute_sma(candles, 200)
        rsi = self._compute_rsi(candles, self.RSI_PERIOD)
        adx = self._compute_adx(candles, self.ADX_PERIOD)
        range_upper, range_lower = self._compute_range_bands(candles, self.RANGE_PERIOD)

        # 2. Detect Base Signals (Wick Sweeps)
        signals = self._detect_signals(candles)

        if not signals:
            return {"action": 0, "confidence": 0.0, "direction": "FLAT", "level": 0.0}

        # 3. Apply the Three Filters to the most recent signal
        latest_sig = signals[-1]
        latest_sig.divergence = self._check_rsi_divergence(latest_sig, candles, rsi)
        latest_sig.pro_trend = self._check_pro_trend(latest_sig, candles, sma200)
        latest_sig.in_range = self._check_range_rotation(latest_sig, candles, adx, range_upper, range_lower)

        # 4. Tier 1 "Prime" Execution Logic
        # A signal MUST have divergence, agree with the macro trend, and be in a ranging environment
        if latest_sig.divergence and latest_sig.pro_trend and latest_sig.in_range:
            # We assign arbitrary high confidence for UI display since it passed all strict filters
            action_val = 1 if latest_sig.direction == "BULL" else 2  # 1=Buy, 2=Sell
            return {
                "action": action_val,
                "confidence": 99.9,
                "direction": latest_sig.direction,
                "level": latest_sig.level
            }

        return {"action": 0, "confidence": 0.0, "direction": "FLAT", "level": 0.0}

    # --- INDICATOR MATH (Ported directly from MTrade) ---

    def _swing_highs(self, candles: List[Candle]) -> List[Tuple[int, float]]:
        out = []
        limit = len(candles) - self.SWING_LOOKBACK
        for i in range(self.SWING_LOOKBACK, limit):
            h = candles[i].high
            if all(h >= candles[j].high for j in range(i - self.SWING_LOOKBACK, i + self.SWING_LOOKBACK + 1) if j != i):
                out.append((i, h))
        return out

    def _swing_lows(self, candles: List[Candle]) -> List[Tuple[int, float]]:
        out = []
        limit = len(candles) - self.SWING_LOOKBACK
        for i in range(self.SWING_LOOKBACK, limit):
            l = candles[i].low
            if all(l <= candles[j].low for j in range(i - self.SWING_LOOKBACK, i + self.SWING_LOOKBACK + 1) if j != i):
                out.append((i, l))
        return out

    def _detect_signals(self, candles: List[Candle]) -> List[Signal]:
        if len(candles) < self.SWING_LOOKBACK * 2 + self.SIGNAL_LOOKBACK + 2:
            return []

        completed = candles[:-1]  # Exclude the actively forming live candle
        n = len(completed)

        sh = self._swing_highs(completed)
        sl = self._swing_lows(completed)

        recent_sh = [(i, p) for i, p in sh if i > n - self.SWING_WINDOW]
        recent_sl = [(i, p) for i, p in sl if i > n - self.SWING_WINDOW]

        signals = []
        seen = set()

        for ci in range(max(0, n - self.SIGNAL_LOOKBACK), n):
            if ci in seen: continue
            c = completed[ci]

            # Bearish Grab Check
            for si, level in recent_sh:
                if si >= ci: continue
                wick_above = c.high - level
                reversal = level - c.close
                # 30% reversal rule
                if wick_above > 0 and c.close < level and reversal >= wick_above * 0.30:
                    signals.append(Signal(candle_index=ci, direction="BEAR", level=level))
                    seen.add(ci)
                    break

            if ci in seen: continue

            # Bullish Grab Check
            for si, level in recent_sl:
                if si >= ci: continue
                wick_below = level - c.low
                reversal = c.close - level
                if wick_below > 0 and c.close > level and reversal >= wick_below * 0.30:
                    signals.append(Signal(candle_index=ci, direction="BULL", level=level))
                    seen.add(ci)
                    break

        return signals

    def _check_rsi_divergence(self, sig: Signal, candles: List[Candle], rsi: List[Optional[float]]) -> bool:
        ci = sig.candle_index
        if ci >= len(candles) or ci >= len(rsi): return False
        rsi_grab = rsi[ci]
        if rsi_grab is None: return False

        lookback_start = max(0, ci - self.SWING_WINDOW)

        if sig.direction == "BULL":
            grab_low = candles[ci].low
            for ri in range(ci - 2, lookback_start - 1, -1):
                if rsi[ri] is None: continue
                if grab_low < candles[ri].low and rsi_grab > rsi[ri]:
                    return True
        else:
            grab_high = candles[ci].high
            for ri in range(ci - 2, lookback_start - 1, -1):
                if rsi[ri] is None: continue
                if grab_high > candles[ri].high and rsi_grab < rsi[ri]:
                    return True
        return False

    def _check_pro_trend(self, sig: Signal, candles: List[Candle], sma200: List[Optional[float]]) -> bool:
        ci = sig.candle_index
        if ci >= len(candles) or ci >= len(sma200) or sma200[ci] is None: return False
        close = candles[ci].close
        return (close > sma200[ci]) if sig.direction == "BULL" else (close < sma200[ci])

    def _check_range_rotation(self, sig: Signal, candles: List[Candle], adx: List[Optional[float]],
                              range_upper: List[Optional[float]], range_lower: List[Optional[float]]) -> bool:
        ci = sig.candle_index
        if ci >= len(candles) or ci >= len(adx): return False
        adx_val = adx[ci]
        if adx_val is None or adx_val >= self.ADX_THRESHOLD: return False

        if ci >= len(range_upper) or ci >= len(range_lower): return False
        upper = range_upper[ci]
        lower = range_lower[ci]
        if upper is None or lower is None: return False
        rng = upper - lower
        if rng <= 0: return False

        proximity = self.RANGE_PROX * rng
        if sig.direction == "BULL":
            return sig.level <= lower + proximity
        else:
            return sig.level >= upper - proximity

    def _compute_sma(self, candles: List[Candle], period: int) -> List[Optional[float]]:
        closes = [c.close for c in candles]
        result = []
        for i in range(len(closes)):
            if i < period - 1:
                result.append(None)
            else:
                result.append(sum(closes[i - period + 1: i + 1]) / period)
        return result

    def _compute_rsi(self, candles: List[Candle], period: int) -> List[Optional[float]]:
        closes = [c.close for c in candles]
        n = len(closes)
        result = [None] * n
        if n < period + 1: return result

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
            result[period] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

        for i in range(period + 1, n):
            diff = closes[i] - closes[i - 1]
            avg_gain = (avg_gain * (period - 1) + max(diff, 0.0)) / period
            avg_loss = (avg_loss * (period - 1) + max(-diff, 0.0)) / period
            if avg_loss == 0:
                result[i] = 100.0
            else:
                result[i] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
        return result

    def _compute_adx(self, candles: List[Candle], period: int) -> List[Optional[float]]:
        n = len(candles)
        result = [None] * n
        if n < period * 2: return result

        tr_vals, pdm_vals, mdm_vals = [], [], []
        for i in range(1, n):
            h, l = candles[i].high, candles[i].low
            h_prev = candles[i - 1].high
            l_prev = candles[i - 1].low
            c_prev = candles[i - 1].close
            tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
            up_move = h - h_prev
            dn_move = l_prev - l
            pdm = up_move if (up_move > dn_move and up_move > 0) else 0.0
            mdm = dn_move if (dn_move > up_move and dn_move > 0) else 0.0
            tr_vals.append(tr)
            pdm_vals.append(pdm)
            mdm_vals.append(mdm)

        def _dx(apdm_v, amdm_v, atr_v):
            if atr_v == 0: return 0.0
            pdi = apdm_v / atr_v * 100
            mdi = amdm_v / atr_v * 100
            return abs(pdi - mdi) / (pdi + mdi) * 100 if (pdi + mdi) > 0 else 0.0

        atr = sum(tr_vals[:period])
        apdm = sum(pdm_vals[:period])
        amdm = sum(mdm_vals[:period])
        dx_vals = [_dx(apdm, amdm, atr)]

        for k in range(period, len(tr_vals)):
            atr = atr - atr / period + tr_vals[k]
            apdm = apdm - apdm / period + pdm_vals[k]
            amdm = amdm - amdm / period + mdm_vals[k]
            dx_vals.append(_dx(apdm, amdm, atr))

        adx_val = sum(dx_vals[:period]) / period
        ci = period + period - 1
        if ci < n: result[ci] = adx_val
        for j in range(period, len(dx_vals)):
            adx_val = (adx_val * (period - 1) + dx_vals[j]) / period
            ci = period + j
            if ci < n: result[ci] = adx_val

        return result

    def _compute_range_bands(self, candles: List[Candle], period: int) -> Tuple[
        List[Optional[float]], List[Optional[float]]]:
        n = len(candles)
        upper, lower = [None] * n, [None] * n
        for i in range(period - 1, n):
            window = candles[i - period + 1: i + 1]
            upper[i] = max(c.high for c in window)
            lower[i] = min(c.low for c in window)
        return upper, lower