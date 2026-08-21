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
    src: str = "pivot"          # how it was drawn: S+S, R+R, S+R, R+S, pivot
    false_breaks: int = 0       # how many times it was broken and respected again
    fba: bool = False           # TWO of those = a False Breakout Area
    pend_bar: int = -1          # bar a still-unconfirmed break happened on
    pend_dir: int = 0           # +1 broke up · -1 broke down

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
class Swing:
    """A confirmed swing high or low, kept so a later swing at the same price
    can pair with it into a zone — the book's S+S / R+R / S+R / R+S."""
    price: float
    is_high: bool
    index: int


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
    max_zones_ltf: int = 14
    max_zones_htf: int = 8
    life_ltf: int = 600         # chart bars
    life_htf: int = 150         # analysis bars
    pivot_len: int = 10         # (kept for compatibility)
    max_zones: int = 6
    big_move_atr: float = 1.2
    breakout_pct: float = 75.0
    min_zone_atr: float = 0.15
    max_zone_atr: float = 0.4   # a zone 1 ATR tall is a region, not a zone —
                                # on H4 that drew 287-dollar bands across the chart
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
    min_sl_atr: float = 2.5     # a floor against noise, NOT a way to buy a
                                # win rate. A stop so wide it can never be hit
                                # turns every open loser into a fake 'win'.
    max_open: int = 3           # how many zones may carry a live order/trade
    min_rr: float = 1.0         # the next zone must be at least this many R away
    # Master class image 54 — the setups ranked by strength, "the market
    # respects these more and it is better to use them":
    #   1 Trend · 2 PO2 · 3 PO2 inversion · 4 V.S/V.R Inversion · 5 GAP
    #   6 V.S/V.R Fresh · 7 SBR/RBS
    # With only max_open slots, the strongest candidates must get them.
    rank_setups: bool = True
    # Image 44: only V.S / V.R / PO2 fresh / PO2 inversion / FBA may BE a
    # target, and "the first target is on the 5-minute, the second on the
    # 1-hour" — TP1 from a chart zone, TP2 from an analysis one.
    tp1_chart_tf: bool = True
    tp2_analysis_tf: bool = True
    max_trade_bars: int = 60    # a setup that never resolves must not linger
    rr_tp1: float = 1.0     # fallback TP1 = SL distance x this (book: at least 1:1)
    tp_max_r: float = 6.0   # a zone further than this many R is not a FIRST target

    # ── rules taken from the page-by-page read of the book ────────────────
    # p24: a zone is a band bracketing TWO swing points at a similar price
    #      (S+S, R+R, S+R, R+S). One pivot on its own is not a zone.
    pair_zones: bool = True
    pair_tol_atr: float = 0.5    # "a similar price" = within this many ATR
    pair_max_gap: int = 90       # ...and no further apart than this many bars
    pair_lookback: int = 10      # how many earlier swings to try to pair with
    # p39: zones are marked on W/D/4H/1H; the low timeframes only MONITOR.
    entries_htf_only: bool = False
    # p42: entry at the zone midpoint, stop just beyond the zone, TP1 at 1:1
    entry_edge: bool = True      # limit at the near edge of the zone, not its mid
    sl_buffer_atr: float = 0.8   # the book puts the stop just beyond the zone,
                                 # on the liquidity behind it
    # p47-50: a level broken and then respected again is a False Breakout
    # Area — a GOOD zone. Only a break that HOLDS inverts the zone.
    fba_bars: int = 3            # bars a break must hold before it inverts
    # p14: the small zone must sit inside the big one
    require_nested: bool = False
    order_expiry_bars: int = 10   # a limit order that never fills must expire —
                                  # while it rests it blocks every new signal

    # Book §15 (liquidity): "in gold the sell-side liquidity is usually taken
    # first and THEN the real move — about 80% of the time". So a sell placed
    # right after price has just swept a multi-week low is selling into the
    # reversal. Measured on the real data: the H4 chart sold 4 bars after the
    # 3942 bottom and the market then ran 600 points the other way.
    sweep_guard: bool = True
    sweep_bars: int = 40         # "a fresh extreme" = the low/high of this many bars
    sweep_recent: int = 10       # ...and it was made within this many bars
    # Master class images 16/17 — "Pump Base Pump" and "Dump Base Dump":
    # a strong impulse, then a sideways BASE, then the SAME move again. The
    # base is a continuation zone, and it is the only entry the strategy has
    # in a runaway trend — measured on H4, price never even touched a support
    # zone on 63 of the 67 bars where the trend was formally up.
    # Measured: drawing the base as a zone and resting a limit on it makes
    # results WORSE (median -0.07R -> -0.13R). The pattern is real and it is
    # in the book, but a continuation base is entered on the CONTINUATION, not
    # by waiting for price to come back into it — which is what a limit order
    # does. Off by default until it is traded the right way.
    base_zones: bool = False
    base_impulse_atr: float = 2.5   # the impulse must cover this many ATR
    base_impulse_bars: int = 6      # ...within this many bars
    base_min_bars: int = 4          # the base must last at least this long
    base_max_atr: float = 1.2       # ...and stay inside this many ATR

