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
    far: bool = False           # too far from price to be worth drawing
    refined: bool = False       # the analysis band was redrawn from the chart
    raw_top: float = 0.0        # the band as the ANALYSIS timeframe drew it,
    raw_bot: float = 0.0        # kept only so the chart can still show it
    await_pull: int = -1        # broke on this bar and is waiting for the
                                # pullback swing that becomes the real level

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
    # The captain's pairing, in his own words and three worked examples:
    #   1H  -> the zone is marked, go to 15m: refine, confirm, enter
    #   30m -> ...go straight to 5m
    #   15m -> ...go straight to 1m
    # Every one of them is TWO rungs of the standard ladder, with the middle
    # rung deliberately skipped — the SKIP column of the book's TimeFrames
    # table. Set chart_minutes and the analysis timeframe follows from it;
    # leave it 0 and htf_mult is used as given.
    # ONE TIMEFRAME. The captain's ladder marks the zones two rungs up and
    # drops to the chart to confirm and enter. This turns that off: the chart
    # marks its own zones, confirms on itself, and opens the trade on itself —
    # 1-minute zones traded on the 1-minute chart.
    #
    # With no analysis timeframe there is no separate trend to obey either, so
    # the trend filter reads the CHART's own structure, and TP2 falls back to
    # the next chart zone instead of preferring an analysis one. Nothing else
    # in the rules changes: the same five ways of drawing a zone, the same
    # entries, the same targets.
    single_tf: bool = True
    chart_minutes: int = 0
    htf_rungs: int = 2
    htf_mult: int = 3
    pivot_ltf: int = 5          # smaller -> more chart zones -> more signals
    pivot_htf: int = 8
    max_zones_ltf: int = 14
    max_zones_htf: int = 8
    # A zone is killed by a BREAK, not by a clock. These two stay only for
    # anyone who wants the old behaviour back; both are off by default.
    expire_by_age: bool = False
    life_ltf: int = 600         # chart bars
    life_htf: int = 150         # analysis bars
    pivot_len: int = 10         # (kept for compatibility)
    max_zones: int = 6
    big_move_atr: float = 1.2
    # "if ONE of them, the first movement or the second, is bigger, it is
    # BETTER" — the captain's own words. "both" = the old, stricter reading;
    # "either" = at least one of the two; "off" = never reject on size.
    big_move_rule: str = "either"
    need_big_move: bool = True  # (kept: the pullback re-anchor still reads it)
    breakout_pct: float = 75.0
    min_zone_atr: float = 0.15
    # The analysis zone is drawn from the STRUCTURE of the turn, so its cap is
    # a sanity bound rather than a shape — the shape comes from the candles.
    htf_struct_zone: bool = True
    htf_struct_bars: int = 3
    max_zone_atr_htf: float = 1.0   # ...and the analysis zones have their own
    max_zone_atr: float = 0.4   # a zone 1 ATR tall is a region, not a zone —
                                # on H4 that drew 287-dollar bands across the chart
    atr_len: int = 14
    trend_filter: bool = True
    allow_counter_inv: bool = False  # "Trend is King" — inversion zones obey it too
    range_bars: int = 10        # both sides of structure broken this recently = range
    max_touches: int = 3
    # Far zones are HIDDEN, not deleted: the level a trade is aiming at is
    # usually the one price has not reached yet.
    max_zone_dist_atr: float = 6.0   # ...further than this = hide it
    drop_far_zones: bool = False     # ...delete it instead (old behaviour)
    max_flips: int = 2          # role inversions before a level is retired
    kill_on_stop: bool = True   # a zone whose signal got stopped out is finished
    break_even: bool = True     # book: risk free once the trade pays 1:1
    # Master class image 41, the fully worked trade: zone 4706-4720, stop
    # 4698.67, and a RED LINE drawn at 4732.33 — exactly 1:1 against that
    # stop. "On the 5-minute we go break-even and take money off the account,
    # and we wait for the target." So break-even happens at 1R, NOT when the
    # first zone target is reached: that zone can be far away and the trade
    # would ride all the way back to the stop before it ever got protected.
    be_at_r: float = 1.0
    # The stop sits behind the WICK, on the liquidity — and nothing else may
    # widen it. This floor used to be 2.5 ATR, which set the stop on 97-100%
    # of trades instead of the wick: the median wick sits 1.3 ATR away, so my
    # floor was silently doubling every stop. Measured on 83 days of real
    # XAUUSD, dropping it moved the median from -0.038R to +0.057R.
    min_sl_atr: float = 0.0
    # ...but there has to be a CEILING. The wick is read from the last 3 chart
    # candles, and when price spiked far below the zone and closed back inside
    # (a false break, which the book calls a good sign) that wick can sit ten
    # zone-heights away. Measured on M5: the stop is a median 5.9x the zone
    # height, p90 11.7x, worst 52x. A stop that wide makes the R:R marginal and
    # the position unfundable on a small account. 0 = no ceiling.
    # Every value from 5 ATR down to 2 beats no ceiling at all, which is what
    # makes it a real effect rather than a fitted number; 3.0 turns away 1.7%
    # of the setups and takes the median from +0.056 to +0.064.
    max_sl_atr: float = 3.0
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
    # Images 31/33/52 — the GAP: "the space between a support and a
    # resistance; when that space is created the market fills it back with
    # 80% probability". Unlike the S+S / R+R pairs this is TWO levels at
    # DIFFERENT prices and the zone is the band between them, so it needs its
    # own height cap — the ordinary one (0.4 ATR) would never allow it.
    # The book marks GAP on H1/H4/Daily/Weekly only.
    gap_zones: bool = True
    gap_min_atr: float = 0.6     # the two levels must be at least this far apart
    gap_max_atr: float = 2.5     # ...and at most this far
    gap_htf_only: bool = True    # the book marks GAP on H1/H4/D/W only
    max_trade_bars: int = 60    # a setup that never resolves must not linger

    # ── rules taken from the page-by-page read of the book ────────────────
    # p24: a zone is a band bracketing TWO swing points at a similar price
    #      (S+S, R+R, S+R, R+S). One pivot on its own is not a zone.
    pair_zones: bool = True
    pair_tol_atr: float = 0.5    # "a similar price" = within this many ATR
    pair_max_gap: int = 150      # ...and no further apart than this many bars
    pair_lookback: int = 20      # how many earlier swings to try to pair with
    # Image 31: "use a Line Chart to see and mark the zones better", and
    # images 12/13 print the same chart as candles AND as a line with the level
    # sitting on the CLOSE level. A line-chart zone is drawn from repeated
    # CLOSES, so it finds levels a wick pivot cannot see.
    line_zones: bool = True
    # ...and on a line chart EVERY peak is an R and every trough an S
    line_single_levels: bool = True
    # The captain's 30m chart of an RBS: an R at 4,433.9 breaks upward, price
    # runs to 4,530 and pulls back to make an S at ~4,460 — ABOVE the broken
    # level, which price never returns to. "When an R breaks and on the way
    # back an S low forms, that is where we buy." So the level to trade is the
    # NEW swing the pullback makes, not the old band sitting where the break
    # happened; a limit left on the old band would simply never fill.
    flip_needs_pullback: bool = True
    engulf_zones: bool = True    # p24 method 5, drawn per images 27/28
    engulf_full_candle: bool = True  # ...and the zone is the WHOLE candle
    momentum_zones: bool = True  # image 43: one momentum candle IS a zone
    momentum_body_atr: float = 0.8   # ...and this is how big "momentum" is
    # Image 43 boxes the WHOLE candle, the way the engulf rule explicitly does
    # («کل کندل بدنه با سایه میشه زون»). Confirmed by the captain. Measured
    # both ways it is a dead heat (+0.0250 vs +0.0257 pooled over ~6450
    # trades), so this is the book's answer, not the backtest's.
    momentum_full_candle: bool = True
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
    # The captain's middle layer: mark the zones on the 1-hour, drop to the
    # 15-minute and draw SMALLER zones INSIDE them, then confirm on the
    # 5-minute. So an analysis zone is not traded at its own wide edge — it is
    # first TIGHTENED to the chart-timeframe structure standing inside it.
    # ON, and it is the captain's own rule, not an optimisation:
    #   «در یک دقیقه اون زون ۱۵ دقیقه برداشته میشه، روی زونی که یک دقیقه
    #    مشخص کرده حساب میشه، و وقتی قیمت پول‌بک زد معامله انجام میشه»
    # On the entry chart the analysis band is LIFTED and the tight band the
    # entry chart drew inside it is what the trade uses. The wide band is not
    # deleted — raw_top/raw_bot keep it for the drawing and for context — but
    # the order, the stop and the touches all belong to the tight one.
    # I had this OFF before because the screenshots show the big box unchanged;
    # they do, ON ITS OWN CHART. Both are true: the box stays drawn where the
    # analysis timeframe put it, and the trade is taken off the smaller one.
    refine_htf: bool = True
    require_nested: bool = False   # as a GATE: measured much worse
    nested_bonus: bool = True      # ...as a PREFERENCE instead
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
    peak: int = 0          # best target reached before the exit (stat loses it)
    stat: int = 0          # 0 running · 1/2/3 TP reached · -1 stopped · -2 BE
    exit_px: float = 0.0   # where the REST of the position actually came off.
                           # Without it a trade that simply ran out of bars had
                           # no exit price at all and the scoring had to guess.
    closed: bool = False
    be: bool = False       # stop already moved to entry
    uid: int = 0           # the zone that produced this setup

    @property
    def open(self) -> bool:
        return not self.closed


