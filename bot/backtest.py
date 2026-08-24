"""
SNRZ backtester — replays candles through snrz_core and reports how the setups
actually resolved, so a rule change can be judged on numbers instead of on the
look of one screenshot.

    python bot/backtest.py candles.csv          # columns: time,open,high,low,close
    python bot/backtest.py --synthetic          # built-in random-walk sanity run

Outcomes follow the indicator: TP1/TP2/TP3 reached, stopped out, or closed at
break-even after TP1 paid 1:1 (the book's Zero Float rule).
"""
from __future__ import annotations

import csv
import random
import sys
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
    open_at_end: int = 0
    r_total: float = 0.0               # realized R, from each trade's own levels

    @property
    def resolved(self) -> int:
        return self.tp1 + self.tp2 + self.tp3 + self.stopped + self.breakeven

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
        return (f"{label:<28} signals={self.signals:<4} "
                f"TP1={self.tp1:<3} TP2={self.tp2:<3} TP3={self.tp3:<3} "
                f"BE={self.breakeven:<3} SL={self.stopped:<3} "
                f"win={self.win_rate:5.1f}%  E={self.expectancy_r:+.2f}R")


def realized_r(p) -> float:
    """What one finished setup actually paid, in units of its own initial risk.

    The book's plan (image 41): money comes off at the 1:1 line and the stop
    goes to entry there, then the rest rides to the zone targets — a quarter
    at TP2 and a quarter at TP3."""
    risk = p.risk0            # break-even overwrites p.sl, so never derive it
    if risk <= 0:
        return 0.0
    sign = 1.0 if p.side == "buy" else -1.0
    r1 = sign * (p.tp1 - p.entry) / risk
    r2 = sign * (p.tp2 - p.entry) / risk
    r3 = sign * (p.tp3 - p.entry) / risk
    if p.stat == -1:
        return -1.0                          # stopped before the 1:1 line
    # Image 41: at the 1:1 red line money comes off and the stop goes to
    # entry. p.peak is how far the trade actually got before it ended, which
    # p.stat forgets once the break-even stop closes it.
    paid = 0.5 * (r1 if p.peak >= 1 else 1.0)     # the first half
    if p.peak >= 2:
        paid += 0.25 * r2
    if p.peak >= 3:
        paid += 0.25 * r3
    return paid


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


def run(candles: Iterable[Candle], cfg: Config) -> Report:
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
            rep.r_total += realized_r(p)
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
    rep.open_at_end = sum(1 for t in eng.trades if not t.closed)
    return rep


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
            out.append(Candle(i, float(f[1]), float(f[2]), float(f[3]), float(f[4])))
        except ValueError:
            continue                                           # skip a header
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
        t.open_at_end += r.open_at_end
        t.r_total += r.r_total
    return t


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] != "--synthetic":
        rows = read_csv(args[0])
        cfg = Config(chart_minutes=minutes_from_name(args[0]))
        print(run(rows, cfg).line(args[0]))
        return

    sets = [
        ("everything on (default)", Config()),
        ("no micro-BOS", Config(need_micro_bos=False)),
        ("micro-BOS over 4 bars", Config(micro_bos_len=4)),
        ("no break-even", Config(break_even=False)),
        ("keep stopped-out zones", Config(kill_on_stop=False)),
    ]
    books = [synthetic(s) for s in range(1, 13)]
    for label, cfg in sets:
        print(total([run(b, cfg) for b in books]).line(label))
    print("\nNOTE: this is a random walk — it has no real support or resistance,")
    print("so an S/R strategy cannot show an edge on it. Use it to check that a")
    print("rule change does not starve the strategy, and export real XAUUSD")
    print("candles to a CSV to judge whether the rules actually make money.")


if __name__ == "__main__":
    main()
