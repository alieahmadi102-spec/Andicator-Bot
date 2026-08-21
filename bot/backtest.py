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
        has paid. Scoring a TP1-then-break-even trade as 0R would understate
        it — half the position was already booked at 1R."""
        if not self.resolved:
            return 0.0
        total = (self.tp1 * 0.5            # half at 1R, rest ran back to entry
                 + self.tp2 * 1.0          # 0.5*1R + 0.25*2R
                 + self.tp3 * 1.75         # 0.5*1R + 0.25*2R + 0.25*3R
                 + self.breakeven * 0.5    # reached TP1, then came back
                 - self.stopped)
        return total / self.resolved

    def line(self, label: str) -> str:
        return (f"{label:<28} signals={self.signals:<4} "
                f"TP1={self.tp1:<3} TP2={self.tp2:<3} TP3={self.tp3:<3} "
                f"BE={self.breakeven:<3} SL={self.stopped:<3} "
                f"win={self.win_rate:5.1f}%  E={self.expectancy_r:+.2f}R")


def run(candles: Iterable[Candle], cfg: Config) -> Report:
    eng = SnrzEngine(cfg)
    rep = Report()
    counted: set[int] = set()          # positions already tallied, by open index
    for c in candles:
        rep.signals += len(eng.on_candle(c))
        p = eng.position
        if p is None or not p.closed or p.index in counted:
            continue
        counted.add(p.index)
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
    if eng.position is not None and not eng.position.closed:
        rep.open_at_end += 1
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
    return t


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] != "--synthetic":
        rows = read_csv(args[0])
        print(run(rows, Config()).line(args[0]))
        return

    sets = [
        ("everything on (default)", Config()),
        ("no micro-BOS", Config(need_micro_bos=False)),
        ("micro-BOS over 4 bars", Config(micro_bos_len=4)),
        ("no break-even", Config(break_even=False)),
        ("keep stopped-out zones", Config(kill_on_stop=False)),
        ("no rejection close", Config(need_reject=False)),
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
