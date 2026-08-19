"""
SNRZ core engine — the same zone logic as the TradingView / MT5 indicators,
implemented once in Python so the live bot (MT5 + crypto exchange) and the
backtester share identical rules.

Rules implemented (see docs/SNRZ_STRATEGY.md):
  * pivot-based Support / Resistance zones
  * two-movement validation (Valid S/R)
  * 75% breakout rule -> inversion (RBS / SBR / IVS / IVR)
  * touch counting + Power of Second Touch (PO2)
  * SNRZ engulfing / pin-bar confirmation
  * structure trend filter (HH/HL vs LH/LL)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional


class Role(IntEnum):
    SUPPORT = 1
    RESISTANCE = -1


class State(IntEnum):
    FRESH = 0      # one movement only
    VALID = 1      # two movements confirmed
    INVERTED = 2   # broken -> role flipped (RBS/SBR/IVS/IVR)


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float

    @property
    def bull(self) -> bool:
        return self.close > self.open

    @property
    def bear(self) -> bool:
        return self.close < self.open


@dataclass
class Zone:
    top: float
    bot: float
    role: Role
    state: State = State.FRESH
    touches: int = 0
    was_valid: bool = False
    born_index: int = 0
    in_zone_prev: bool = False

    @property
    def kind(self) -> str:
        if self.role == Role.SUPPORT:
            return ("IVS" if self.was_valid else "RBS") if self.state == State.INVERTED \
                else ("V.S" if self.state == State.VALID else "S")
        return ("IVR" if self.was_valid else "SBR") if self.state == State.INVERTED \
            else ("V.R" if self.state == State.VALID else "R")


@dataclass
class Signal:
    index: int
    side: str          # "buy" | "sell"
    kind: str          # "PO2" | "rejection" | "inversion_break"
    zone: str          # zone label at signal time
    price: float
    sl: float
    tp1: float


@dataclass
class Config:
    pivot_len: int = 8
    max_zones: int = 10
    big_move_atr: float = 1.5
    breakout_pct: float = 75.0
    min_zone_atr: float = 0.15
    max_zone_atr: float = 1.6
    atr_len: int = 14
    trend_filter: bool = True
    need_confirm: bool = True
    max_touches: int = 3
    rr_tp1: float = 1.0     # TP1 = SL distance x this (book: at least 1:1)


class SnrzEngine:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self.candles: List[Candle] = []
        self.zones: List[Zone] = []
        self.signals: List[Signal] = []
        self._tr: List[float] = []
        # structure trend
        self.last_high = self.prev_high = None
        self.last_low = self.prev_low = None

    # ── indicators ─────────────────────────────────────────────────────────
    def _atr(self) -> Optional[float]:
        n = self.cfg.atr_len
        if len(self._tr) < n:
            return None
        return sum(self._tr[-n:]) / n

    def _push_tr(self, c: Candle):
        if self.candles:
            pc = self.candles[-1].close
            tr = max(c.high - c.low, abs(c.high - pc), abs(c.low - pc))
        else:
            tr = c.high - c.low
        self._tr.append(tr)

    # ── 75% breakout rule ──────────────────────────────────────────────────
    def _bull_break(self, lvl: float, c: Candle) -> bool:
        if c.close > lvl and c.open > lvl:
            return True
        if c.close <= lvl:
            return False
        body_low = min(c.open, c.close)
        length = c.high - body_low          # ignore lower shadow
        if length <= 0:
            return False
        outside = c.high - max(lvl, body_low)
        return outside / length * 100.0 >= self.cfg.breakout_pct

    def _bear_break(self, lvl: float, c: Candle) -> bool:
        if c.close < lvl and c.open < lvl:
            return True
        if c.close >= lvl:
            return False
        body_high = max(c.open, c.close)
        length = body_high - c.low          # ignore upper shadow
        if length <= 0:
            return False
        outside = min(lvl, body_high) - c.low
        return outside / length * 100.0 >= self.cfg.breakout_pct

    # ── confirmation candles (SNRZ style) ──────────────────────────────────
    def _confirm(self, c: Candle, p: Candle) -> tuple[bool, bool]:
        bull_engulf = c.bull and p.bear and c.close >= p.open
        bear_engulf = c.bear and p.bull and c.close <= p.open
        rng = c.high - c.low
        bull_pin = rng > 0 and (min(c.open, c.close) - c.low) >= 0.6 * rng and c.close >= c.open
        bear_pin = rng > 0 and (c.high - max(c.open, c.close)) >= 0.6 * rng and c.close <= c.open
        return bull_engulf or bull_pin, bear_engulf or bear_pin

    # ── zone creation from pivots ──────────────────────────────────────────
    def _overlaps(self, top: float, bot: float) -> bool:
        return any(not (bot > z.top or top < z.bot) for z in self.zones)

    def _add_zone(self, top: float, bot: float, role: Role, atr: float, idx: int):
        mn, mx = atr * self.cfg.min_zone_atr, atr * self.cfg.max_zone_atr
        if top - bot < mn:
            mid = (top + bot) / 2
            top, bot = mid + mn / 2, mid - mn / 2
        if top - bot > mx:
            if role == Role.SUPPORT:
                top = bot + mx
            else:
                bot = top - mx
        self.zones.append(Zone(top, bot, role, born_index=idx))
        if len(self.zones) > self.cfg.max_zones:
            self.zones.pop(0)

    def _detect_pivots(self, idx: int, atr: float):
        n = self.cfg.pivot_len
        p = idx - n
        if p < n:
            return
        window = self.candles[p - n: p + n + 1]
        pc = self.candles[p]
        cur = self.candles[idx]
        is_ph = all(w.high < pc.high for i, w in enumerate(window) if i != n)
        is_pl = all(w.low > pc.low for i, w in enumerate(window) if i != n)
        big = atr * self.cfg.big_move_atr
        if is_ph:
            self.prev_high, self.last_high = self.last_high, pc.high
            top, bot = pc.high, max(pc.open, pc.close)
            if (top - cur.low) >= big * 0.5 and not self._overlaps(top, bot):
                self._add_zone(top, bot, Role.RESISTANCE, atr, idx)
        if is_pl:
            self.prev_low, self.last_low = self.last_low, pc.low
            top, bot = min(pc.open, pc.close), pc.low
            if (cur.high - bot) >= big * 0.5 and not self._overlaps(top, bot):
                self._add_zone(top, bot, Role.SUPPORT, atr, idx)

    @property
    def trend_up(self) -> bool:
        v = (self.last_high, self.prev_high, self.last_low, self.prev_low)
        return all(x is not None for x in v) and self.last_high > self.prev_high and self.last_low > self.prev_low

    @property
    def trend_down(self) -> bool:
        v = (self.last_high, self.prev_high, self.last_low, self.prev_low)
        return all(x is not None for x in v) and self.last_high < self.prev_high and self.last_low < self.prev_low

    # ── main entry: feed one CLOSED candle ─────────────────────────────────
    def on_candle(self, c: Candle) -> List[Signal]:
        self._push_tr(c)
        self.candles.append(c)
        idx = len(self.candles) - 1
        atr = self._atr()
        out: List[Signal] = []
        if atr is None or atr <= 0 or idx < 1:
            return out

        self._detect_pivots(idx, atr)
        bull_conf, bear_conf = self._confirm(c, self.candles[idx - 1])
        cfg = self.cfg

        for z in self.zones:
            if idx <= z.born_index:
                continue
            in_zone = c.low <= z.top and c.high >= z.bot

            if z.role == Role.SUPPORT:
                if self._bear_break(z.bot, c):
                    z.was_valid = z.state == State.VALID
                    z.role, z.state, z.touches = Role.RESISTANCE, State.INVERTED, 0
                elif in_zone and c.close >= z.bot:
                    if not z.in_zone_prev:
                        z.touches += 1
                        if z.state == State.FRESH and z.touches >= 2:
                            z.state = State.VALID
                    ok_trend = (not cfg.trend_filter) or self.trend_up or z.state == State.INVERTED
                    ok_conf = (not cfg.need_confirm) or bull_conf
                    if ok_trend and ok_conf and z.touches <= cfg.max_touches and c.close > z.bot:
                        sl = z.bot - atr * 0.3
                        risk = c.close - sl
                        out.append(Signal(idx, "buy",
                                          "PO2" if z.touches == 2 else "rejection",
                                          z.kind, c.close, sl, c.close + risk * cfg.rr_tp1))
            else:
                if self._bull_break(z.top, c):
                    z.was_valid = z.state == State.VALID
                    z.role, z.state, z.touches = Role.SUPPORT, State.INVERTED, 0
                elif in_zone and c.close <= z.top:
                    if not z.in_zone_prev:
                        z.touches += 1
                        if z.state == State.FRESH and z.touches >= 2:
                            z.state = State.VALID
                    ok_trend = (not cfg.trend_filter) or self.trend_down or z.state == State.INVERTED
                    ok_conf = (not cfg.need_confirm) or bear_conf
                    if ok_trend and ok_conf and z.touches <= cfg.max_touches and c.close < z.top:
                        sl = z.top + atr * 0.3
                        risk = sl - c.close
                        out.append(Signal(idx, "sell",
                                          "PO2" if z.touches == 2 else "rejection",
                                          z.kind, c.close, sl, c.close - risk * cfg.rr_tp1))
            z.in_zone_prev = in_zone

        self.signals.extend(out)
        return out


# ── quick CSV backtest demo ────────────────────────────────────────────────
if __name__ == "__main__":
    import csv
    import sys

    if len(sys.argv) < 2:
        print("usage: python snrz_core.py candles.csv   (columns: time,open,high,low,close)")
        sys.exit(1)

    eng = SnrzEngine()
    with open(sys.argv[1]) as f:
        for row in csv.DictReader(f):
            sigs = eng.on_candle(Candle(int(float(row["time"])), float(row["open"]),
                                        float(row["high"]), float(row["low"]), float(row["close"])))
            for s in sigs:
                print(f"[{s.index}] {s.side.upper():4s} {s.kind:9s} zone={s.zone:4s} "
                      f"entry={s.price:.2f} sl={s.sl:.2f} tp1={s.tp1:.2f}")
    print(f"\ntotal signals: {len(eng.signals)}")
