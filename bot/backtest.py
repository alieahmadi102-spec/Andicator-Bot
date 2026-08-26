"""
SNRZ backtester — replays candles through snrz_core and reports how the setups
actually resolved, so a rule change can be judged on numbers instead of on the
look of one screenshot.

    python bot/backtest.py candles.csv                 # time,open,high,low,close
    python bot/backtest.py candles.csv --spread 0.14   # charge the real spread
    python bot/backtest.py --synthetic                 # random-walk sanity run

Outcomes follow the indicator: TP1/TP2/TP3 reached, stopped out, or closed at
break-even after TP1 paid 1:1 (the book's Zero Float rule).
"""
from __future__ import annotations

import csv
import random
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Iterable, List

from snrz_core import Candle, Config, SnrzEngine


@dataclass
class Report:
    signals: int = 0
    tp1: int = 0
    tp2: int = 0
    tp3: int = 0
    stopped: int = 0
    breakeven: int = 0
    timeout: int = 0                   # ran out of bars without reaching either
    open_at_end: int = 0
    r_total: float = 0.0               # realized R, from each trade's own levels

    @property
    def resolved(self) -> int:
        # Timed-out trades used to be added to r_total and left OUT of this
        # count, so they moved the average without paying for a slot in it.
        return (self.tp1 + self.tp2 + self.tp3 + self.stopped
                + self.breakeven + self.timeout)

    @property
    def win_rate(self) -> float:
        """A setup that reached at least TP1 paid for itself — including the
        ones that came back to the break-even stop afterwards, since half the
        position was already booked at 1R by then."""
        wins = self.tp1 + self.tp2 + self.tp3 + self.breakeven
        return 100.0 * wins / self.resolved if self.resolved else 0.0

    @property
    def expectancy_r(self) -> float:
        """Average R under the book's own exit plan: bank half at TP1, a
        quarter at TP2, a quarter at TP3, and move the stop to entry once TP1
        has paid. Every trade is scored from ITS OWN entry/SL/TP prices, so
        changing where TP1 sits changes the payout honestly — a nominal
        "+0.5R for reaching TP1" would silently keep crediting 1R even after
        TP1 was moved to a quarter of that."""
        return self.r_total / self.resolved if self.resolved else 0.0

    def line(self, label: str) -> str:
        # "hit a target" is the number the user actually watches -- the
        # break-even trades count as wins in win_rate because half the position
        # was banked at 1R under the book's plan, but on an account that cannot
        # split, a break-even trade pays nothing. Both are shown.
        hit = self.tp1 + self.tp2 + self.tp3
        green = 100.0 * hit / self.resolved if self.resolved else 0.0
        return (f"{label:<28} n={self.resolved:<5} "
                f"TP={hit:<4} BE={self.breakeven:<4} SL={self.stopped:<4} "
                f"TO={self.timeout:<3} "
                f"green={green:5.1f}%  E={self.expectancy_r:+.3f}R")