@dataclass
class PendingOrder:
    """A limit order resting AT one zone (book p41/p42).

    One order per zone, placed the moment the zone becomes tradable, and the
    target is the NEXT zone in the trade direction — that is the book's own
    picture: zones stacked up the chart, an order on each, each one aiming at
    the one above (or below) it. Earlier builds ran a single trade at a time
    with a 1R target, so most zones never got an order at all and the target
    had nothing to do with the chart.
    """
    bar: int
    side: str
    zone: str
    po2: bool
    uid: int
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float


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
    risk0: float = 0.0     # the ORIGINAL stop distance — break-even overwrites sl
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
        # one order and one trade PER ZONE — the book stacks zones and puts
        # an order on each; a single-slot engine could never do that.
        self.orders: List["PendingOrder"] = []
        self.trades: List["Position"] = []
        self.position: Optional["Position"] = None   # the newest, for display
        self._zone_seq = 0
        # analysis-timeframe stream, aggregated from the chart candles
        self.htf_candles: List[Candle] = []
        self._htf_buf: List[Candle] = []
        self._htf_tr: List[float] = []
        # chart-structure fallback for when the analysis timeframe has not
        # printed enough swings to have an opinion yet
        self.c_high = self.c_prev_high = None
        self.c_low = self.c_prev_low = None
        # confirmed swing points per set, for pairing them into zones (p24)
        self.swings_ltf: List[Swing] = []
        self.swings_htf: List[Swing] = []

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
                  idx: int, htf: bool, src: str = "pivot", valid: bool = False):
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
        self.zones.append(Zone(top, bot, role,
                               state=State.VALID if valid else State.FRESH,
                               uid=self._zone_seq, htf=htf, born_index=idx,
                               src=src))
        cap = self.cfg.max_zones_htf if htf else self.cfg.max_zones_ltf
        while sum(1 for z in self.zones if z.htf == htf) > cap:
            same = [i for i, z in enumerate(self.zones) if z.htf == htf]
            victim = next((i for i in same if self.zones[i].dead), same[0])
            self.zones.pop(victim)

    def _pivot_zones(self, series: List[Candle], n: int, atr: float,
                     idx: int, htf: bool, track_trend: bool):
        """One pivot pass over a candle series. Runs on the chart candles and
        on the aggregated analysis candles, because the book marks zones on
        both (p41).

        A confirmed swing does NOT become a zone on its own. Book p24 gives
        five ways to draw one and four of them pair TWO swing points at a
        similar price (S+S, R+R, S+R, R+S) — the band is what the two of them
        bracket. Marking a zone at every pivot is what filled the charts with
        levels the market had never actually respected twice."""
        if self.cfg.base_zones:
            self._base_zone(series, atr, idx, htf)
        j = len(series) - 1
        p = j - n
        if p < n:
            return
        window = series[p - n: p + n + 1]
        pc = series[p]
        is_ph = all(w.high < pc.high for i, w in enumerate(window) if i != n)
        is_pl = all(w.low > pc.low for i, w in enumerate(window) if i != n)
        if not (is_ph or is_pl):
            return

        if track_trend:
            if is_ph:
                self.prev_high, self.last_high = self.last_high, pc.high
            if is_pl:
                self.prev_low, self.last_low = self.last_low, pc.low
        else:
            if is_ph:
                self.c_prev_high, self.c_high = self.c_high, pc.high
            if is_pl:
                self.c_prev_low, self.c_low = self.c_low, pc.low

        cfg = self.cfg
        swings = self.swings_htf if htf else self.swings_ltf
        big = atr * cfg.big_move_atr
        run = series[p: j + 1]
        hi_run = max(w.high for w in run)
        lo_run = min(w.low for w in run)

        for is_high in (True, False):
            if is_high and not is_ph:
                continue
            if not is_high and not is_pl:
                continue
            price = pc.high if is_high else pc.low
            sw = Swing(price, is_high, p)

            if not cfg.pair_zones:
                # the old behaviour: one pivot, one zone
                if is_high:
                    top, bot = pc.high, max(pc.open, pc.close)
                    if (top - lo_run) >= big and not self._overlaps(top, bot, htf):
                        self._add_zone(top, bot, Role.RESISTANCE, atr, idx, htf,
                                       src="pivot", valid=False)
                else:
                    top, bot = min(pc.open, pc.close), pc.low
                    if (hi_run - bot) >= big and not self._overlaps(top, bot, htf):
                        self._add_zone(top, bot, Role.SUPPORT, atr, idx, htf,
                                       src="pivot", valid=False)
                swings.append(sw)
                continue

            # pair this swing with an earlier one at a similar price
            tol = atr * cfg.pair_tol_atr
            mate = None
            for prev in reversed(swings[-cfg.pair_lookback:]):
                if p - prev.index > cfg.pair_max_gap:
                    continue
                if abs(prev.price - price) <= tol:
                    mate = prev
                    break
            swings.append(sw)
            if mate is None:
                # p24, the FIFTH way to draw a zone: "when there is no S/R
                # pair to draw from, draw it from the engulf" — the book never
                # says "then draw nothing". Without this fallback the chart
                # ran at 2 live zones instead of 8 and the panel read Zones: 0.
                # An unpaired zone is born FRESH, so it still needs its two
                # touches before it may be traded.
                if is_high:
                    top, bot = pc.high, max(pc.open, pc.close)
                    if (top - lo_run) >= big and not self._overlaps(top, bot, htf):
                        self._add_zone(top, bot, Role.RESISTANCE, atr, idx, htf,
                                       src="pivot", valid=False)
                else:
                    top, bot = min(pc.open, pc.close), pc.low
                    if (hi_run - bot) >= big and not self._overlaps(top, bot, htf):
                        self._add_zone(top, bot, Role.SUPPORT, atr, idx, htf,
                                       src="pivot", valid=False)
                continue

            top = max(mate.price, price)
            bot = min(mate.price, price)
            if is_high and mate.is_high:
                role, src = Role.RESISTANCE, "R+R"
            elif (not is_high) and (not mate.is_high):
                role, src = Role.SUPPORT, "S+S"
            else:
                # S+R / R+S — the GAP band between a support and a resistance
                # (p51). Which side we trade it from depends on where price is.
                mid = (top + bot) / 2
                role = Role.SUPPORT if series[j].close > mid else Role.RESISTANCE
                src = "S+R" if mate.is_high else "R+S"
            if self._overlaps(top, bot, htf):
                continue
            # two touches already define it, so it is born VALID (p35: first
            # movement + second movement). The entry is the RETURN to it.
            self._add_zone(top, bot, role, atr, idx, htf, src=src, valid=True)

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
    def _rank(self, z: "Zone") -> int:
        """Image 54's strength order, lower = stronger."""
        inv = z.state == State.INVERTED
        if inv and z.touches == 1 and z.was_valid:
            return 3                                   # PO2 inversion
        if inv and z.touches == 1:
            return 2                                   # PO2 (2nd touch coming)
        if inv:
            return 4                                   # V.S / V.R inversion
        if z.src in ("S+R", "R+S", "PBP", "DBD"):
            return 5                                   # GAP / base
        if z.state == State.VALID:
            return 6                                   # V.S / V.R fresh
        return 7                                       # SBR / RBS

    def _base_zone(self, series: List[Candle], atr: float, idx: int, htf: bool):
        """Master class images 16/17 — Pump Base Pump / Dump Base Dump.

        A strong impulse, then a tight sideways BASE, then the same move
        continues. The base is the continuation zone: a buy zone after a pump,
        a sell zone after a dump. Without it the strategy has nothing to trade
        in a trend, because price never returns to the zones it left behind.
        """
        cfg = self.cfg
        n = cfg.base_min_bars
        j = len(series) - 1
        if j < cfg.base_impulse_bars + n + 1:
            return
        base = series[j - n + 1: j + 1]
        top = max(w.high for w in base)
        bot = min(w.low for w in base)
        if top - bot > atr * cfg.base_max_atr:
            return                                   # not a base, still moving

        # the impulse that led into it
        imp = series[j - n - cfg.base_impulse_bars + 1: j - n + 1]
        if not imp:
            return
        rise = top - min(w.low for w in imp)
        fall = max(w.high for w in imp) - bot
        need = atr * cfg.base_impulse_atr
        if rise >= need and rise >= fall:
            role, src = Role.SUPPORT, "PBP"          # pump · base · pump
        elif fall >= need:
            role, src = Role.RESISTANCE, "DBD"       # dump · base · dump
        else:
            return
        if self._overlaps(top, bot, htf):
            return
        # the base already IS the two movements, so it is born valid
        self._add_zone(top, bot, role, atr, idx, htf, src=src, valid=True)

    def _fresh_extreme(self, idx: int, is_high: bool) -> bool:
        """True when the last few bars have just taken out the extreme of the
        whole lookback — the liquidity grab of the book's liquidity section.
        Selling straight into a fresh low (or buying a fresh high) is taking
        the wrong side of it."""
        n = self.cfg.sweep_bars
        if idx < n:
            return False
        window = self.candles[idx - n + 1: idx + 1]
        recent = window[-self.cfg.sweep_recent:]
        if is_high:
            return max(w.high for w in recent) >= max(w.high for w in window)
        return min(w.low for w in recent) <= min(w.low for w in window)

    def _nested(self, z: "Zone") -> bool:
        """p14: the small zone sits INSIDE the big one."""
        if z.htf:
            return True
        return any(h.htf and not h.dead and h.role == z.role
                   and z.bot <= h.top and z.top >= h.bot for h in self.zones)

    def _can_be_target(self, z: "Zone") -> bool:
        """Master class image 44 names exactly which zones may BE a target:
        "V.S, V.R, PO2 Fresh, PO2 Inversion, False breakout area".
        A plain untested S/R, an SBR/RBS or an SRR/RSS is a place to enter
        FROM, not a level to aim AT."""
        return (z.state == State.VALID or z.state == State.INVERTED or z.fba)

    def _zones_ahead(self, is_buy: bool, entry: float) -> List[tuple]:
        """The opposite-role zones lying ahead of the entry, nearest first.

        Image 44: "the order we make from a BUY zone goes to a SELL zone — the
        opposite", and "the first target is on the 5-minute timeframe, the
        second on the 1-hour" — so TP1 prefers a CHART-timeframe zone and TP2
        an ANALYSIS-timeframe one. Returns (level, is_htf) pairs."""
        out = []
        for z in self.zones:
            if z.dead or not self._can_be_target(z):
                continue
            if is_buy and z.role == Role.RESISTANCE and z.bot > entry:
                out.append((z.bot, z.htf))
            elif (not is_buy) and z.role == Role.SUPPORT and z.top < entry:
                out.append((z.top, z.htf))
        out.sort(key=lambda t: t[0], reverse=not is_buy)
        return out

    def _levels(self, is_buy: bool, z: "Zone", c: Candle, atr: float):
        """Order at the zone, stop just beyond it, targets = the next zones.

        Returns None when there is no zone ahead to aim at — the book's "No
        Setup, No Trade". Inventing a 1R target where the chart offers nothing
        is exactly the guesswork this is meant to remove."""
        cfg = self.cfg
        wick_lo = min(w.low for w in self.candles[-3:])
        wick_hi = max(w.high for w in self.candles[-3:])
        if is_buy:
            entry = z.top                      # the edge price meets first
            raw_sl = min(z.bot, wick_lo) - atr * cfg.sl_buffer_atr
        else:
            entry = z.bot
            raw_sl = max(z.top, wick_hi) + atr * cfg.sl_buffer_atr
        risk = max(abs(entry - raw_sl), atr * cfg.min_sl_atr)
        sl = entry - risk if is_buy else entry + risk

        ahead = self._zones_ahead(is_buy, entry)
        # the first target must be worth the risk, or the setup is not one
        ahead = [t for t in ahead if abs(t[0] - entry) >= risk * cfg.min_rr]
        if not ahead:
            return None
        # image 44: TP1 from the chart timeframe, TP2 from the analysis one
        chart = [t for t in ahead if not t[1]]
        tp1 = (chart[0][0] if (chart and cfg.tp1_chart_tf) else ahead[0][0])
        rest = [t[0] for t in ahead if (t[0] > tp1 if is_buy else t[0] < tp1)]
        htf = [t[0] for t in ahead
               if t[1] and (t[0] > tp1 if is_buy else t[0] < tp1)]
        tp2 = (htf[0] if (htf and cfg.tp2_analysis_tf)
               else (rest[0] if rest else entry + (tp1 - entry) * 2))
        beyond = [x for x in rest if (x > tp2 if is_buy else x < tp2)]
        tp3 = beyond[0] if beyond else entry + (tp1 - entry) * 3
        return entry, sl, tp1, tp2, tp3

    def _has_order_or_trade(self, uid: int) -> bool:
        return any(o.uid == uid for o in self.orders) or \
            any(t.uid == uid and not t.closed for t in self.trades)

    def _fill_orders(self, c: Candle, idx: int):
        """A resting limit fills when price trades back to it, never on the bar
        it was placed."""
        keep = []
        for o in self.orders:
            if idx <= o.bar:
                keep.append(o)
                continue
            if c.low <= o.entry <= c.high:
                t = Position(idx, o.side, o.zone, o.po2, o.entry, o.sl,
                             o.tp1, o.tp2, o.tp3,
                             risk0=abs(o.entry - o.sl), uid=o.uid)
                self.trades.append(t)
                self.position = t
                continue                          # order consumed
            blown = (c.high >= o.sl) if o.side == "sell" else (c.low <= o.sl)
            gone = (c.low <= o.tp1) if o.side == "sell" else (c.high >= o.tp1)
            if blown or gone or idx - o.bar > self.cfg.order_expiry_bars:
                continue                          # invalidated or timed out
            keep.append(o)
        self.orders = keep

    def _update_trades(self, c: Candle, idx: int):
        for p in self.trades:
            if p.closed:
                continue
            # on the fill bar only the STOP may be judged: a buy limit fills
            # because price traded DOWN to it, so a low beyond the stop came
            # after the fill, but the bar's high may have printed before it
            entry_bar = idx == p.index
            if p.side == "buy":
                if c.low <= p.sl:
                    p.stat, p.closed = (-2 if p.be else -1), True
                elif entry_bar:
                    pass
                elif c.high >= p.tp3:
                    p.stat, p.closed = 3, True
                elif c.high >= p.tp2 and p.stat < 2:
                    p.stat = 2
                elif c.high >= p.tp1 and p.stat < 1:
                    p.stat = 1
            else:
                if c.high >= p.sl:
                    p.stat, p.closed = (-2 if p.be else -1), True
                elif entry_bar:
                    pass
                elif c.low <= p.tp3:
                    p.stat, p.closed = 3, True
                elif c.low <= p.tp2 and p.stat < 2:
                    p.stat = 2
                elif c.low <= p.tp1 and p.stat < 1:
                    p.stat = 1
            # book p41: once it pays, make it risk free
            if self.cfg.break_even and not p.closed and p.stat >= 1 and not p.be:
                p.sl, p.be = p.entry, True
            if not p.closed and idx - p.index > self.cfg.max_trade_bars:
                p.closed = True
            if self.cfg.kill_on_stop and p.closed and p.stat == -1:
                for z in self.zones:
                    if z.uid == p.uid:
                        z.dead = True
        if len(self.trades) > 400:
            self.trades = [t for t in self.trades if not t.closed][-200:]

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
        self._fill_orders(c, idx)
        self._update_trades(c, idx)
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
        # Every zone gets its OWN order, so nothing blocks anything: the only
        # limit is one live order per zone and a cap on how many run at once.
        live = len(self.orders) + sum(1 for t in self.trades if not t.closed)
        can_fire = live < cfg.max_open
        cand: list = []          # (rank, uid, side, kind, po2, levels)

        for z in self.zones:
            if idx <= z.born_index:
                continue
            in_zone = c.low <= z.top and c.high >= z.bot


            if z.role == Role.SUPPORT:
                if z.pend_dir == 0 and self._bear_break(z.bot, c):
                    z.pend_bar, z.pend_dir = idx, -1     # wait: does it hold?
                elif z.pend_dir == -1:
                    if c.close >= z.bot:                 # came back — p47: FBA
                        z.pend_bar, z.pend_dir = -1, 0
                        # 2026 master class, "FALSE BREAKOUT AREA": the area
                        # the market has broken TWICE and then respected again.
                        # One failed break is not an FBA — it takes two.
                        z.false_breaks += 1
                        z.fba = z.false_breaks >= 2
                        z.dead = False
                    elif idx - z.pend_bar >= cfg.fba_bars:
                        z.was_valid = z.state == State.VALID
                        z.role, z.state = Role.RESISTANCE, State.INVERTED
                        z.touches = z.sig_touch = 0
                        z.srr = z.fba = False
                        z.false_breaks = 0
                        z.flips += 1
                        z.dead = z.flips >= cfg.max_flips
                        z.pend_bar, z.pend_dir = -1, 0
                        broke_support = True
                elif in_zone and c.close >= z.bot and not z.dead:
                    if not z.in_zone_prev:
                        z.touches += 1
                        if z.state == State.FRESH and z.touches >= 2:
                            z.state = State.VALID          # Second Movement
                        if (z.state != State.INVERTED and z.touches > cfg.max_touches) or \
                           (z.state == State.INVERTED and z.touches > 2):
                            z.dead = True                  # 3-touch rule
                    # a paired zone already has its two touches by
                    # construction (p24/p35), so the RETURN to it is the entry
                    need = 1 if z.src != "pivot" else 2
                    tradable = (not z.dead) and z.pend_dir == 0 and (
                        (z.state == State.VALID and z.touches >= need) or
                        (z.srr and z.touches >= 1) or
                        (z.state == State.INVERTED and 1 <= z.touches <= 2))
                    if cfg.entries_htf_only and not z.htf:
                        tradable = False        # p39: entries only from W/D/4H/1H
                    if cfg.require_nested and not self._nested(z):
                        tradable = False        # p14/p35: small zone inside big
                    ok_trend = (not cfg.trend_filter) or self.trend_up \
                        or (self.trend_unknown and not self.in_range) \
                        or (cfg.allow_counter_inv and z.state == State.INVERTED)
                    # §15: buying right into a freshly swept HIGH is buying the
                    # liquidity grab, the mirror of the sell-side rule
                    if cfg.sweep_guard and self._fresh_extreme(idx, True):
                        ok_trend = False
                    ok_conf = (not cfg.need_confirm) or bull_conf
                    reject_ok = c.close > z.top if cfg.need_reject else c.close > z.bot
                    if tradable and ok_trend and can_fire \
                            and not self._has_order_or_trade(z.uid) \
                            and c.close > z.top:      # price is ABOVE the zone
                        lv = self._levels(True, z, c, atr)
                        if lv is not None:
                            cand.append((self._rank(z) if cfg.rank_setups else 0,
                                         z.uid, "buy", z.kind,
                                         z.state == State.INVERTED and z.touches == 2, lv))
            else:
                if z.pend_dir == 0 and self._bull_break(z.top, c):
                    z.pend_bar, z.pend_dir = idx, 1
                elif z.pend_dir == 1:
                    if c.close <= z.top:                 # came back — p47: FBA
                        z.pend_bar, z.pend_dir = -1, 0
                        # 2026 master class, "FALSE BREAKOUT AREA": the area
                        # the market has broken TWICE and then respected again.
                        # One failed break is not an FBA — it takes two.
                        z.false_breaks += 1
                        z.fba = z.false_breaks >= 2
                        z.dead = False
                    elif idx - z.pend_bar >= cfg.fba_bars:
                        z.was_valid = z.state == State.VALID
                        z.role, z.state = Role.SUPPORT, State.INVERTED
                        z.touches = z.sig_touch = 0
                        z.srr = z.fba = False
                        z.false_breaks = 0
                        z.flips += 1
                        z.dead = z.flips >= cfg.max_flips
                        z.pend_bar, z.pend_dir = -1, 0
                        broke_resistance = True
                elif in_zone and c.close <= z.top and not z.dead:
                    if not z.in_zone_prev:
                        z.touches += 1
                        if z.state == State.FRESH and z.touches >= 2:
                            z.state = State.VALID
                        if (z.state != State.INVERTED and z.touches > cfg.max_touches) or \
                           (z.state == State.INVERTED and z.touches > 2):
                            z.dead = True
                    # a paired zone already has its two touches by
                    # construction (p24/p35), so the RETURN to it is the entry
                    need = 1 if z.src != "pivot" else 2
                    tradable = (not z.dead) and z.pend_dir == 0 and (
                        (z.state == State.VALID and z.touches >= need) or
                        (z.srr and z.touches >= 1) or
                        (z.state == State.INVERTED and 1 <= z.touches <= 2))
                    if cfg.entries_htf_only and not z.htf:
                        tradable = False        # p39: entries only from W/D/4H/1H
                    if cfg.require_nested and not self._nested(z):
                        tradable = False        # p14/p35: small zone inside big
                    ok_trend = (not cfg.trend_filter) or self.trend_down \
                        or (self.trend_unknown and not self.in_range) \
                        or (cfg.allow_counter_inv and z.state == State.INVERTED)
                    # §15: gold takes the sell-side liquidity FIRST and then
                    # moves — selling just after a fresh low is selling the
                    # reversal. This is what sold the 3942 bottom.
                    if cfg.sweep_guard and self._fresh_extreme(idx, False):
                        ok_trend = False
                    ok_conf = (not cfg.need_confirm) or bear_conf
                    reject_ok = c.close < z.bot if cfg.need_reject else c.close < z.top
                    if tradable and ok_trend and can_fire \
                            and not self._has_order_or_trade(z.uid) \
                            and c.close < z.bot:      # price is BELOW the zone
                        lv = self._levels(False, z, c, atr)
                        if lv is not None:
                            cand.append((self._rank(z) if cfg.rank_setups else 0,
                                         z.uid, "sell", z.kind,
                                         z.state == State.INVERTED and z.touches == 2, lv))
            z.in_zone_prev = in_zone

        # image 54: when more zones qualify than there are slots, the
        # STRONGEST setups take them — not whichever was created first
        cand.sort(key=lambda t: t[0])
        for rank, uid, side, kind, po2, lv in cand[:max(0, cfg.max_open - live)]:
            entry, sl, tp1, tp2, tp3 = lv
            out.append(Signal(idx, side, "PO2" if po2 else "limit",
                              kind, entry, sl, tp1, tp2, tp3))
            self.orders.append(PendingOrder(idx, side, kind, po2, uid,
                                            entry, sl, tp1, tp2, tp3))

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
