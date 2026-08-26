r"""
Draw what the engine ACTUALLY sees — candles, with every zone it drew, on a
real PNG. Same snrz_core the bot and the backtest use, so what comes out of
here is the ground truth, not a description of it.

    python bot/chart.py data/XAUUSDM5.csv                 # last 200 candles
    python bot/chart.py data/XAUUSDM5.csv --bars 400
    python bot/chart.py data/XAUUSDM5.csv --at 2026-08-26 21:00
    python bot/chart.py data/XAUUSDM5.csv --hidden         # fresh zones too

Every box is labelled with its name and its exact price range, and a tick
marks the candle it was drawn from — so a zone that is in the wrong place can
be pointed at by price and time instead of described.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from snrz_core import Candle, Config, SnrzEngine, State, Role
from backtest import read_csv, minutes_from_name

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

# One colour per zone NAME, matching the Pine indicator so the two can be
# compared side by side without translating.
COLOURS = {
    "I.PO2": "#ff9100", "PO2": "#ffc400",
    "I.VR": "#7c4dff", "I.VS": "#d500f9",
    "RBS": "#00b8d4", "SBR": "#ff6d00",
    "SRR": "#00e676", "RSS": "#ff1744",
    "V.S": "#089981", "V.R": "#f23645",
    "S": "#4caf50", "R": "#ef5350",
}


def name_of(z) -> str:
    """The bare zone name, without the touch/FBA suffixes Zone.kind adds."""
    return z.kind.split()[0]


def draw(path: str, bars: int, at: str | None, show_hidden: bool,
         out: str) -> str:
    rows = read_csv(path)
    tf = minutes_from_name(path)
    eng = SnrzEngine(Config(chart_minutes=tf))

    stop = len(rows)
    if at:
        want = int(datetime.strptime(at, "%Y-%m-%d %H:%M")
                   .replace(tzinfo=timezone.utc).timestamp())
        # the last candle at or before the requested time
        stop = max((i for i, c in enumerate(rows) if c.time <= want),
                   default=len(rows) - 1) + 1

    for c in rows[:stop]:
        eng.on_candle(c)

    lo_i = max(0, stop - bars)
    view = rows[lo_i:stop]
    if not view:
        raise SystemExit("no candles in that window")

    fig, ax = plt.subplots(figsize=(19, 10), dpi=110)
    fig.patch.set_facecolor("#131722")
    ax.set_facecolor("#131722")

    # candles
    for k, c in enumerate(view):
        up = c.close >= c.open
        col = "#26a69a" if up else "#ef5350"
        ax.plot([k, k], [c.low, c.high], color=col, linewidth=0.8, zorder=2)
        ax.add_patch(Rectangle((k - 0.32, min(c.open, c.close)), 0.64,
                               max(abs(c.close - c.open), 1e-9),
                               facecolor=col, edgecolor=col, zorder=3))

    def hidden(z) -> bool:
        return z.far or (z.state == State.FRESH and not z.srr and not z.fba)

    # The visible price window, decided by the CANDLES. A zone far outside it
    # is still a live target for the engine, but drawing its label at its own
    # price drags the axes out to reach it and squashes the candles into a
    # strip at the top -- so anything that does not reach the window is left
    # off the picture and counted instead.
    p_lo = min(c.low for c in view)
    p_hi = max(c.high for c in view)
    pad = (p_hi - p_lo) * 0.06
    lo_lim, hi_lim = p_lo - pad, p_hi + pad

    right = len(view) - 1
    drawn = 0
    offscreen = 0
    for z in eng.zones:
        if z.dead or (hidden(z) and not show_hidden):
            continue
        if z.bot > hi_lim or z.top < lo_lim:
            offscreen += 1
            continue
        nm = name_of(z)
        col = COLOURS.get(nm, "#9e9e9e")
        left = max(0, z.origin_index - lo_i)
        if left > right:
            continue
        drawn += 1
        ax.add_patch(Rectangle((left, z.bot), right - left + 6,
                               max(z.top - z.bot, 1e-9),
                               facecolor=col, alpha=0.22,
                               edgecolor=col, linewidth=1.1, zorder=1))
        # the origin tick: which candle this zone was drawn from
        ax.plot([left, left], [z.bot, z.top], color=col, linewidth=3.5,
                zorder=4)
        ax.text(right + 6.4, (z.top + z.bot) / 2,
                f" {z.kind}  {z.bot:.2f}–{z.top:.2f}",
                color=col, fontsize=9, va="center", family="monospace")

    ax.set_xlim(-1, len(view) + 34)
    ax.set_ylim(lo_lim, hi_lim)

    step = max(1, len(view) // 12)
    ticks = list(range(0, len(view), step))
    ax.set_xticks(ticks)
    ax.set_xticklabels(
        [datetime.fromtimestamp(view[i].time, timezone.utc).strftime("%d %b\n%H:%M")
         for i in ticks], color="#b2b5be", fontsize=8)
    ax.tick_params(colors="#b2b5be")
    for s in ax.spines.values():
        s.set_color("#2a2e39")
    ax.grid(color="#1e222d", linewidth=0.6)
    ax.yaxis.tick_right()

    last = view[-1]
    when = datetime.fromtimestamp(last.time, timezone.utc)
    trend = ("UPTREND" if eng.trend_up else
             "DOWNTREND" if eng.trend_down else "trend unclear")
    ax.set_title(
        f"SNRZ — {path.split('/')[-1]}   ·   M{tf}   ·   "
        f"{when:%Y-%m-%d %H:%M} UTC   ·   {trend}   ·   "
        f"{drawn} zones drawn"
        + (f"  ·  {offscreen} off-screen" if offscreen else "")
        + ("  ·  fresh ones included" if show_hidden else ""),
        color="#d4af37", fontsize=12, pad=14)

    fig.tight_layout()
    fig.savefig(out, facecolor=fig.get_facecolor())
    return out


def main() -> None:
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)

    def take(flag, default=None):
        if flag in args:
            i = args.index(flag)
            v = args[i + 1]
            del args[i:i + 2]
            return v
        return default

    show_hidden = "--hidden" in args
    if show_hidden:
        args.remove("--hidden")
    bars = int(take("--bars", "200"))
    at = take("--at")
    if at and len(args) > 1 and ":" in args[1]:
        at = at + " " + args.pop(1)          # "--at 2026-08-26 21:00"
    out = take("--out", "chart.png")
    print(draw(args[0], bars, at, show_hidden, out))


if __name__ == "__main__":
    main()
