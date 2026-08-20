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
    sig_touch: int = 0          # anti-spam latch: one signal per touch
    opp_breaks: int = 0         # opposite zones broken since creation (SRR/RSS)
    srr: bool = False           # qualified as SRR (support) / RSS (resistance)
    was_valid: bool = False
    dead: bool = False          # 3-touch rule exhausted -> no more trades
    flips: int = 0              # role inversions — a level broken from both
                                # sides repeatedly is range noise, not a zone
    uid: int = 0
    born_index: int = 0
    in_zone_prev: bool = False

    @property
    def kind(self) -> str:
        # book naming: a broken zone keeps its ORIGIN name
        # broken Valid Resistance = I.VR (buy), broken Valid Support = I.VS (sell)
        if self.role == Role.SUPPORT:
            if self.state == State.INVERTED:
                return "I.VR" if self.was_valid else "RBS"
            return "SRR" if self.srr else ("V.S" if self.state == State.VALID else "S")
        if self.state == State.INVERTED:
            return "I.VS" if self.was_valid else "SBR"
        return "RSS" if self.srr else ("V.R" if self.state == State.VALID else "R")


@dataclass
class Signal:
    index: int
    side: str          # "buy" | "sell"
    kind: str          # "PO2" | "rejection"
    zone: str          # zone label at signal time
    price: float
    sl: float
    tp1: float
    tp2: float = 0.0
    tp3: float = 0.0


@dataclass
class Config:
    pivot_len: int = 10
    max_zones: int = 6
    big_move_atr: float = 1.5
    breakout_pct: float = 75.0
    min_zone_atr: float = 0.15
    max_zone_atr: float = 1.2
    atr_len: int = 14
    trend_filter: bool = True
    allow_counter_inv: bool = False  # "Trend is King" — inversion zones obey it too
    need_confirm: bool = True
    need_reject: bool = True    # confirmation candle must close outside the zone
    range_bars: int = 10        # both sides of structure broken this recently = range
    max_touches: int = 3
    max_zone_dist_atr: float = 5.0   # a zone this far from price is not tradeable
    max_flips: int = 2          # role inversions before a level is retired
    kill_on_stop: bool = True   # a zone whose signal got stopped out is finished
    need_micro_bos: bool = True # book: "a small BOS in the trade direction"
    micro_bos_len: int = 2
    break_even: bool = True     # book: risk free once the trade pays 1:1
    min_sl_atr: float = 0.5     # a stop closer than this gets swept by noise
    one_trade: bool = True      # book: don't overtrade — one setup at a time
    max_trade_bars: int = 300   # a setup that never resolves must not block forever
    rr_tp1: float = 1.0     # fallback TP1 = SL distance x this (book: at least 1:1)
    tp_max_r: float = 6.0   # a zone further than this many R is not a FIRST target


@dataclass
class Position:
    """The single setup that is currently being managed."""
    index: int
    side: str
    zone: str
    po2: bool
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    stat: int = 0          # 0 running · 1/2/3 TP reached · -1 stopped · -2 BE
    closed: bool = False
    be: bool = False       # stop already moved to entry
    uid: int = 0           # the zone that produced this setup

    @property
    def open(self) -> bool:
        return not self.closed