def realized_r(p, spread: float = 0.0) -> float:
    """What one finished setup actually paid, in units of its own initial risk.

    The book's plan (image 41): money comes off at the 1:1 line and the stop
    goes to entry there, then the rest rides to the zone targets — a quarter
    at TP2 and a quarter at TP3. Whatever is STILL on when the trade ends comes
    off at the price it actually ended at.

    That last clause is why p.exit_px exists. Before it, a trade that simply
    ran out of bars without reaching anything was scored as though half of it
    had come off at the 1:1 line — a flat +0.5R for a trade that went nowhere.
    On M5 that was 37 trades silently worth +18R, and the same trades were
    missing from the denominator, so the error was counted twice.

    `spread` is the broker's bid/ask gap in price units. A round trip crosses
    it once — in at the ask, out at the bid for a buy — so the whole position
    pays it once, at entry, whatever the trade later does. In R that is
    spread / risk, which is why the same 14-cent spread is nothing on an H4
    stop and a large bite on an M1 one."""
    risk = p.risk0            # break-even overwrites p.sl, so never derive it
    if risk <= 0:
        return 0.0
    cost = spread / risk if spread > 0 else 0.0
    sign = 1.0 if p.side == "buy" else -1.0

    def r(px: float) -> float:
        return sign * (px - p.entry) / risk

    if p.stat == -1:
        # stopped before the 1:1 line, so nothing had been banked yet
        return -1.0 - cost

    paid = 0.0
    left = 1.0
    # Image 41: at the red 1:1 line half comes off and the stop goes to entry.
    # p.be records that that actually happened; p.peak records how far the
    # trade got, which p.stat forgets once a later exit overwrites it.
    if p.peak >= 1 or p.be:
        paid += 0.5 * (r(p.tp1) if p.peak >= 1 else 1.0)
        left -= 0.5
    if p.peak >= 2:
        paid += 0.25 * r(p.tp2)
        left -= 0.25
    if p.peak >= 3:
        paid += 0.25 * r(p.tp3)
        left -= 0.25
    if left > 1e-9:
        exit_px = p.exit_px if p.exit_px else (p.entry if p.be else p.sl)
        paid += left * r(exit_px)
    return paid - cost


TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30,
              "H1": 60, "H4": 240, "Daily": 1440, "D1": 1440}


def minutes_from_name(path: str) -> int:
    """XAUUSDM15.csv -> 15, so the analysis timeframe can follow the captain's
    ladder instead of a fixed multiplier."""
    stem = path.replace("\\", "/").split("/")[-1].rsplit(".", 1)[0]
    for tag in sorted(TF_MINUTES, key=len, reverse=True):
        if stem.endswith(tag):
            return TF_MINUTES[tag]
    return 0


def run(candles: Iterable[Candle], cfg: Config, spread: float = 0.0) -> Report:
    eng = SnrzEngine(cfg)
    rep = Report()
    counted: set[int] = set()          # trades already tallied, by (uid, bar)
    for c in candles:
        rep.signals += len(eng.on_candle(c))
        for p in eng.trades:
            key = (p.uid, p.index)
            if not p.closed or key in counted:
                continue
            counted.add(key)
            rep.r_total += realized_r(p, spread)
            if p.stat == 3:
                rep.tp3 += 1
            elif p.stat == 2:
                rep.tp2 += 1
            elif p.stat == 1:
                rep.tp1 += 1
            elif p.stat == -2:
                rep.breakeven += 1
            elif p.stat == -1:
                rep.stopped += 1
            else:
                rep.timeout += 1
    rep.open_at_end = sum(1 for t in eng.trades if not t.closed)
    return rep


# Everything in this project was measured in-sample until now: the same
# candles were used to choose a rule and to judge it, across dozens of
# experiments on one dataset. That is how a backtest flatters itself, and the
# live account is out-of-sample. So the data is split once, here, and the last
# slice is never used to choose anything.
SPLIT_AT = 0.70


def split_rows(rows: List[Candle], which: str) -> List[Candle]:
    """"train" = the first 70%, where rules are discovered and chosen.
    "test"  = the last 30%, touched only to confirm what training picked.
    "all"   = everything, which is what every earlier measurement did."""
    if which == "all":
        return rows
    cut = int(len(rows) * SPLIT_AT)
    return rows[:cut] if which == "train" else rows[cut:]


