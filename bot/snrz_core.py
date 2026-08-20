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
    htf: bool = False           # analysis-timeframe zone (book: the TP2 zone)
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
    # Book p.41/p.44: zones are marked on the ANALYSIS timeframe and on the
    # chart itself at the same time — TP1 comes from a chart-timeframe zone,
    # TP2 from an analysis-timeframe one. htf_mult says how many chart candles
    # make one analysis candle (5m chart -> 15m analysis = 3).
    htf_mult: int = 3
    pivot_ltf: int = 5          # smaller -> more chart zones -> more signals
    pivot_htf: int = 8
    max_zones_ltf: int = 8
    max_zones_htf: int = 4
    life_ltf: int = 250         # chart bars
    life_htf: int = 60          # analysis bars
    pivot_len: int = 10         # (kept for compatibility)
    max_zones: int = 6
    big_move_atr: float = 1.2
    breakout_pct: float = 75.0
    min_zone_atr: float = 0.15
    max_zone_atr: float = 1.0
    atr_len: int = 14
    trend_filter: bool = True
    allow_counter_inv: bool = False  # "Trend is King" — inversion zones obey it too
    need_confirm: bool = True
    need_reject: bool = True    # confirmation candle must close outside the zone
    range_bars: int = 10        # both sides of structure broken this recently = range
    max_touches: int = 3
    max_zone_dist_atr: float = 6.0   # a zone this far from price is not tradeable
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
        # analysis-timeframe stream, aggregated from the chart candles
        self.htf_candles: List[Candle] = []
        self._htf_buf: List[Candle] = []
        self._htf_tr: List[float] = []
        # chart-structure fallback for when the analysis timeframe has not
        # printed enough swings to have an opinion yet
        self.c_high = self.c_prev_high = None
        self.c_low = self.c_prev_low = None

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
    def _overlaps(self, top: float, bot: float, htf: bool) -> bool:
        # Checked WITHIN a set only: a small chart zone is expected to sit
        # inside a big analysis zone. An exhausted zone reserves nothing —
        # once a zone has had its touches the book says you redraw it.
        return any(z.htf == htf and not z.dead and not (bot > z.top or top < z.bot)
                   for z in self.zones)

    def _add_zone(self, top: float, bot: float, role: Role, atr: float,
                  idx: int, htf: bool):
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
        self.zones.append(Zone(top, bot, role, uid=self._zone_seq,
                               htf=htf, born_index=idx))
        cap = self.cfg.max_zones_htf if htf else self.cfg.max_zones_ltf
        while sum(1 for z in self.zones if z.htf == htf) > cap:
            same = [i for i, z in enumerate(self.zones) if z.htf == htf]
            victim = next((i for i in same if self.zones[i].dead), same[0])
            self.zones.pop(victim)

    def _pivot_zones(self, series: List[Candle], n: int, atr: float,
                     idx: int, htf: bool, track_trend: bool):
        """One pivot pass over a candle series. Runs twice per bar: once on the
        chart candles and once on the aggregated analysis candles, because the
        book marks zones on both at the same time (p.41)."""
        j = len(series) - 1
        p = j - n
        if p < n:
            return
        window = series[p - n: p + n + 1]
        pc = series[p]
        is_ph = all(w.high < pc.high for i, w in enumerate(window) if i != n)
        is_pl = all(w.low > pc.low for i, w in enumerate(window) if i != n)
        big = atr * self.cfg.big_move_atr
        # the movement made AFTER the pivot is the book's "Big Movement"
        run = series[p: j + 1]
        hi_run = max(w.high for w in run)
        lo_run = min(w.low for w in run)
        if is_ph:
            if track_trend:
                self.prev_high, self.last_high = self.last_high, pc.high
            else:
                self.c_prev_high, self.c_high = self.c_high, pc.high
            top, bot = pc.high, max(pc.open, pc.close)
            if (top - lo_run) >= big and not self._overlaps(top, bot, htf):
                self._add_zone(top, bot, Role.RESISTANCE, atr, idx, htf)
        if is_pl:
            if track_trend:
                self.prev_low, self.last_low = self.last_low, pc.low
            else:
                self.c_prev_low, self.c_low = self.c_low, pc.low
            top, bot = min(pc.open, pc.close), pc.low
            if (hi_run - bot) >= big and not self._overlaps(top, bot, htf):
                self._add_zone(top, bot, Role.SUPPORT, atr, idx, htf)

    def _push_htf(self, c: Candle) -> bool:
        """Aggregate chart candles into analysis candles. Returns True on the
        bar that completes one."""
        m = max(1, self.cfg.htf_mult)
        self._htf_buf.append(c)
        if len(self._htf_buf) < m:
            return False
        b = self._htf_buf
        self.htf_candles.append(Candle(b[0].time, b[0].open,
                                       max(w.high for w in b),
                                       min(w.low for w in b), b[-1].close))
        self._htf_buf = []
        if len(self.htf_candles) > 1:
            prev = self.htf_candles[-2].close
            h = self.htf_candles[-1]
            self._htf_tr.append(max(h.high - h.low, abs(h.high - prev),
                                    abs(h.low - prev)))
        return True

    def _htf_atr(self) -> Optional[float]:
        n = self.cfg.atr_len
        if len(self._htf_tr) < n:
            return None
        return sum(self._htf_tr[-n:]) / n

    def _update_trend(self, c: Candle, atr: float, idx: int):
        """c is the last CLOSED analysis candle — "Trend is King" in the book
        means the higher-timeframe trend, not the chart's."""
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
        span = self.cfg.range_bars * max(1, self.cfg.htf_mult)
        self.in_range = (idx - self.last_bos_up) <= span \
            and (idx - self.last_bos_dn) <= span

    # ── targets (book p.44): TP1 from a CHART zone, TP2 from an ANALYSIS
    #    zone, TP3 whatever lies beyond — 1R / 2R / 3R when no zone is there ──
    def _targets(self, is_buy: bool, entry: float, risk: float) -> tuple[float, float, float]:
        cap = risk * self.cfg.tp_max_r
        d1 = d2 = d3 = None
        for z in self.zones:
            if z.dead:
                continue
            lvl = z.bot if is_buy else z.top
            ahead = (z.role == Role.RESISTANCE and lvl > entry) if is_buy \
                else (z.role == Role.SUPPORT and lvl < entry)
            if not ahead:
                continue
            d = abs(lvl - entry)
            if not z.htf and d <= cap and (d1 is None or d < d1):
                d1 = d
            if z.htf and (d2 is None or d < d2):
                d2 = d
            if d3 is None or d > d3:
                d3 = d
        d1 = risk if d1 is None else max(d1, risk)
        d2 = max(d1 + risk, risk * 2) if (d2 is None or d2 <= d1) else d2
        d3 = max(d2 + risk, risk * 3) if (d3 is None or d3 <= d2) else d3
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
    def _effective_trend(self) -> int:
        """The analysis timeframe decides — "Trend is King" means the higher
        timeframe. But until it has printed two swings it has no opinion, and
        blocking every trade forever is not what the book means, so the chart's
        own structure answers instead."""
        if self.trend_state != 0:
            return self.trend_state
        if None in (self.c_prev_high, self.c_prev_low):
            return 0
        if self.c_high > self.c_prev_high and self.c_low > self.c_prev_low:
            return 1
        if self.c_high < self.c_prev_high and self.c_low < self.c_prev_low:
            return -1
        return 0

    @property
    def trend_unknown(self) -> bool:
        """Neither timeframe can tell. No opinion is not the same as no trade."""
        return self._effective_trend == 0

    @property
    def trend_up(self) -> bool:
        return self._effective_trend == 1 and not self.in_range

    @property
    def trend_down(self) -> bool:
        return self._effective_trend == -1 and not self.in_range

    # ── main entry: feed one CLOSED candle ─────────────────────────────────
    def on_candle(self, c: Candle) -> List[Signal]:
        self._push_tr(c)
        self.candles.append(c)
        idx = len(self.candles) - 1
        atr = self._atr()
        out: List[Signal] = []
        if atr is None or atr <= 0 or idx < 1:
            return out

        # Book p.41: zones are marked on the analysis timeframe AND the chart,
        # so both passes run. Only the analysis pass feeds the trend.
        self._pivot_zones(self.candles, self.cfg.pivot_ltf, atr, idx,
                          htf=False, track_trend=False)
        if self._push_htf(c):
            atr_h = self._htf_atr()
            if atr_h and atr_h > 0:
                self._pivot_zones(self.htf_candles, self.cfg.pivot_htf, atr_h,
                                  idx, htf=True, track_trend=True)
                self._update_trend(self.htf_candles[-1], atr_h, idx)
        self._update_position(c, idx)
        # expire zones by age, and drop any that price has left far behind
        atr_h = self._htf_atr() or atr
        kept: List[Zone] = []
        for z in self.zones:
            ref = atr_h if z.htf else atr
            life = self.cfg.life_htf * max(1, self.cfg.htf_mult) if z.htf else self.cfg.life_ltf
            gap = (c.close - z.top if c.close > z.top else
                   z.bot - c.close if c.close < z.bot else 0.0)
            if idx - z.born_index <= life and gap <= ref * self.cfg.max_zone_dist_atr:
                kept.append(z)
        self.zones = kept
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
                    ok_trend = (not cfg.trend_filter) or self.trend_up \
                        or (self.trend_unknown and not self.in_range) \
                        or (cfg.allow_counter_inv and z.state == State.INVERTED)
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
                    ok_trend = (not cfg.trend_filter) or self.trend_down \
                        or (self.trend_unknown and not self.in_range) \
                        or (cfg.allow_counter_inv and z.state == State.INVERTED)
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