class SnrzEngine:
    TF_LADDER = (1, 5, 15, 30, 60, 240, 1440, 10080)

    @staticmethod
    def analysis_minutes(chart_minutes: int, rungs: int = 2) -> int:
        """Two rungs up the standard ladder, skipping the one between."""
        rung = SnrzEngine.TF_LADDER
        i = 0
        while i < len(rung) - 1 and rung[i] < chart_minutes:
            i += 1
        return rung[min(i + rungs, len(rung) - 1)]

    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config()
        if self.cfg.chart_minutes > 0:
            up = self.analysis_minutes(self.cfg.chart_minutes, self.cfg.htf_rungs)
            self.cfg.htf_mult = max(2, round(up / self.cfg.chart_minutes))
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
        # the LINE CHART's own swings — closes only, kept apart so a close
        # pivot can never pair with a wick pivot (that would be two different
        # charts glued together)
        self.swings_line_ltf: List[Swing] = []
        self.swings_line_htf: List[Swing] = []

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
    def _overlaps(self, top: float, bot: float, htf: bool) -> bool:
        # Checked WITHIN a set only: a small chart zone is expected to sit
        # inside a big analysis zone. An exhausted zone reserves nothing —
        # once a zone has had its touches the book says you redraw it.
        return any(z.htf == htf and not z.dead and not (bot > z.top or top < z.bot)
                   for z in self.zones)

    def _add_zone(self, top: float, bot: float, role: Role, atr: float,
                  idx: int, htf: bool, src: str = "pivot", valid: bool = False):
        # The captain draws the ANALYSIS zone wide and then tightens it on the
        # middle timeframe, so the two sets do not share one cap.
        cap = self.cfg.max_zone_atr_htf if htf else self.cfg.max_zone_atr
        mn, mx = atr * self.cfg.min_zone_atr, atr * cap
        if top - bot < mn:
            mid = (top + bot) / 2
            top, bot = mid + mn / 2, mid - mn / 2
        if top - bot > mx:
            if role == Role.SUPPORT:
                top = bot + mx
            else:
                bot = top - mx
        raw_top, raw_bot = top, bot
        if htf and self.cfg.refine_htf:
            top, bot = self._refine_from_chart(top, bot, role)
        self._zone_seq += 1
        self.zones.append(Zone(top, bot, role,
                               state=State.VALID if valid else State.FRESH,
                               uid=self._zone_seq, htf=htf, born_index=idx,
                               src=src + (" ref" if top != raw_top or bot != raw_bot
                                          else ""),
                               raw_top=raw_top, raw_bot=raw_bot,
                               refined=(top != raw_top or bot != raw_bot)))
        cap = self.cfg.max_zones_htf if htf else self.cfg.max_zones_ltf
        while sum(1 for z in self.zones if z.htf == htf) > cap:
            same = [i for i, z in enumerate(self.zones) if z.htf == htf]
            victim = next((i for i in same if self.zones[i].dead), same[0])
            self.zones.pop(victim)

    def _engulf_zone(self, series: List[Candle], p: int, is_high: bool,
                     atr: float, idx: int, htf: bool,
                     hi_run: float, lo_run: float, big: float):
        """Book p24 method 5, drawn the way images 27/28 describe it.

        «لەو شوێنەوەی کە دوستی کردووە بە بەرز بوون و کاندڵێک پێش خۆی کامیان
        کوورتر بوون ئەوە دیاری ئەکەین» — mark it where the wicks have
        befriended, taking whichever of the two candles is SHORTER.

        So this needs a real engulfing PAIR around the pivot: at a pivot low a
        bullish candle that engulfs the bearish one before it, at a pivot high
        the mirror. The zone is the shorter candle's body — not the pivot
        candle's wick, and not every pivot that failed to find a mate."""
        cfg = self.cfg
        # the pair may straddle the pivot on either side: (p-1, p) or (p, p+1)
        for a in ((p - 1, p) if cfg.engulf_zones else ()):
            b = a + 1
            if a < 0 or b >= len(series):
                continue
            c1, c2 = series[a], series[b]
            up = c2.close > c2.open
            if is_high and up:
                continue                       # a top needs a BEARISH engulf
            if (not is_high) and (not up):
                continue                       # a bottom needs a BULLISH one
            b1_hi, b1_lo = max(c1.open, c1.close), min(c1.open, c1.close)
            b2_hi, b2_lo = max(c2.open, c2.close), min(c2.open, c2.close)
            if not (b2_hi >= b1_hi and b2_lo <= b1_lo and (b2_hi - b2_lo) > 0):
                continue                       # c2 must really engulf c1
            # "whichever is SHORTER" — by full range, wick included
            short = c1 if (c1.high - c1.low) <= (c2.high - c2.low) else c2
            # ...and the zone is that WHOLE candle: "کل کندل بدنه با سایه میشه
            # زون" — body together with its wick, not the body alone. Taking
            # only the body cut the zone off at exactly the end the market
            # reacts from.
            top = short.high if cfg.engulf_full_candle else max(short.open, short.close)
            bot = short.low if cfg.engulf_full_candle else min(short.open, short.close)
            if top - bot <= 0:
                continue
            role = Role.RESISTANCE if is_high else Role.SUPPORT
            away = (hi_run - bot) if role == Role.SUPPORT else (top - lo_run)
            if away < big or self._overlaps(top, bot, htf):
                continue
            # unpaired, so it is born FRESH: it still owes its two touches
            self._add_zone(top, bot, role, atr, idx, htf,
                           src="engulf", valid=False)
            return

        # Image 43: a single MOMENTUM candle is a zone in its own right — "in
        # 80% of cases the market repeats from it". That is the book's own
        # basis for a one-candle zone, and it is the only thing that belongs
        # here besides the engulf. A pivot candle with a small body is neither,
        # and gets nothing drawn on it.
        if cfg.momentum_zones:
            pc = series[p]
            body = abs(pc.close - pc.open)
            if body >= atr * cfg.momentum_body_atr:
                if cfg.momentum_full_candle:
                    top, bot = pc.high, pc.low
                    away = (top - lo_run) if is_high else (hi_run - bot)
                elif is_high:
                    top, bot = pc.high, max(pc.open, pc.close)
                    away = top - lo_run
                else:
                    top, bot = min(pc.open, pc.close), pc.low
                    away = hi_run - bot
                if top > bot and away >= big and not self._overlaps(top, bot, htf):
                    self._add_zone(top, bot,
                                   Role.RESISTANCE if is_high else Role.SUPPORT,
                                   atr, idx, htf, src="mom", valid=False)

    def _reanchor_flipped(self, price: float, is_high: bool, atr: float,
                          idx: int, htf: bool):
        """A broken level only becomes tradable where the PULLBACK turns.

        The captain's 30m chart: an R at 4,433.9 breaks upward, price runs to
        4,530, then pulls back and prints an S at ~4,460. That S — not the old
        4,433.9 band, which price never revisits — is the buy. So when a zone
        flips, it waits here until the first swing of the matching kind prints
        on the correct side of the break, and then MOVES to it.

        The zone keeps its uid and its was_valid flag, so it is still named
        I.VR / RBS exactly as before; only the price it sits at changes."""
        for z in self.zones:
            if z.await_pull < 0 or z.dead or z.htf != htf:
                continue
            if idx <= z.await_pull:
                continue
            # a broken resistance is now support: it wants the pullback LOW
            want_low = z.role == Role.SUPPORT
            if want_low == is_high:
                continue
            # a pullback that cuts back through the old level is a failed
            # break, not a pullback — the false-breakout path handles that
            if want_low and price < z.bot:
                continue
            if (not want_low) and price > z.top:
                continue
            # On the captain's chart the break ran from 4,433.9 to 4,530 BEFORE
            # the pullback printed its S. So the swing we re-anchor to is the
            # turn of a real retracement, not the first twitch after the break:
            # price must have travelled a Big Movement away first.
            if self.cfg.need_big_move:
                run = self.candles[z.await_pull: idx + 1]
                if run:
                    gone = (max(w.high for w in run) - z.top) if want_low \
                        else (z.bot - min(w.low for w in run))
                    if gone < atr * self.cfg.big_move_atr:
                        continue
            half = max(atr * self.cfg.min_zone_atr, 1e-9) / 2.0
            top, bot = price + half, price - half
            # A zone is only ever checked for overlap where it is CREATED, so
            # moving one here could drop it straight on top of a live zone —
            # which is what put two boxes with the same label on top of each
            # other on the 15m and 4h charts. The zone itself does not count
            # as its own obstacle.
            # Two flipped levels whose pullbacks print on the same swing land
            # on EXACTLY the same band — a re-anchor is always price +/- half.
            # The live run showed "RBS 4658.05-4659.59" and
            # "I.VR 4658.05-4659.59" stacked, which reads like a bug.
            #
            # It is only clutter. Two identical zones do NOT double the risk:
            # each carries at most one order, max_open caps the total and the
            # ranking picks between them, so on 45,045 M5 bars there was ONE
            # bar with two resting orders at the same side and price, and four
            # with two open trades. Suppressing the duplicate costs real money
            # (pooled +0.0291 -> +0.0137), so the labels stay.
            #
            # Two boxes can also legitimately sit on one price: the pullback
            # swing that re-anchors a flipped level is itself a pivot, so the
            # ordinary passes may already have drawn their own zone on it. That
            # is the same nesting the book does on purpose, not a collision.
            #
            # Three ways of forbidding it were measured after the stacked
            # labels showed up on the 15m and 4h charts — skip the move, retire
            # the duplicate, let the stronger rank win — and all three cost
            # money (pooled +0.0252 -> +0.0123 / +0.0065 / +0.0073). The
            # stacked boxes were a DRAWING bug in the Pine file, not this: a
            # zone that moved kept its original box top. Fixed there.
            z.top, z.bot = top, bot
            z.born_index = idx
            z.await_pull = -1
            # ...and "pull pull pull" was just noise on the label
            if not z.src.endswith(" pull"):
                z.src = z.src + " pull"

    def _pivot_zones(self, series: List[Candle], n: int, atr: float,
                     idx: int, htf: bool, track_trend: bool,
                     use_close: bool = False):
        """One pivot pass over a candle series. Runs on the chart candles and
        on the aggregated analysis candles, because the book marks zones on
        both (p41).

        With use_close it reads the series as a LINE CHART: only closes, no
        wicks at all. Image 31 tells the reader to use a line chart "to see
        and mark the zones better", and images 12/13 show why — the same chart
        is printed twice, candles and line, and the level sits on the CLOSE
        level while the wicks poke through it (4,737.99 support with wicks to
        4,735; 4,800.51 resistance with wicks to 4,801). A level that price
        keeps CLOSING at is invisible to a high/low pivot when each spike
        stops somewhere different.

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
        if use_close:
            is_ph = all(w.close < pc.close for i, w in enumerate(window) if i != n)
            is_pl = all(w.close > pc.close for i, w in enumerate(window) if i != n)
        else:
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
        if use_close:
            swings = self.swings_line_htf if htf else self.swings_line_ltf
        else:
            swings = self.swings_htf if htf else self.swings_ltf
        big = atr * cfg.big_move_atr
        run = series[p: j + 1]
        # a line chart has no wicks, so its movements are measured close to close
        hi_run = max((w.close if use_close else w.high) for w in run)
        lo_run = min((w.close if use_close else w.low) for w in run)

        for is_high in (True, False):
            if is_high and not is_ph:
                continue
            if not is_high and not is_pl:
                continue
            price = pc.close if use_close else (pc.high if is_high else pc.low)
            sw = Swing(price, is_high, p)
            if cfg.flip_needs_pullback:
                self._reanchor_flipped(price, is_high, atr, idx, htf)
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
            # A GAP mate is the OPPOSITE kind of swing at a DIFFERENT price,
            # so the ordinary same-price mate search above can never find one.
            if cfg.gap_zones and (htf or not cfg.gap_htf_only):
                lo, hi = atr * cfg.gap_min_atr, atr * cfg.gap_max_atr
                gmate = None
                for prev in reversed(swings[-cfg.pair_lookback - 1:-1]):
                    if p - prev.index > cfg.pair_max_gap:
                        continue
                    if prev.is_high != is_high and lo <= abs(prev.price - price) <= hi:
                        gmate = prev
                        break
                if gmate is not None and self._gap_zone(
                        sw, gmate, price, is_high, atr, idx, htf, series[j].close):
                    continue
            if mate is None and use_close:
                # The captain's own 15m line chart, tagged by hand: EVERY swing
                # that makes a peak carries an R and every swing that makes a
                # trough carries an S. No size filter and no higher-high rule —
                # on that chart the second R sits BELOW the first one. So an
                # unpaired line swing is still a level: a plain FRESH S/R,
                # which image 39 will not enter and image 44 will not aim at
                # until it earns its second touch.
                #
                # This matters past the drawing. SBR/RBS are what a PLAIN
                # support or resistance becomes when it breaks, so without
                # these levels the line chart could never produce one.
                #
                # (Until now this branch did not exist at all: a line swing
                # fell through to the engulf/momentum code below and drew its
                # zone from the CANDLE's body — geometry a line chart does not
                # even have.)
                if cfg.line_single_levels:
                    role = Role.RESISTANCE if is_high else Role.SUPPORT
                    if not self._overlaps(price, price, htf):
                        self._add_zone(price, price, role, atr, idx, htf,
                                       src="line R" if is_high else "line S",
                                       valid=False)
                continue

            if mate is None:
                # p24, the FIFTH way to draw a zone: "when there is no S/R pair
                # to draw from, draw it from the ENGULF" — and images 27/28 say
                # exactly where: "at the place where the wicks have befriended,
                # and whichever candle is SHORTER than the one before it".
                #
                # Until v9.6 this branch drew a zone at EVERY unpaired pivot,
                # which is not a rule the book has anywhere: 70% of all zones
                # came out of here. Now it draws only where a real engulf pair
                # sits, and the SHORTER candle of the pair is the band.
                self._engulf_zone(series, p, is_high, atr, idx, htf,
                                  hi_run, lo_run, big)
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
                # the MATE came first, so a resistance mate makes it R+S
                src = "R+S" if mate.is_high else "S+R"
            if self._overlaps(top, bot, htf):
                continue
            # Master class image 36: "the difference from an ordinary support
            # is only that it has a First Movement and a Second Movement — and
            # if EACH of the movements is a BIG MOVEMENT, the market respects
            # that zone MORE." The paired path never checked that, so a zone
            # was born VALID off two swings the market had barely reacted to.
            # A valid zone has a FIRST movement and a SECOND one. On how big
            # they must be, the captain's own words: "if ONE of them, the first
            # or the second, is bigger, it is BETTER" — one, not both, and
            # better, not required. The code used to reject any zone unless
            # BOTH cleared 1.2 ATR, which is stricter than the rule twice over.
            # ...measured close-to-close on a line chart, exactly like hi_run
            # and lo_run above. A line chart has no wicks, so reading .high/.low
            # here was measuring the first movement on a chart the zone was
            # never drawn from — the same mistake the engulf path had.
            seg = series[mate.index: p + 1]
            if role == Role.SUPPORT:
                first = max((w.close if use_close else w.high) for w in seg) - top
            else:
                first = bot - min((w.close if use_close else w.low) for w in seg)
            away = (hi_run - top) if role == Role.SUPPORT else (bot - lo_run)
            if cfg.big_move_rule == "both":
                if first < big or away < big:
                    continue
            elif cfg.big_move_rule == "either":
                if first < big and away < big:
                    continue
            # "off": the movements never reject a zone, they only feed the
            # strength ranking of image 54
            # two touches already define it, so it is born VALID (p35: first
            # movement + second movement). The entry is the RETURN to it.
            if htf and cfg.htf_struct_zone and not use_close:
                top, bot = self._struct_expand(series, (mate.index, p), role,
                                               top, bot, cfg.htf_struct_bars)
            self._add_zone(top, bot, role, atr, idx, htf,
                           src=("line " + src) if use_close else src, valid=True)

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
        # Both sides broken recently = ranging; the book says stand aside.
        # range_bars counts ANALYSIS bars, and idx is a CHART index, so the
        # window is scaled by the multiplier between them — which is 1 when the
        # chart IS the analysis timeframe. Leaving the multiplier in under
        # single_tf made the window three times too wide and, since the trend
        # now updates on every chart bar rather than every analysis bar, held
        # the engine "in range" almost permanently: 571 trades instead of 6352.
        mult = 1 if self.cfg.single_tf else max(1, self.cfg.htf_mult)
        span = self.cfg.range_bars * mult
        self.in_range = (idx - self.last_bos_up) <= span \
            and (idx - self.last_bos_dn) <= span

    # ── targets (book p.44): TP1 from a CHART zone, TP2 from an ANALYSIS
    #    zone, TP3 whatever lies beyond — 1R / 2R / 3R when no zone is there ──
    def _gap_zone(self, sw, mate, price: float, is_high: bool,
                  atr: float, idx: int, htf: bool, close: float):
        """Images 31/33/52 — the GAP is the band BETWEEN a support and a
        resistance sitting at different prices, and the market fills it back
        about 80% of the time. Up-trend GAP is R+S, down-trend is S+R."""
        cfg = self.cfg
        if is_high == mate.is_high:
            return False                       # S+S or R+R, not a gap
        top, bot = max(mate.price, price), min(mate.price, price)
        span = top - bot
        if not (atr * cfg.gap_min_atr <= span <= atr * cfg.gap_max_atr):
            return False
        # Which side the gap is traded from is decided by where price IS, and
        # nothing else: above the band it is support underneath, below it is
        # resistance overhead. While price sits INSIDE the band there is no
        # side yet — images 52/53 both draw the box with price already outside
        # it and wait for the return. (The old test here read
        # `(not is_high and price > mate.price) or (is_high and price > mate.price)`
        # — the same condition twice, so is_high did nothing and a band price
        # was standing in got a side assigned by an accident of which swing
        # printed last.)
        if close > top:
            role = Role.SUPPORT
        elif close < bot:
            role = Role.RESISTANCE
        else:
            return False
        if self._overlaps(top, bot, htf):
            return False
        self._add_zone(top, bot, role, atr, idx, htf, src="GAP", valid=True)
        return True

    def _rank(self, z: "Zone") -> int:
        """Image 54's strength order, lower = stronger.

        On top of that order, a chart zone that sits inside an analysis zone
        gets a bonus. That is the captain's own routine: mark the zones on the
        1-hour, drop to the 15-minute and draw SMALLER zones INSIDE them, then
        go to the 5-minute to confirm and trade. Requiring it outright was
        tried and throws away 64% of the setups without improving the rest —
        because he REFINES a big zone by eye while the engine draws both
        independently and can only check whether they happened to coincide.
        As a preference rather than a gate it costs nothing and puts his
        refined zones at the front of the queue for the open-order slots."""
        base = self._rank_book(z)
        if self.cfg.nested_bonus and not z.htf and self._nested(z):
            return base - 1
        return base

    def _rank_book(self, z: "Zone") -> int:
        inv = z.state == State.INVERTED
        if inv and z.touches == 1 and z.was_valid:
            return 3                                   # PO2 inversion
        if inv and z.touches == 1:
            return 2                                   # PO2 (2nd touch coming)
        if inv:
            return 4                                   # V.S / V.R inversion
        # Image 54 ranks GAP fifth. This used to test for "S+R"/"R+S", which
        # are the names of the PAIRED zones — a real gap carries src "GAP", so
        # the branch never once fired for the thing it was written for, and the
        # paired zones it did catch were being ranked above their own class.
        if "GAP" in z.src or z.src in ("PBP", "DBD"):
            return 5                                   # GAP / continuation base
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

    def _distinct_levels(self, prices: List[float], atr: float) -> int:
        """How many separate LEVELS these broken zones represent.

        Zones drawn by the candle pass, the line pass and the analysis pass sit
        on top of each other, so three zones inverting together is usually one
        level giving way, not three."""
        if not prices:
            return 0
        tol = atr * self.cfg.pair_tol_atr
        kept: List[float] = []
        for p in sorted(prices):
            if not kept or p - kept[-1] > tol:
                kept.append(p)
        return len(kept)

    def _struct_expand(self, series: List[Candle], idxs, role: Role,
                       top: float, bot: float, k: int):
        """Draw an ANALYSIS zone the way the captain draws it: not a hairline
        through two swing prices, but the whole turn those swings sit in.

        His 1-hour boxes cover the base of the reversal — from the wick that
        made the extreme across to the body edge of the candles around it. So
        for each of the two pivots the zone is built from, the band is widened
        to that turn: wick side out to the extreme, body side to the closes.
        That is what leaves room for the 15-minute layer to tighten it."""
        lo, hi = bot, top
        n = len(series)
        for i in idxs:
            a, b = max(0, i - k), min(n - 1, i + k)
            seg = series[a: b + 1]
            if not seg:
                continue
            if role == Role.SUPPORT:
                lo = min(lo, min(w.low for w in seg))
                hi = max(hi, max(max(w.open, w.close) for w in seg))
            else:
                hi = max(hi, max(w.high for w in seg))
                lo = min(lo, min(min(w.open, w.close) for w in seg))
        return (hi, lo) if hi > lo else (top, bot)

    def _refine_from_chart(self, top: float, bot: float, role: Role):
        """The captain's own words, in code:

        «وقتی زون بزرگ در تایم ۱۵ دقیقه مشخص شد و به ۱ دقیقه می‌روی، آن زون
        کوچک‌تر می‌شود چون کندل‌ها کوچک‌ترند. در یک دقیقه آن زونِ ۱۵ دقیقه
        برداشته می‌شود و روی زونی که یک دقیقه مشخص کرده حساب می‌شود.»

        The important part is WHICH candles do the redrawing. It is not price
        coming back later — the 15-minute zone and the 1-minute zone are drawn
        from THE SAME MINUTES, just at a finer resolution. One 15-minute candle
        with a five-dollar lower wick is fifteen 1-minute candles, and the turn
        itself only occupied three of them. So the chart candles that built the
        analysis zone are re-read here, the one holding the extreme is found,
        and the band is drawn around ITS little cluster with the ordinary SNRZ
        geometry — wick side out to the extreme, body side in to the closes.

        The wide band is not thrown away: `raw_top`/`raw_bot` keep it so the
        chart can still outline where the analysis timeframe saw the level.
        But the order, the stop and the touch counting all use the tight one,
        which is what "the 15-minute zone is lifted" means for a trade."""
        cfg = self.cfg
        n = len(self.candles)
        # The analysis pivot needs pivot_htf bars each side to be confirmed, so
        # the turn it marks lies inside that many analysis bars of chart candles.
        span = max(1, cfg.htf_mult) * (2 * cfg.pivot_htf + 1)
        lo_i = max(0, n - span)
        best, best_px = -1, None
        for i in range(lo_i, n):
            c = self.candles[i]
            if c.high < bot or c.low > top:            # never visited the band
                continue
            px = max(bot, min(top, c.low if role == Role.SUPPORT else c.high))
            if best_px is None or (px < best_px if role == Role.SUPPORT
                                   else px > best_px):
                best, best_px = i, px
        if best < 0:
            return top, bot

        k = cfg.htf_struct_bars
        seg = self.candles[max(0, best - k): min(n, best + k + 1)]
        if role == Role.SUPPORT:
            nb = max(bot, min(w.low for w in seg))
            nt = min(top, max(max(w.open, w.close) for w in seg))
        else:
            nt = min(top, max(w.high for w in seg))
            nb = max(bot, min(min(w.open, w.close) for w in seg))

        atr = self._atr() or 0.0
        mn = atr * cfg.min_zone_atr
        if nt - nb < mn:                 # still a zone, not a hair line
            mid = (nt + nb) / 2.0
            nt, nb = min(top, mid + mn / 2.0), max(bot, mid - mn / 2.0)
        if nt <= nb or nt - nb >= top - bot:
            return top, bot              # no tighter than it already was
        return nt, nb

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
        if cfg.max_sl_atr > 0 and risk > atr * cfg.max_sl_atr:
            return None          # the wick is too far away — no setup, no trade
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
        # Every target is a zone that is ALREADY on the chart and has not been
        # broken. When there is no second or third such zone, there is no TP2
        # or TP3 — the whole position comes off at TP1. The old code put a
        # target at 2x and 3x the TP1 distance, which is a price the chart
        # never named.
        tp2 = (htf[0] if (htf and cfg.tp2_analysis_tf)
               else (rest[0] if rest else tp1))
        beyond = [x for x in rest if (x > tp2 if is_buy else x < tp2)]
        tp3 = beyond[0] if beyond else tp2
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
                    p.exit_px = p.sl
                elif entry_bar:
                    pass
                elif c.high >= p.tp3:
                    p.stat, p.peak, p.closed = 3, 3, True
                    p.exit_px = p.tp3
                elif c.high >= p.tp2 and p.stat < 2:
                    p.stat = p.peak = 2
                elif c.high >= p.tp1 and p.stat < 1:
                    p.stat = p.peak = 1
            else:
                if c.high >= p.sl:
                    p.stat, p.closed = (-2 if p.be else -1), True
                    p.exit_px = p.sl
                elif entry_bar:
                    pass
                elif c.low <= p.tp3:
                    p.stat, p.peak, p.closed = 3, 3, True
                    p.exit_px = p.tp3
                elif c.low <= p.tp2 and p.stat < 2:
                    p.stat = p.peak = 2
                elif c.low <= p.tp1 and p.stat < 1:
                    p.stat = p.peak = 1
            # image 41: the red 1:1 line is where the stop goes to entry and
            # money comes off — not the first zone target
            if self.cfg.break_even and not p.closed and not p.be and not entry_bar:
                r1 = p.entry + p.risk0 * self.cfg.be_at_r if p.side == "buy" \
                    else p.entry - p.risk0 * self.cfg.be_at_r
                reached = (c.high >= r1) if p.side == "buy" else (c.low <= r1)
                if reached or p.stat >= 1:
                    p.sl, p.be = p.entry, True
            if not p.closed and idx - p.index > self.cfg.max_trade_bars:
                # ran out of bars: whatever is still on comes off HERE, at the
                # market. Recording the price is the whole point — without it
                # the scoring had to invent an exit for these.
                p.closed, p.exit_px = True, c.close
            if self.cfg.kill_on_stop and p.closed and p.stat == -1:
                for z in self.zones:
                    if z.uid == p.uid:
                        z.dead = True
        if len(self.trades) > 400:
            # Keep every trade that could still be closing on this bar. The old
            # line dropped ALL closed trades, so a trade that finished on the
            # very bar the list overflowed was gone before anything downstream
            # could read it — it never reached the backtester's tally or a live
            # runner's log. A trade cannot outlive max_trade_bars, so anything
            # older than that has certainly been seen already.
            keep_from = idx - self.cfg.max_trade_bars - 1
            self.trades = [t for t in self.trades
                           if not t.closed or t.index >= keep_from]

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
        # single_tf: the chart marks its OWN zones, confirms on itself and
        # trades on itself — so the trend has to come from the chart's own
        # structure too, which is what track_trend does here.
        one = self.cfg.single_tf
        self._pivot_zones(self.candles, self.cfg.pivot_ltf, atr, idx,
                          htf=False, track_trend=one)
        if self.cfg.line_zones:
            self._pivot_zones(self.candles, self.cfg.pivot_ltf, atr, idx,
                              htf=False, track_trend=False, use_close=True)
        if one:
            self._update_trend(c, atr, idx)
        elif self._push_htf(c):
            atr_h = self._htf_atr()
            if atr_h and atr_h > 0:
                self._pivot_zones(self.htf_candles, self.cfg.pivot_htf, atr_h,
                                  idx, htf=True, track_trend=True)
                if self.cfg.line_zones:
                    self._pivot_zones(self.htf_candles, self.cfg.pivot_htf,
                                      atr_h, idx, htf=True, track_trend=False,
                                      use_close=True)
                self._update_trend(self.htf_candles[-1], atr_h, idx)
        self._fill_orders(c, idx)
        self._update_trades(c, idx)
        # A zone dies when the market BREAKS it — not when a clock runs out and
        # not because price walked away from it. Both of those were my own
        # inventions: the book never retires a level by age, and a level far
        # overhead is exactly what a running trade is aiming AT. `far` is now
        # only a hint for the drawing code, so a distant zone stops cluttering
        # the chart while still being available as a target.
        atr_h = self._htf_atr() or atr
        kept: List[Zone] = []
        for z in self.zones:
            ref = atr_h if z.htf else atr
            life = self.cfg.life_htf * max(1, self.cfg.htf_mult) if z.htf else self.cfg.life_ltf
            gap = (c.close - z.top if c.close > z.top else
                   z.bot - c.close if c.close < z.bot else 0.0)
            z.far = gap > ref * self.cfg.max_zone_dist_atr
            if self.cfg.expire_by_age and idx - z.born_index > life:
                continue
            if self.cfg.drop_far_zones and z.far:
                continue
            kept.append(z)
        self.zones = kept
        # The book's confirmation list asks for "a small Break of Structure in
        # the trade direction". It is NOT applied here, and the two config
        # knobs that claimed to apply it are gone — they were computed into two
        # locals that nothing ever read, so "no micro-BOS" and "micro-BOS over
        # 4 bars" printed byte-identical backtests to the default.
        #
        # Wiring it in was measured rather than assumed: it throws away 55% of
        # the setups (6450 -> 2935) and takes the median from +0.066 to -0.004.
        # That fits the model — the rule is a confirmation candle for entering
        # AT MARKET once price reaches the zone, and this engine rests a LIMIT
        # on the zone before price gets there, so the fill IS the entry. There
        # is no candle left to confirm.
        cfg = self.cfg
        # p25: "it MUST break TWO supports; if it breaks more, that is no
        # problem" — so what is counted is LEVELS, not bars. These were plain
        # booleans, which meant one impulse through three resistances credited
        # a support with a single break and an SRR the book would have named
        # needed a second impulse to appear.
        #
        # The prices are collected rather than just counted, because the same
        # real level is often carried by more than one zone at once — the
        # candle pass, the line pass and the analysis pass each draw their own,
        # and _overlaps only keeps them apart WITHIN a set. Counting raw
        # inversions would let one level break three times.
        broke_support: List[float] = []
        broke_resistance: List[float] = []
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
                        z.await_pull = idx if cfg.flip_needs_pullback else -1
                        z.touches = z.sig_touch = 0
                        z.srr = z.fba = False
                        z.false_breaks = 0
                        z.flips += 1
                        z.dead = z.flips >= cfg.max_flips
                        z.pend_bar, z.pend_dir = -1, 0
                        broke_support.append(z.bot)
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
                    # await_pull >= 0 means the level broke but the pullback
                    # has not printed its swing yet, so there is nothing to
                    # trade at any price. The old band is not the level.
                    tradable = (not z.dead) and z.pend_dir == 0 \
                        and z.await_pull < 0 and (
                        (z.state == State.VALID and z.touches >= need) or
                        # SRR/RSS: "a support that has broken TWO resistances,
                        # and when it pulls back to it we place the order on
                        # it". The order is armed while price is AWAY from the
                        # zone, so the pullback IS the fill — demanding a touch
                        # first threw away exactly the pullback the captain's
                        # chart marks with an arrow, and waited for a second.
                        z.srr or
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
                    if tradable and ok_trend and can_fire \
                            and not self._has_order_or_trade(z.uid) \
                            and c.close > z.top:      # price is ABOVE the zone
                        lv = self._levels(True, z, c, atr)
                        if lv is not None:
                            cand.append((self._rank(z) if cfg.rank_setups else 0,
                                         z.uid, "buy", z.kind,
                                         # image 55: PO2 is the SECOND touch of
                                         # an inversion zone. The order is armed
                                         # while price is away, so the fill IS
                                         # that second touch — which means the
                                         # zone must have exactly ONE touch now.
                                         z.state == State.INVERTED and z.touches == 1, lv))
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
                        z.await_pull = idx if cfg.flip_needs_pullback else -1
                        z.touches = z.sig_touch = 0
                        z.srr = z.fba = False
                        z.false_breaks = 0
                        z.flips += 1
                        z.dead = z.flips >= cfg.max_flips
                        z.pend_bar, z.pend_dir = -1, 0
                        broke_resistance.append(z.top)
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
                    # await_pull >= 0 means the level broke but the pullback
                    # has not printed its swing yet, so there is nothing to
                    # trade at any price. The old band is not the level.
                    tradable = (not z.dead) and z.pend_dir == 0 \
                        and z.await_pull < 0 and (
                        (z.state == State.VALID and z.touches >= need) or
                        # SRR/RSS: "a support that has broken TWO resistances,
                        # and when it pulls back to it we place the order on
                        # it". The order is armed while price is AWAY from the
                        # zone, so the pullback IS the fill — demanding a touch
                        # first threw away exactly the pullback the captain's
                        # chart marks with an arrow, and waited for a second.
                        z.srr or
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
                    if tradable and ok_trend and can_fire \
                            and not self._has_order_or_trade(z.uid) \
                            and c.close < z.bot:      # price is BELOW the zone
                        lv = self._levels(False, z, c, atr)
                        if lv is not None:
                            cand.append((self._rank(z) if cfg.rank_setups else 0,
                                         z.uid, "sell", z.kind,
                                         # image 55: PO2 is the SECOND touch of
                                         # an inversion zone. The order is armed
                                         # while price is away, so the fill IS
                                         # that second touch — which means the
                                         # zone must have exactly ONE touch now.
                                         z.state == State.INVERTED and z.touches == 1, lv))
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
            n_res = self._distinct_levels(broke_resistance, atr)
            n_sup = self._distinct_levels(broke_support, atr)
            for z in self.zones:
                if z.state == State.INVERTED or z.dead:
                    continue
                if n_res and z.role == Role.SUPPORT and c.close > z.top:
                    z.opp_breaks += n_res
                    if z.opp_breaks >= 2:
                        z.srr = True
                if n_sup and z.role == Role.RESISTANCE and c.close < z.bot:
                    z.opp_breaks += n_sup
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