def read_csv(path: str) -> List[Candle]:
    """Reads both TradingView exports (a header row with named columns) and
    MetaTrader 5 exports (UTF-16, no header, date[ time],O,H,L,C,vol,spread)."""
    raw = open(path, "rb").read()
    for enc in ("utf-16", "utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f"cannot decode {path}")
    lines = [ln for ln in text.replace("\r", "").split("\n") if ln.strip()]

    first = lines[0].lower()
    if "open" in first and "high" in first:                    # TradingView
        rows = csv.DictReader(lines)
        return [Candle(int(float(r["time"])), float(r["open"]), float(r["high"]),
                       float(r["low"]), float(r["close"])) for r in rows]

    out: List[Candle] = []                                     # MetaTrader 5
    for i, ln in enumerate(lines):
        f = ln.split(",")
        if len(f) < 5:
            continue
        try:
            o, h, lo, cl = (float(f[1]), float(f[2]), float(f[3]), float(f[4]))
        except ValueError:
            continue                                           # skip a header
        # The real timestamp was being thrown away and the ROW INDEX used as
        # the candle time. That is harmless for zone logic, which only ever
        # compares bars to each other -- and it silently makes any question
        # about the clock unanswerable, which is why hour-of-day had never
        # been tested. "2026.01.02 01:05" parses fine.
        t = i
        try:
            d, hm = f[0].strip().split()
            y, mo, dy = (int(x) for x in d.replace("-", ".").split("."))
            hh, mm = (int(x) for x in hm.split(":")[:2])
            t = int(datetime(y, mo, dy, hh, mm,
                             tzinfo=timezone.utc).timestamp())
        except Exception:
            pass                       # no parsable stamp: fall back to the row
        out.append(Candle(t, o, h, lo, cl))
    if not out:
        raise SystemExit(f"no candles parsed from {path}")
    return out


def synthetic(seed: int, n: int = 3000) -> List[Candle]:
    """Trending and ranging phases, so both the trend filter and the range
    lockout get exercised. Not real data — only good for relative comparison."""
    rnd = random.Random(seed)
    out: List[Candle] = []
    price = 2000.0
    for i in range(n):
        phase = (i // 250) % 3
        drift = 1.1 if phase == 0 else (-0.9 if phase == 1 else 0.0)
        price += drift + rnd.uniform(-4, 4)
        o = price
        c = price + rnd.uniform(-3, 3)
        h = max(o, c) + abs(rnd.uniform(0, 2.5))
        lo = min(o, c) - abs(rnd.uniform(0, 2.5))
        out.append(Candle(i, o, h, lo, c))
    return out


def total(reports: List[Report]) -> Report:
    t = Report()
    for r in reports:
        t.signals += r.signals
        t.tp1 += r.tp1
        t.tp2 += r.tp2
        t.tp3 += r.tp3
        t.stopped += r.stopped
        t.breakeven += r.breakeven
        t.timeout += r.timeout
        t.open_at_end += r.open_at_end
        t.r_total += r.r_total
    return t


def main() -> None:
    args = sys.argv[1:]
    # --spread 0.14 charges the broker's real bid/ask gap to every trade. The
    # MT5 CSV export writes 0 in its spread column, so the number has to come
    # from Market Watch on the live account, not from the file.
    spread = 0.0
    if "--spread" in args:
        i = args.index("--spread")
        spread = float(args[i + 1])
        del args[i:i + 2]
    which = "all"
    if "--split" in args:
        i = args.index("--split")
        which = args[i + 1]
        if which not in ("train", "test", "all"):
            raise SystemExit("--split takes train, test or all")
        del args[i:i + 2]

    if args and args[0] != "--synthetic":
        rows = split_rows(read_csv(args[0]), which)
        cfg = Config(chart_minutes=minutes_from_name(args[0]))
        label = f"{args[0]} [{which}]" + (f" sp{spread:g}" if spread else "")
        print(run(rows, cfg, spread).line(label))
        return

    sets = [
        ("everything on (default)", Config()),
        ("no analysis-zone refine", Config(refine_htf=False)),
        ("whole-candle momentum zone", Config(momentum_full_candle=True)),
        ("no break-even", Config(break_even=False)),
        ("keep stopped-out zones", Config(kill_on_stop=False)),
    ]
    books = [synthetic(s) for s in range(1, 13)]
    for label, cfg in sets:
        print(total([run(b, cfg, spread) for b in books]).line(label))
    print("\nNOTE: this is a random walk — it has no real support or resistance,")
    print("so an S/R strategy cannot show an edge on it. Use it to check that a")
    print("rule change does not starve the strategy, and export real XAUUSD")
    print("candles to a CSV to judge whether the rules actually make money.")


if __name__ == "__main__":
    main()