class SnrzEngine:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self.candles: List[Candle] = []
        self.zones: List[Zone] = []
        self.signals: List[Signal] = []
        self._tr: List[float] = []
        # structure trend (BOS-based, see trend_up/trend_down)
        self.last_high = self.prev_high = None
        self.last_low = self.prev_low = None
        self.trend_state = 0        # 1 up · -1 down · 0 undecided
        self.last_bos_up = -10**9
        self.last_bos_dn = -10**9
        self._bos_up_prev = False
        self._bos_dn_prev = False
        self.in_range = False
        # the single open setup (book: one trade at a time)
        self.position: Optional["Position"] = None
        self._zone_seq = 0

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
        # an exhausted zone must not keep the area reserved forever — once a
        # zone has had its touches the book says you redraw it
        return any(not z.dead and not (bot > z.top or top < z.bot)
                   for z in self.zones)

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
        self._zone_seq += 1
        self.zones.append(Zone(top, bot, role, uid=self._zone_seq, born_index=idx))
        while len(self.zones) > self.cfg.max_zones:
            victim = next((i for i, z in enumerate(self.zones) if z.dead), 0)
            self.zones.pop(victim)

    def _detect_pivots(self, idx: int, atr: float):
        n = self.cfg.pivot_len
        p = idx - n
        if p < n:
            return
        window = self.candles[p - n: p + n + 1]
        pc = self.candles[p]
        is_ph = all(w.high < pc.high for i, w in enumerate(window) if i != n)
        is_pl = all(w.low > pc.low for i, w in enumerate(window) if i != n)
        big = atr * self.cfg.big_move_atr
        # the movement made AFTER the pivot is the book's "Big Movement"
        run = self.candles[p: idx + 1]
        hi_run = max(w.high for w in run)
        lo_run = min(w.low for w in run)
        if is_ph:
            self.prev_high, self.last_high = self.last_high, pc.high
            top, bot = pc.high, max(pc.open, pc.close)
            if (top - lo_run) >= big and not self._overlaps(top, bot):
                self._add_zone(top, bot, Role.RESISTANCE, atr, idx)
        if is_pl:
            self.prev_low, self.last_low = self.last_low, pc.low
            top, bot = min(pc.open, pc.close), pc.low
            if (hi_run - bot) >= big and not self._overlaps(top, bot):
                self._add_zone(top, bot, Role.SUPPORT, atr, idx)

    def _update_trend(self, c: Candle, atr: float, idx: int):
        # The book calls a close beyond the last confirmed swing a Break of
        # Structure, and that is what turns the trend. A tiny poke past the
        # swing is not a break, so the close has to clear it by a buffer, and
        # only the MOMENT it breaks counts — not every bar it stays broken.
        bos_up = self.last_high is not None and c.close > self.last_high + atr * 0.1
        bos_dn = self.last_low is not None and c.close < self.last_low - atr * 0.1
        if bos_up and not self._bos_up_prev:
            self.last_bos_up = idx
            self.trend_state = 1
        if bos_dn and not self._bos_dn_prev:
            self.last_bos_dn = idx
            self.trend_state = -1
        self._bos_up_prev, self._bos_dn_prev = bos_up, bos_dn
        if self.trend_state == 0 and None not in (self.prev_high, self.prev_low):
            if self.last_high > self.prev_high and self.last_low > self.prev_low:
                self.trend_state = 1
            elif self.last_high < self.prev_high and self.last_low < self.prev_low:
                self.trend_state = -1
        # both sides broken recently = ranging; the book says stand aside
        self.in_range = (idx - self.last_bos_up) <= self.cfg.range_bars \
            and (idx - self.last_bos_dn) <= self.cfg.range_bars

    # ── targets: nearest opposite zones ahead of price, else 1R / 2R / 3R ──
    def _targets(self, is_buy: bool, entry: float, risk: float) -> tuple[float, float, float]:
        dists = []
        for z in self.zones:
            if z.dead:
                continue
            lvl = z.bot if is_buy else z.top
            ahead = (z.role == Role.RESISTANCE and lvl > entry) if is_buy \
                else (z.role == Role.SUPPORT and lvl < entry)
            if ahead:
                dists.append(abs(lvl - entry))
        dists.sort()
        # The book takes TP1 at the NEAREST liquidity. A zone sitting 15R away
        # is a destination, not a first target, so only zones within tp_max_r
        # feed TP1/TP2; anything beyond that can still serve as TP3.
        cap = risk * self.cfg.tp_max_r
        near = [d for d in dists if d <= cap]
        far = [d for d in dists if d > cap]
        d1 = near[0] if len(near) >= 1 else risk
        d2 = near[1] if len(near) >= 2 else max(d1 + risk, risk * 2)
        d3 = near[2] if len(near) >= 3 else (far[0] if far else max(d2 + risk, risk * 3))
        # book: RR at least 1:1, and each target beyond the previous one
        d1 = max(d1, risk * self.cfg.rr_tp1)
        d2 = max(d2, d1 + risk * 0.5)
        d3 = max(d3, d2 + risk * 0.5)
        if is_buy:
            return entry + d1, entry + d2, entry + d3
        return entry - d1, entry - d2, entry - d3

    def _update_position(self, c: Candle, idx: int):
        p = self.position
        if p is None or p.closed:
            return
        if p.side == "buy":
            if c.low <= p.sl:
                p.stat, p.closed = (-2 if p.be else -1), True
            elif c.high >= p.tp3:
                p.stat, p.closed = 3, True
            elif c.high >= p.tp2 and p.stat < 2:
                p.stat = 2
            elif c.high >= p.tp1 and p.stat < 1:
                p.stat = 1
        else:
            if c.high >= p.sl:
                p.stat, p.closed = (-2 if p.be else -1), True
            elif c.low <= p.tp3:
                p.stat, p.closed = 3, True
            elif c.low <= p.tp2 and p.stat < 2:
                p.stat = 2
            elif c.low <= p.tp1 and p.stat < 1:
                p.stat = 1
        # book: once the trade has paid 1:1, make it risk free (Zero Float)
        if self.cfg.break_even and not p.closed and p.stat >= 1 and not p.be:
            p.sl, p.be = p.entry, True
        if not p.closed and idx - p.index > self.cfg.max_trade_bars:
            p.closed = True
        # book: a zone whose signal got stopped out has been broken — finished
        if self.cfg.kill_on_stop and p.closed and p.stat == -1:
            for z in self.zones:
                if z.uid == p.uid:
                    z.dead = True

    @property
    def trend_up(self) -> bool:
        return self.trend_state == 1 and not self.in_range

    @property
    def trend_down(self) -> bool:
        return self.trend_state == -1 and not self.in_range

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
        self._update_trend(c, atr, idx)
        self._update_position(c, idx)
        # a zone far away from price is no longer tradeable — drop it
        max_dist = atr * self.cfg.max_zone_dist_atr
        self.zones = [z for z in self.zones
                      if (c.close - z.top if c.close > z.top else
                          z.bot - c.close if c.close < z.bot else 0.0) <= max_dist]
        bull_conf, bear_conf = self._confirm(c, self.candles[idx - 1])
        # book, confirmation list: "a small Break of Structure in the trade
        # direction" — without it a sell fires in the middle of a rally just
        # because one candle poked the zone
        prev = self.candles[max(0, idx - self.cfg.micro_bos_len):idx]
        bos_buy_ok = (not self.cfg.need_micro_bos) or not prev \
            or c.close > max(w.close for w in prev)
        bos_sell_ok = (not self.cfg.need_micro_bos) or not prev \
            or c.close < min(w.close for w in prev)
        cfg = self.cfg
        broke_support = broke_resistance = False
        # book: don't overtrade — while a setup is running, no new signal
        can_fire = not (cfg.one_trade and self.position is not None and self.position.open)

        for z in self.zones:
            if idx <= z.born_index:
                continue
            in_zone = c.low <= z.top and c.high >= z.bot

            if z.role == Role.SUPPORT:
                if self._bear_break(z.bot, c):
                    z.was_valid = z.state == State.VALID
                    z.role, z.state = Role.RESISTANCE, State.INVERTED
                    z.touches = z.sig_touch = 0
                    z.srr = False
                    z.flips += 1
                    # a level broken from both sides repeatedly is a range
                    # boundary, not a zone — the book calls sideway dangerous
                    z.dead = z.flips >= cfg.max_flips
                    broke_support = True
                elif in_zone and c.close >= z.bot and not z.dead:
                    if not z.in_zone_prev:
                        z.touches += 1
                        if z.state == State.FRESH and z.touches >= 2:
                            z.state = State.VALID          # Second Movement
                        if (z.state != State.INVERTED and z.touches > cfg.max_touches) or \
                           (z.state == State.INVERTED and z.touches > 2):
                            z.dead = True                  # 3-touch rule
                    tradable = (not z.dead) and (
                        (z.state == State.VALID and z.touches >= 2) or
                        (z.srr and z.touches >= 1) or
                        (z.state == State.INVERTED and 1 <= z.touches <= 2))
                    ok_trend = (not cfg.trend_filter) or self.trend_up or \
                        (cfg.allow_counter_inv and z.state == State.INVERTED)
                    ok_conf = (not cfg.need_confirm) or bull_conf
                    reject_ok = c.close > z.top if cfg.need_reject else c.close > z.bot
                    if tradable and ok_trend and ok_conf and z.sig_touch != z.touches \
                            and reject_ok and bos_buy_ok and can_fire and not out:
                        z.sig_touch = z.touches            # one signal per touch
                        swing_lo = min(w.low for w in self.candles[-3:])
                        raw_sl = min(z.bot, swing_lo) - atr * 0.15
                        risk = max(c.close - raw_sl, atr * cfg.min_sl_atr)
                        tp1, tp2, tp3 = self._targets(True, c.close, risk)
                        po2 = z.state == State.INVERTED and z.touches == 2
                        out.append(Signal(idx, "buy", "PO2" if po2 else "rejection",
                                          z.kind, c.close, c.close - risk, tp1, tp2, tp3))
                        self.position = Position(idx, "buy", z.kind, po2,
                                                 c.close, c.close - risk,
                                                 tp1, tp2, tp3, uid=z.uid)
            else:
                if self._bull_break(z.top, c):
                    z.was_valid = z.state == State.VALID
                    z.role, z.state = Role.SUPPORT, State.INVERTED
                    z.touches = z.sig_touch = 0
                    z.srr = False
                    z.flips += 1
                    z.dead = z.flips >= cfg.max_flips
                    broke_resistance = True
                elif in_zone and c.close <= z.top and not z.dead:
                    if not z.in_zone_prev:
                        z.touches += 1
                        if z.state == State.FRESH and z.touches >= 2:
                            z.state = State.VALID
                        if (z.state != State.INVERTED and z.touches > cfg.max_touches) or \
                           (z.state == State.INVERTED and z.touches > 2):
                            z.dead = True
                    tradable = (not z.dead) and (
                        (z.state == State.VALID and z.touches >= 2) or
                        (z.srr and z.touches >= 1) or
                        (z.state == State.INVERTED and 1 <= z.touches <= 2))
                    ok_trend = (not cfg.trend_filter) or self.trend_down or \
                        (cfg.allow_counter_inv and z.state == State.INVERTED)
                    ok_conf = (not cfg.need_confirm) or bear_conf
                    reject_ok = c.close < z.bot if cfg.need_reject else c.close < z.top
                    if tradable and ok_trend and ok_conf and z.sig_touch != z.touches \
                            and reject_ok and bos_sell_ok and can_fire and not out:
                        z.sig_touch = z.touches
                        swing_hi = max(w.high for w in self.candles[-3:])
                        raw_sl = max(z.top, swing_hi) + atr * 0.15
                        risk = max(raw_sl - c.close, atr * cfg.min_sl_atr)
                        tp1, tp2, tp3 = self._targets(False, c.close, risk)
                        po2 = z.state == State.INVERTED and z.touches == 2
                        out.append(Signal(idx, "sell", "PO2" if po2 else "rejection",
                                          z.kind, c.close, c.close + risk, tp1, tp2, tp3))
                        self.position = Position(idx, "sell", z.kind, po2,
                                                 c.close, c.close + risk,
                                                 tp1, tp2, tp3, uid=z.uid)
            z.in_zone_prev = in_zone

        # SRR / RSS qualification (book): a Support whose move broke >=2
        # Resistances becomes SRR (buy); a Resistance whose move broke >=2
        # Supports becomes RSS (sell).
        if broke_resistance or broke_support:
            for z in self.zones:
                if z.state == State.INVERTED or z.dead:
                    continue
                if broke_resistance and z.role == Role.SUPPORT and z.touches == 0 and c.close > z.top:
                    z.opp_breaks += 1
                    if z.opp_breaks >= 2:
                        z.srr = True
                if broke_support and z.role == Role.RESISTANCE and z.touches == 0 and c.close < z.bot:
                    z.opp_breaks += 1
                    if z.opp_breaks >= 2:
                        z.srr = True

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
                      f"entry={s.price:.2f} sl={s.sl:.2f} "
                      f"tp1={s.tp1:.2f} tp2={s.tp2:.2f} tp3={s.tp3:.2f}")
    print(f"\ntotal signals: {len(eng.signals)}")
