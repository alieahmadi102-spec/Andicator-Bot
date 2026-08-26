"""
MT5 live runner (skeleton) — connects the SNRZ engine to MetaTrader 5.

Requirements (Windows, or Linux+Wine):
    pip install MetaTrader5

هشدار: این یک اسکلت اولیه است. قبل از پول واقعی، حتماً روی حساب دمو تست کن.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:  # keeps the repo importable on non-Windows dev machines
    mt5 = None

from snrz_core import Candle, Config, Signal, SnrzEngine

SYMBOL = "XAUUSD"
# The CHART timeframe — the one the bot watches and trades on. The ANALYSIS
# timeframe is derived from it by the captain's ladder (two rungs up, middle
# rung skipped), so 1 -> 15, 5 -> 30, 15 -> 60, 30 -> 240, 60 -> D, 240 -> W.
# This used to be described as the analysis timeframe, which it stopped being
# when the ladder went in — and the startup banner still repeated that, printing
# "analysis TF 1m" on a run whose zones were marked on 15m.
TIMEFRAME_MIN = 60
RISK_PCT = 1.0              # max 1% risk per trade (book rule)
# The spread must not eat more than this share of a trade's risk. The account's
# own Market Watch shows 14 (bid 4646.88 / ask 4647.02 at 2 digits = 14 cents),
# so 0.10 asks for a stop of at least $1.40 in normal conditions. The point of
# the guard is the abnormal ones: this broker quotes a FLOATING spread, and the
# news-time widening is what turns a small stop into a losing trade before the
# market has moved at all.
MAX_SPREAD_R = 0.10
# Every order this bot sends is stamped with a magic number, and that stamp is
# how it tells its own trades from everything else on the account. It used to
# be ONE number for every timeframe -- which is fine for one bot and wrong the
# moment two run together, as five did here (1m, 5m, 15m, 30m, 1h). Each would
# have seen the other four's orders as its own: moving their stops, skipping
# its own order as a duplicate of theirs, and reporting their results as its.
# The timeframe is added so each bot owns exactly its own trades.
MAGIC_BASE = 20260819
MAGIC = MAGIC_BASE          # read_args() adds the timeframe
# How long a resting limit is given to fill. This used to be its own number
# here -- 40, against the engine's 10 -- and the two disagreeing is worse than
# either value: the engine dropped its order after 10 bars while the broker
# kept it for 40, so the engine re-armed the same zone into an order that was
# still sitting there. It is read from the engine's own config now, so there is
# one value and it cannot drift again.
ORDER_EXPIRY_BARS = Config().order_expiry_bars

# Nothing is sent to the broker while this is True. Turn it off only after you
# have watched it print signals for a while ON A DEMO ACCOUNT and you agree
# with them. The crypto runner defaults the same way.
DRY_RUN = True

# "Trend is King" is the book's rule and it is the single biggest reason a
# setup does not fire -- 31% of the times price stood in a zone and nothing
# happened. Turning it off with --no-trend gives roughly FOUR times as many
# trades at slightly lower quality: on 83 days the median went +0.024 to
# +0.002, M1 from -0.059 to +0.001, M5 from +0.104 to +0.075.
NO_TREND = False

# How the trade is entered, mirrored from the engine's Config so place() does
# not have to infer it from where price happens to be at send time.
#
#   "market"  open the position straight away, at the price the signal bar
#             closed at. Measured better PER TRADE on real XAUUSD -- M5
#             +0.206 -> +0.242R, M15 +0.082 -> +0.201R, and H1 goes from
#             LOSING (-0.070R) to +0.199R. A resting limit only ever fills
#             when price comes back to the zone, and a good part of the time
#             it comes back because the zone is failing -- so the limit is
#             selected into the losers while the setups that worked
#             immediately never fill at all.
#
#   "limit"   the book's own way. Fewer R per trade but MORE trades, because
#             the entry sits on the zone and the stop is only the zone's own
#             height -- so more setups fit under a small account's risk
#             ceiling. Over the whole run that still totalled more.
ENTRY_MODE = "market"

# The ceiling on what ONE trade may risk when the broker's minimum lot leaves
# no room to scale down. Below some balance 0.01 lots is the only position
# there is, so the choice is take it at whatever it risks or take nothing.
# Measured on a $114 account over 83 days: a 2% ceiling took 13 trades, 3% took
# 75 and finished flat with a 23% drawdown, 5% took 617 and lost a quarter of
# the account with a 75% drawdown. 3 is the last value that is not simply a
# faster way down.
# 0 = no ceiling, which is the default: the bot does not gate on the balance.
# --risk-cap N restores the old behaviour of refusing a trade whose forced
# risk exceeds N% of the account.
MAX_RISK_PCT = 0.0

# How often the loop wakes. The stop and the target sit AT the broker and are
# enforced tick by tick regardless -- but the break-even move and the ratchet
# are decided here, so a slow loop leaves a trade riskier than the plan for as
# long as it sleeps. This was 5 seconds.
#
# Entries deliberately stay on the CLOSED bar. Zones, ATR and touch counts are
# all built from finished candles; deciding from half a candle would be trading
# something that was never measured.
POLL_SECONDS = 1.0
REPORT_ONLY = False
# Where profit is taken, as a multiple of the stop. --tp N moves it.
#
# This is the scalping dial, and it was measured across both halves of both
# timeframes. Taking profit sooner buys winners and sells money, cleanly and
# monotonically -- every column moves the same way:
#
#             M1 train      M1 test      M5 train      M5 test     green
#   0.75R   -0.046       -0.142       -0.038       +0.075       53-63%
#   1.0R    -0.051       -0.103       -0.035       +0.079       49-55%
#   1.5R    +0.038       +0.005       +0.085       +0.199       35-40%
#   2.0R    +0.089       +0.036       +0.150       +0.281       25-33%
#   3.0R    +0.145       +0.096       +0.257       +0.367       17-23%
#
# So a 58% win rate is available and it LOSES money: at 0.75R you win 58% of
# 0.75R and lose 42% of a full R, which is +0.015R before the spread and
# negative after it. The edge of this strategy is in letting a winner reach
# 3R, and scalping inverts exactly that.
#
# 2.0R is the fastest setting that is still positive in all four columns --
# about half again as many winning trades as 3R. Below 2.0R, M1 goes
# negative on data it was not fitted on.
TP_MULT = 0.0        # 0 = use the engine's default (3.0)

# Why setups do not become orders, counted.
#
# Every reason this file declines to send already printed a line and scrolled
# away, so "it found setups all day and opened nothing" was a question that
# could only be answered by hunting back through the console -- and by then the
# lines were gone. The engine's own side is measurable here (13.8 setups a day
# on M1 in simulation), so when the live bot sends none the cause is one of
# these, and it should take one line to see which.
REFUSED: dict = {}
SEEN = {"setups": 0, "sent": 0}


def refuse(reason: str, detail: str = ""):
    """Record a refusal and print it. The tally is what survives."""
    REFUSED[reason] = REFUSED.get(reason, 0) + 1
    if detail:
        print(f"  SKIPPED - {detail}")


def ledger_line() -> str:
    """One line: what the engine offered, what went out, what ate the rest."""
    if not SEEN["setups"]:
        return ""
    bits = " · ".join(f"{k} {v}" for k, v in
                      sorted(REFUSED.items(), key=lambda kv: -kv[1]))
    return (f"since start: {SEEN['setups']} setups · {SEEN['sent']} sent"
            + (f" · refused: {bits}" if bits else ""))


def mt5_timeframe(minutes: int):
    """MT5 has no TIMEFRAME_M60 — anything from an hour up has its own name."""
    named = {60: "H1", 120: "H2", 180: "H3", 240: "H4", 360: "H6",
             480: "H8", 720: "H12", 1440: "D1", 10080: "W1", 43200: "MN1"}
    key = named.get(minutes)
    attr = f"TIMEFRAME_{key}" if key else f"TIMEFRAME_M{minutes}"
    tf = getattr(mt5, attr, None)
    if tf is None:
        raise SystemExit(f"unsupported timeframe: {minutes} minutes")
    return tf


def lots_for_risk(symbol: str, sl_distance: float, risk_pct: float):
    """Size this trade for this account, automatically.

    Two numbers, not one:

      RISK_PCT      what to risk when there is room to choose. Sizing scales
                    the lot down as the stop gets wider, so this is honoured
                    exactly whenever the arithmetic allows it.
      MAX_RISK_PCT  the ceiling. Below some balance the broker's minimum lot
                    already risks more than the target and there is nothing to
                    scale down to -- 0.01 lots is 0.01 lots. The trade is taken
                    anyway while the forced risk stays under this, and refused
                    once it does not.

    That is what makes it adapt on its own. A $10 account and a $500 account
    run the same file: the big one sizes to the target, the small one takes the
    tight-stop setups its minimum lot can afford and passes on the rest. No
    knob to guess, and no silent 47%-of-the-account trade either, which is what
    the very first version did by quietly falling back to the minimum lot."""
    info = mt5.symbol_info(symbol)
    acc = mt5.account_info()
    if info is None or acc is None:
        return None, "the terminal gave no symbol or account information"
    tick_value = info.trade_tick_value
    tick_size = info.trade_tick_size
    if sl_distance <= 0 or tick_value <= 0 or tick_size <= 0:
        return None, "cannot size the trade (broker gave no tick value)"

    loss_per_lot = sl_distance / tick_size * tick_value
    target = acc.balance * risk_pct / 100.0
    steps = math.floor((target / loss_per_lot) / info.volume_step)
    lots = round(steps * info.volume_step, 8)

    if lots >= info.volume_min:
        lots = min(lots, info.volume_max)
        return lots, lots * loss_per_lot

    # No room to scale: the smallest position the broker sells is the only one,
    # and it is taken.
    #
    # There used to be a ceiling here -- refuse the trade when the minimum
    # lot's forced risk went past MAX_RISK_PCT. On a $56 account that ceiling
    # meant M5 could not place a single trade out of 580 and M1 was down to
    # its tightest quarter, so the bot sat there declining everything. Asked
    # about it, the account owner's answer was that the bot should not be
    # gating on the balance at all, and this is that decision.
    #
    # The risk does not go away, it becomes VISIBLE instead of blocking: the
    # caller prints what share of the account each trade puts at stake, and
    # marks it when that is large. --risk-cap N puts the old ceiling back.
    floor_lots = info.volume_min
    floor_risk = floor_lots * loss_per_lot
    floor_pct = 100.0 * floor_risk / acc.balance if acc.balance > 0 else 999.0
    if MAX_RISK_PCT > 0 and floor_pct > MAX_RISK_PCT:
        return None, (
            f"would risk {floor_pct:.1f}% and --risk-cap is set to "
            f"{MAX_RISK_PCT:.1f}% — the smallest lot ({floor_lots}) risks "
            f"${floor_risk:.2f} on a ${sl_distance:.2f} stop.")
    return floor_lots, floor_risk


def cheapest_lot_risk(symbol: str):
    """What the SMALLEST position this symbol sells loses per $1 of stop.

    This one number decides whether a balance can trade a symbol at all. On a
    standard XAUUSD contract (100 ounces) the minimum 0.01 lots is 1 ounce, so
    it is $1.00 -- a $3 stop costs $3 whether you like it or not. A broker's
    micro gold (contract 10) makes the same stop cost $0.30, and a cent-sized
    contract less again."""
    info = mt5.symbol_info(symbol)
    if info is None or info.trade_tick_size <= 0 or info.volume_min <= 0:
        return None
    return info.volume_min * info.trade_tick_value / info.trade_tick_size


def affordable_symbols(balance: float, need_stop: float, like: str = "XAU"):
    """Every symbol on this account the balance could actually trade, cheapest
    first.

    A small balance on standard gold does not take "fewer trades" -- it takes
    NONE, because the minimum lot's risk is fixed and the strategy's stops are
    what they are. That is arithmetic and no rule change reaches it. What CAN
    reach it is a smaller contract, and brokers very often list one right next
    to the standard symbol (XAUUSD.m, XAUUSDm, XAUUSD.micro, GOLDmicro). So
    rather than telling someone their account is too small, this looks."""
    out = []
    for s in (mt5.symbols_get() or []):
        name = s.name
        if like.lower() not in name.lower():
            continue
        if not mt5.symbol_select(name, True):
            continue
        per = cheapest_lot_risk(name)
        if not per or per <= 0:
            continue
        forced = per * need_stop
        out.append((forced, name, per, 100.0 * forced / balance if balance > 0 else 999.0))
    out.sort()
    return out


# What this strategy's stop actually measures, per timeframe, on real XAUUSD:
# the median setup's stop distance in dollars. Sizing is not a matter of
# opinion once these are known -- the minimum lot's risk is the stop distance
# times the per-dollar cost, and either the balance covers it or it does not.
MEDIAN_STOP = {1: 1.75, 5: 5.03, 15: 9.12, 30: 12.75, 60: 20.35, 240: 35.86}


def what_fits(symbol: str, tf_minutes: int) -> str:
    """What one trade will actually put at stake on this account.

    This used to explain which setups the balance would REFUSE. It refuses
    nothing now, so the job changed: say plainly what the smallest position
    the broker sells costs when it loses, because on a small account that is
    a large share of it and the number should be in front of you before the
    first order goes out, not discovered afterwards."""
    acc = mt5.account_info()
    per = cheapest_lot_risk(symbol)
    info = mt5.symbol_info(symbol)
    if acc is None or per is None or info is None:
        return ""
    typical = MEDIAN_STOP.get(tf_minutes, 5.0)
    forced = per * typical
    pct = 100.0 * forced / acc.balance if acc.balance > 0 else 999.0
    target_lots = (acc.balance * RISK_PCT / 100.0) / (per / info.volume_min)

    lines = [f"sizing: aiming for {RISK_PCT:g}% a trade, and the balance is "
             f"never a reason to skip one",
             f"        smallest lot {info.volume_min} loses ${per:.2f} for "
             f"every $1 the stop is wide",
             f"        a typical {tf_minutes}m setup stops ${typical:.2f} away "
             f"= ${forced:.2f} = {pct:.1f}% of ${acc.balance:.2f}"]
    if target_lots >= info.volume_min:
        lines.append(f"        -> there is room to size: about "
                     f"{target_lots:.2f} lots on the typical setup")
    else:
        lines.append(f"        -> no room to size down. Every trade goes out "
                     f"at the minimum lot")
        if pct >= 5.0:
            lines.append(f"           and risks about {pct:.0f}% of the "
                         f"account EACH. Roughly half of all trades hit their")
            lines.append(f"           stop, so a normal run of losses is a "
                         f"large part of this balance.")
            lines.append(f"           --risk-cap 3 makes it skip those "
                         f"instead.")
    return "\n".join(lines)

def _ladder_path() -> str:
    """One file per bot. The magic carries the timeframe, so five bots running
    together keep five ladders instead of overwriting one."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        f".snrz_ladder_{MAGIC}.json")


def ladder_load():
    """The rungs have to survive a restart.

    A position carries only its own SL and TP. When one undivided position is
    aiming at TP3, the two prices its stop is meant to climb to exist nowhere
    but here -- so a restart used to leave a running trade managed as a plain
    break-even, silently trading a different plan from the measured one."""
    global LADDER
    try:
        with open(_ladder_path()) as f:
            LADDER = {k: tuple(v) for k, v in json.load(f).items()}
    except Exception:
        LADDER = {}


def ladder_save():
    try:
        with open(_ladder_path(), "w") as f:
            json.dump({k: list(v) for k, v in LADDER.items()}, f)
    except Exception:
        pass                      # a lost ladder degrades to break-even, not worse


def split_volume(volume: float, info):
    """Image 41's exit: half off at TP1, a quarter at TP2, a quarter at TP3.

    A broker position cannot be closed in pieces by its own TP, so the plan is
    three separate orders at one price, each with its own target. Every leg has
    to be a whole number of volume steps AND clear the minimum lot, which takes
    at least four steps to divide -- below that there is nothing to split and
    the caller falls back to the ratchet.

    The remainder goes to the FIRST leg rather than being rounded away, so the
    three legs always add back up to exactly `volume`. Rounding each leg on its
    own turned 0.05 lots into 0.02+0.01+0.01 and quietly traded 0.04. Giving it
    to the first leg is also the safe direction to be wrong in: TP1 is the
    target that actually gets reached, TP3 the one that mostly does not."""
    step = getattr(info, "volume_step", 0.0)
    if step <= 0:
        return None
    steps = int(round(volume / step))
    if steps < 4:                            # nothing to halve and quarter
        return None
    q2 = q3 = max(1, steps // 4)
    half = steps - q2 - q3
    if half < max(q2, q3):
        return None
    legs = [round(n * step, 8) for n in (half, q2, q3)]
    if any(l < info.volume_min - 1e-12 for l in legs):
        return None
    return legs


def _filling(info, pending: bool = False):
    """Which fill policy this symbol accepts.

    symbol_info.filling_mode is a BITMASK of what the broker allows, and
    order_send is refused outright with "Unsupported filling mode" when asked
    for one that is not in it. A PENDING order is filled when price arrives, so
    RETURN is the only policy that means anything for it and is what brokers
    take for pendings whatever the mask says; a market order has to follow the
    mask."""
    if pending:
        return mt5.ORDER_FILLING_RETURN
    mask = getattr(info, "filling_mode", 0)
    if mask & getattr(mt5, "SYMBOL_FILLING_FOK", 1):
        return mt5.ORDER_FILLING_FOK
    if mask & getattr(mt5, "SYMBOL_FILLING_IOC", 2):
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def _retcode(res):
    """None when the order went through, otherwise a sentence saying why not.

    res is None whenever the terminal refused the request before it ever left
    the machine -- and reading res.retcode there raises AttributeError, which
    buries the actual reason where nobody sees it. The reason in that case is
    in mt5.last_error(), so that is what gets printed."""
    if res is None:
        return f"the terminal rejected the request outright: {mt5.last_error()}"
    ok = {getattr(mt5, n) for n in ("TRADE_RETCODE_DONE", "TRADE_RETCODE_PLACED",
                                    "TRADE_RETCODE_DONE_PARTIAL")
          if hasattr(mt5, n)}
    if res.retcode in ok:
        return None
    said = {
        "TRADE_RETCODE_NO_MONEY": "not enough free margin for this size",
        "TRADE_RETCODE_INVALID_VOLUME": "the volume is outside what this symbol allows",
        "TRADE_RETCODE_INVALID_PRICE": "that price is not valid for this order type",
        "TRADE_RETCODE_INVALID_STOPS": "the SL or TP is inside the broker's minimum distance",
        "TRADE_RETCODE_INVALID_FILL": "this symbol does not accept that filling mode",
        "TRADE_RETCODE_INVALID_EXPIRATION": "this symbol does not accept an expiry time",
        "TRADE_RETCODE_TRADE_DISABLED": "trading is disabled for this symbol or account",
        "TRADE_RETCODE_MARKET_CLOSED": "the market is closed",
        "TRADE_RETCODE_REQUOTE": "requote — price moved while the order was travelling",
        "TRADE_RETCODE_PRICE_OFF": "no quote to trade against right now",
        "TRADE_RETCODE_TOO_MANY_REQUESTS": "too many requests, the terminal is throttling",
    }
    known = {getattr(mt5, n): why for n, why in said.items() if hasattr(mt5, n)}
    why = known.get(res.retcode) or getattr(res, "comment", "") or "no reason given"
    return f"retcode {res.retcode}: {why}"


def _fill_matches(broker_px: float, t, info) -> bool:
    """Is this broker position the engine's trade `t`?

    A resting limit fills at exactly the price it was placed at, so an exact
    comparison was fine while every entry was a limit. A MARKET order does not:
    it is filled at the live ask (or bid), which is already the spread away
    from the close the engine priced the setup at, plus whatever the market
    moved in between. Matching those to the point would have called every
    single market entry a phantom, and reconcile would have closed the engine's
    side of a position that was really open -- then let the zone arm a second
    one on top of it.

    So the tolerance is a share of the trade's own risk: wide enough for a fill
    that slipped, far narrower than the gap between two different setups."""
    tol = max(getattr(info, "point", 0.0) * 1.5, 0.25 * t.risk0)
    return abs(broker_px - t.entry) <= tol


def _same_price(a: float, b: float, info) -> bool:
    """Is this the same price, as far as the broker is concerned?

    The engine's entry is a raw zone edge -- 4650.3847 -- and the broker stores
    what it was sent, rounded to the symbol's digits: 4650.38. Comparing those
    with a 1e-6 tolerance says "different", which is what produced the three
    identical orders on one price: sync_orders looked for its order at the
    broker, the rounded copy did not match, so it decided the send had never
    happened and sent it again. Every bar. One point of tolerance ends it."""
    tol = max(getattr(info, "point", 0.0), 1e-9) * 1.5
    return abs(a - b) <= tol


def rank_candidates(sigs, symbol: str):
    """Score every setup on this bar and hand them back strongest first.

    The runner used to walk the signals in whatever order the zone loop
    produced them and try to send each one, so a bar with three setups printed
    three SKIPPED lines and took none -- while one of the three may well have
    fitted. Nothing was ever CHOSEN.

    What "strongest" means here is measured, not assumed. The book's own
    strength order (image 54) was checked against 3632 real trades and does not
    predict the outcome at all: correlation -0.003, and its weakest class
    ("fresh S/R", +0.094R) actually beat its "VALID S/R" class (+0.037R). So it
    is kept only as a tie-break.

    What DOES decide things on a small account is the forced risk. Where the
    balance leaves room to size, every setup risks the same target percentage
    and this ties -- the book order then picks. Where the minimum lot is the
    only position available, the setup with the tighter stop risks less of the
    account for the same R, so it is strictly the better trade to spend a slot
    on. Returns (fits, risk_pct, signal, volume, why_not) tuples."""
    acc = mt5.account_info()
    bal = acc.balance if acc else 0.0
    out = []
    for s in sigs:
        sl_d = abs(s.price - s.sl)
        vol, info = lots_for_risk(symbol, sl_d, RISK_PCT)
        if vol is None:
            out.append((False, 999.0, s, None, info))
            continue
        pct = 100.0 * info / bal if bal > 0 else 999.0
        out.append((True, pct, s, vol, ""))
    out.sort(key=lambda t: (not t[0], t[1], t[2].rank))
    return out


def choose_and_place(sigs, symbol: str, tf_minutes: int, room: int):
    """Take the strongest setups this bar, up to the slots that are free."""
    if not sigs:
        return
    SEEN["setups"] += len(sigs)
    ranked = rank_candidates(sigs, symbol)
    fits = [r for r in ranked if r[0]]
    print(f"  {len(sigs)} setup(s) on this bar · {len(fits)} the account can "
          f"take · {room} slot(s) free")
    for n, (ok, pct, s, vol, why) in enumerate(ranked):
        sl_d = abs(s.price - s.sl)
        rr = abs(s.tp1 - s.price) / sl_d if sl_d > 0 else 0.0
        head = f"   {'>' if ok and n < room else ' '} {s.side.upper():4s} {s.zone:5s} @ {s.price:9.2f}"
        if ok:
            # a double-digit share of the account on one trade must never
            # scroll past looking like an ordinary number
            flag = "  !! " if pct >= 5.0 else " · "
            print(f"{head}  stop ${sl_d:.2f} ={flag}{pct:.1f}% of the account"
                  f" · {rr:.1f}R to TP1 · {vol} lots"
                  + ("   <- taking this one" if n < room else
                     "   (waiting: no slot)"))
        else:
            print(f"{head}  stop ${sl_d:.2f} · {rr:.1f}R to TP1 · CANNOT SIZE: {why}")
    taken = 0
    for ok, pct, s, vol, why in ranked:
        # Both of these used to drop the setup in silence, which is precisely
        # the hole this ledger exists to close: the tally has to add up to the
        # number of setups the engine produced, or it is not an answer.
        if taken >= room:
            refuse("no free slot (one trade at a time)")
            continue
        if not ok:
            refuse("too big for the balance")
            continue
        if not DRY_RUN:
            resting, _ = broker_state(symbol)
            info = mt5.symbol_info(symbol)
            if info and any(_same_price(o.price_open, s.price, info)
                            for o in resting):
                print("  already resting at the broker — not duplicated")
                continue
        place(s, symbol, tf_minutes)
        taken += 1
    if not fits and MAX_RISK_PCT > 0:
        print(f"  nothing taken: every setup on this bar risks more than the "
              f"--risk-cap {MAX_RISK_PCT:g}% you asked for.")


def bal_cap(symbol: str) -> float:
    """The widest stop this balance can carry at the minimum lot."""
    acc = mt5.account_info()
    per = cheapest_lot_risk(symbol)
    if acc is None or not per:
        return 0.0
    return acc.balance * MAX_RISK_PCT / 100.0 / per


def place(signal, symbol: str, tf_minutes: int):
    """Book p41/p42: the entry is a LIMIT order resting AT the zone, not a
    market order. signal.price is that zone price -- the engine and the
    backtester both assume the fill happens there, so a market order here would
    make the live bot trade something the numbers never measured."""
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None:
        refuse("no quote", f"the terminal has no quote for {symbol}")
        return
    digits = info.digits
    rnd = lambda p: round(p, digits)

    market = tick.ask if signal.side == "buy" else tick.bid
    sl_distance = abs(signal.price - signal.sl)

    # The spread is paid once, at entry, so in R it is spread / stop distance.
    # Measured on 83 days of XAUUSD at this account's own 14 cents, that is
    # 0.3% of the risk on H4 and 6.5% on M1 -- the same spread costs twenty
    # times more on the fast chart. The live spread FLOATS, so it is read here
    # rather than assumed, and the trade is refused when it eats too much.
    spread = max(0.0, tick.ask - tick.bid)
    spread_r = spread / sl_distance if sl_distance > 0 else 1.0
    if spread_r > MAX_SPREAD_R:
        refuse("spread too wide",
               f"spread is {spread:.{digits}f}, which is "
               f"{100 * spread_r:.0f}% of the {sl_distance:.{digits}f} stop "
               f"(limit {100 * MAX_SPREAD_R:.0f}%).")
        return

    # The broker refuses a stop or target closer to the price than its stops
    # level. Their spec showed 0, but a broker can change it without notice.
    stops = getattr(info, "trade_stops_level", 0) * info.point
    if stops > 0 and sl_distance < stops:
        refuse("stop too close to price",
               f"the {sl_distance:.{digits}f} stop is inside the "
               f"broker's minimum distance of {stops:.{digits}f}")
        return

    volume, why = lots_for_risk(symbol, sl_distance, RISK_PCT)
    if volume is None:
        # already counted in choose_and_place when it ranked this setup
        print(f"  SKIPPED - {why}")
        return

    # Can the account actually CARRY this position? Risk and margin are two
    # different limits and only risk was being checked. A $104 account holding
    # two 0.01-lot gold positions has about $12 of free margin left, and gold
    # wants roughly $46 per 0.01 lot at 1:100 -- so the third send came back
    # "retcode 10019: not enough free margin for this size" after the engine
    # had already opened its own side of the trade. Asking first turns a failed
    # order into a clean, explained skip.
    need = mt5.order_calc_margin(
        mt5.ORDER_TYPE_BUY if signal.side == "buy" else mt5.ORDER_TYPE_SELL,
        symbol, volume, market)
    acc = mt5.account_info()
    if need is not None and acc is not None and need > acc.margin_free:
        # The BROKER's limit, not a risk rule of this bot -- without free
        # margin the order comes back retcode 10019 whether or not we ask
        # first. Asking turns a failed send into a clean, explained skip.
        refuse("no free margin (broker limit)",
               f"the broker needs ${need:.2f} of margin for {volume} lots and "
               f"only ${acc.margin_free:.2f} is free. This is the broker's "
               f"limit, not a risk setting — it would reject the order anyway.")
        return

    legs = split_volume(volume, info)
    ratchet = legs is None
    if ratchet:
        # The account cannot divide its position, so the book's half/quarter/
        # quarter exit is out of reach. Refusing outright leaves nothing at
        # all; the best plan a SINGLE position can run is the ratchet -- one
        # order aimed at TP3 with the stop climbing behind each target as it is
        # passed. Measured on 83 days of M5: +0.035R, against -0.078R for a
        # plain single exit at TP1.
        legs = [volume]
        print(f"  spread {spread:.{digits}f} = {100 * spread_r:.1f}% of the "
              f"stop | one position of {volume} lots: this account cannot "
              f"split, so the stop ratchets behind the targets instead")
    else:
        print(f"  spread {spread:.{digits}f} = {100 * spread_r:.1f}% of the "
              f"stop | {volume} lots -> {legs[0]} @TP1 + {legs[1]} @TP2 + "
              f"{legs[2]} @TP3")
    expiry = datetime.now(timezone.utc) + timedelta(
        minutes=tf_minutes * ORDER_EXPIRY_BARS)

    # In market mode the engine already priced the setup AT the signal bar's
    # close, so there is nothing to rest and nothing to wait for -- sending a
    # limit here would be trading a different plan from the measured one.
    if ENTRY_MODE == "market":
        kind = mt5.ORDER_TYPE_BUY if signal.side == "buy" else mt5.ORDER_TYPE_SELL
    elif signal.side == "buy":
        kind = mt5.ORDER_TYPE_BUY_LIMIT if market > signal.price else mt5.ORDER_TYPE_BUY
    else:
        kind = mt5.ORDER_TYPE_SELL_LIMIT if market < signal.price else mt5.ORDER_TYPE_SELL
    pending = kind in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_SELL_LIMIT)
    # Price is already past the zone -- so the limit would fill instantly and a
    # market order is the same trade. Only while it is still NEAR the zone,
    # though: sync_orders retries a failed send every bar, and without this a
    # setup from an hour ago could be entered at market a long way past its
    # price, on a stop that was measured for the zone edge. A quarter of the
    # risk is the most that entry may be worse by.
    if not pending:
        slip = abs(market - signal.price)
        if slip > 0.25 * sl_distance:
            refuse("price ran away",
                   f"price is already {slip:.{digits}f} past the "
                   f"{rnd(signal.price)} entry, more than a quarter of the "
                   f"{sl_distance:.{digits}f} risk. Chasing it is a different trade.")
            return
    # A single position aims at the far target and ratchets its way there.
    targets = [signal.tp3] if ratchet else [signal.tp1, signal.tp2, signal.tp3]

    if DRY_RUN:
        for vol, tp, n in zip(legs, targets, (3,) if ratchet else (1, 2, 3)):
            print(f"  DRY_RUN - would place {signal.side.upper()}"
                  f"{' LIMIT' if pending else ' (market)'} {vol} {symbol} "
                  f"@ {rnd(signal.price)}  SL {rnd(signal.sl)}  TP{n} {rnd(tp)}")
        return

    sent = 0
    for vol, tp, n in zip(legs, targets, (3,) if ratchet else (1, 2, 3)):
        req = {
            "action": mt5.TRADE_ACTION_PENDING if pending else mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": vol,
            "type": kind,
            "price": rnd(signal.price if pending else market),
            "sl": rnd(signal.sl),
            "tp": rnd(tp),
            "magic": MAGIC,
            "comment": f"SNRZ {signal.zone} T{n}"[:31],
            "type_filling": _filling(info, pending),
        }
        if pending:
            req["type_time"] = mt5.ORDER_TIME_SPECIFIED
            req["expiration"] = expiry
        else:
            req["deviation"] = 20
            req["type_time"] = mt5.ORDER_TIME_GTC
        res = mt5.order_send(req)
        bad = _retcode(res)
        # Not every broker takes a timed pending order. Refusing the trade over
        # the EXPIRY would be throwing away the setup for a housekeeping
        # detail, so it is re-sent as good-till-cancelled -- the engine expires
        # its own copy on schedule either way, and reconcile() cancels the one
        # at the broker with it.
        if bad and pending and "expir" in bad.lower():
            req["type_time"] = mt5.ORDER_TIME_GTC
            req.pop("expiration", None)
            res = mt5.order_send(req)
            bad = _retcode(res)
        if bad:
            REFUSED[f"broker rejected: {bad.split(':')[0]}"] = \
                REFUSED.get(f"broker rejected: {bad.split(':')[0]}", 0) + 1
            print(f"  TP{n} leg FAILED - {bad}")
            continue
        sent += 1
        print(f"  TP{n} leg placed: {vol} lots, ticket {res.order}")
        if ratchet:
            # manage_open_positions needs the two targets under TP3 to ratchet
            # against, and a position carries only its own sl/tp. Keyed by the
            # ORDER ticket: when a pending order triggers, the position it
            # opens carries that same ticket, so this survives a fill at a
            # price that is not exactly the one requested. The price key is
            # kept beside it as a fallback for a market entry.
            rungs = [rnd(signal.tp1), rnd(signal.tp2)]
            LADDER[str(res.order)] = rungs
            LADDER[f"{signal.side}@{rnd(signal.price)}"] = rungs
    if sent:
        SEEN["sent"] += 1        # one SETUP, however many legs it took
        ladder_save()


def manage_open_positions(symbol: str):
    """Image 41: at the 1:1 line the stop goes to entry and the trade is free.

    In the backtest that happens in software. Live, nothing moves the stop
    unless something asks the broker to -- and the measured numbers assume it
    happens, so without this the live bot is not running the plan that was
    measured. Every position this bot opened is checked each bar and its stop
    pulled to entry once price has paid 1R."""
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None:
        return
    for p in positions:
        if p.magic != MAGIC or p.sl == 0.0:
            continue
        risk = abs(p.price_open - p.sl)
        if risk <= 0:
            continue
        is_buy = p.type == mt5.POSITION_TYPE_BUY
        entry = round(p.price_open, info.digits)
        side = "buy" if is_buy else "sell"
        px = tick.bid if is_buy else tick.ask

        # Where the stop is allowed to be now. Break-even once 1R is paid, and
        # for a position that could not be split, one rung higher for every
        # target price has already passed -- the ratchet.
        want = None
        if (px >= entry + risk) if is_buy else (px <= entry - risk):
            want = entry
        rungs = LADDER.get(str(p.ticket)) or LADDER.get(f"{side}@{entry}")
        if rungs:
            tp1, tp2 = rungs
            for reached, lock in (((px >= tp2) if is_buy else (px <= tp2), tp1),
                                  ((px >= tp1) if is_buy else (px <= tp1), entry)):
                if reached:
                    if want is None or (lock > want if is_buy else lock < want):
                        want = lock
                    break
        if want is None:
            continue
        better = want > p.sl if is_buy else want < p.sl
        if not better:
            continue
        res = mt5.order_send({
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": p.ticket,
            "sl": round(want, info.digits),
            "tp": p.tp,
        })
        bad = _retcode(res)
        tag = "BREAK-EVEN" if abs(want - entry) < 1e-9 else "RATCHET"
        print(f"  {tag} {p.ticket}: stop -> {round(want, info.digits)}"
              + (f"  FAILED - {bad}" if bad else ""))


def broker_state(symbol: str):
    """What the BROKER thinks is going on, which after a restart is the only
    truth. The engine rebuilds its own orders from history and would happily
    place a second set on top of the ones already resting."""
    orders = [o for o in (mt5.orders_get(symbol=symbol) or []) if o.magic == MAGIC]
    positions = [p for p in (mt5.positions_get(symbol=symbol) or []) if p.magic == MAGIC]
    return orders, positions


def family_exposure(symbol: str):
    """What every SNRZ bot on this account is holding, not just this one.

    Each timeframe now has its own magic and manages only its own trades, which
    stops them fighting -- but it does not stop the RISK adding up. Five bots
    each willing to hold three positions at 1% is fifteen positions and 15% of
    the account at once. That has to be visible."""
    lo, hi = MAGIC_BASE, MAGIC_BASE + 10080
    pos = [p for p in (mt5.positions_get(symbol=symbol) or [])
           if lo <= p.magic <= hi]
    others = [p for p in pos if p.magic != MAGIC]
    return pos, others


def bot_ledger(symbol: str, days: int = 30):
    """Every deal THIS bot has closed, and what they came to.

    Without this a balance that moved is a mystery: the account also carries
    whatever was traded by hand, and swap, and anything another EA did. The
    magic number is the only thing that separates them. If this prints "0
    deals" then the bot has not touched the balance, whatever it has done."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    deals = mt5.history_deals_get(since, datetime.now(timezone.utc)) or []
    mine = [d for d in deals if d.magic == MAGIC and d.symbol == symbol]
    closing = [d for d in mine if d.entry == mt5.DEAL_ENTRY_OUT]
    pnl = sum(d.profit + d.swap + d.commission for d in closing)
    return mine, closing, pnl


def report(symbol: str, days: int = 30):
    """What the bot's OWN trades actually did, scored the way the backtest is.

    The History tab answers "did the balance move" but not "is this working":
    it mixes every timeframe together, counts money rather than R, and says
    nothing about whether the result is inside the range the measurements
    predict or outside it. This reads the same records, splits them by the
    magic number (so each timeframe is judged separately and hand trades are
    excluded), converts every result to R using the stop the order actually
    went out with, and says plainly whether the run is consistent with the
    backtest or genuinely worse.

    Run it any time:   python mt5_runner.py --report
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    now = datetime.now(timezone.utc)
    deals = mt5.history_deals_get(since, now) or []
    orders = mt5.history_orders_get(since, now) or []
    info = mt5.symbol_info(symbol)
    if info is None:
        print("no symbol info"); return
    tick_v, tick_s = info.trade_tick_value, info.trade_tick_size

    # the stop each position went out with, keyed by the position it opened
    sl_of, vol_of = {}, {}
    for o in orders:
        pid = getattr(o, "position_id", 0)
        if pid and getattr(o, "sl", 0.0):
            sl_of.setdefault(pid, o.sl)
            vol_of.setdefault(pid, getattr(o, "volume_initial", 0.01))

    # opening and closing deal of each position
    opens, closes = {}, {}
    for d in deals:
        if d.symbol != symbol or not (MAGIC_BASE <= d.magic <= MAGIC_BASE + 10080):
            continue
        if d.entry == mt5.DEAL_ENTRY_IN:
            opens[d.position_id] = d
        elif d.entry == mt5.DEAL_ENTRY_OUT:
            closes[d.position_id] = d

    by_tf: dict = {}
    no_stop = 0
    for pid, c in closes.items():
        o = opens.get(pid)
        if o is None:
            continue
        tf = c.magic - MAGIC_BASE
        net = c.profit + c.swap + c.commission
        sl = sl_of.get(pid)
        if not sl:
            no_stop += 1
            r = None
        else:
            risk = abs(o.price - sl) / tick_s * tick_v * (vol_of.get(pid) or o.volume)
            r = net / risk if risk > 0 else None
        by_tf.setdefault(tf, []).append((net, r))

    if not by_tf:
        print(f"\nno trades from this bot on {symbol} in the last {days} days.")
        return
    print(f"\n{'=' * 66}\n  THIS BOT'S OWN TRADES on {symbol}, last {days} days\n{'=' * 66}")
    # what the backtest says to expect, per timeframe, on data it never chose on
    EXPECT = {1: (+0.096, 17), 5: (+0.367, 21), 15: (+0.278, 20),
              30: (+0.154, 19), 60: (+0.368, 20), 240: (+0.132, 18)}
    for tf in sorted(by_tf):
        rows = by_tf[tf]
        money = sum(n for n, _ in rows)
        rs = [r for _, r in rows if r is not None]
        wins = sum(1 for n, _ in rows if n > 0.01)
        wr = 100.0 * wins / len(rows)
        print(f"\n  {tf}m — {len(rows)} trades, net {money:+.2f} "
              f"{mt5.account_info().currency}")
        print(f"      {wins} finished green ({wr:.0f}%)")
        if rs:
            avg = sum(rs) / len(rs)
            print(f"      average {avg:+.3f}R per trade")
            exp_r, exp_w = EXPECT.get(tf, (None, None))
            if exp_r is not None:
                # is this run inside the range chance would produce anyway?
                # spread of R is roughly 1.4 per trade for this strategy
                se = 1.4 / max(1, len(rs)) ** 0.5
                lo, hi = exp_r - 2 * se, exp_r + 2 * se
                verdict = ("consistent with the backtest"
                           if lo <= avg <= hi else
                           "OUTSIDE what the backtest predicts")
                print(f"      backtest expects {exp_r:+.3f}R and {exp_w}% green;"
                      f" over {len(rs)} trades\n"
                      f"      anything from {lo:+.3f} to {hi:+.3f}R is normal "
                      f"variation -> {verdict}")
                if len(rs) < 100:
                    print(f"      NOTE: {len(rs)} trades is too few to judge. "
                          f"About 150 are needed\n            before this "
                          f"number means anything.")
    if no_stop:
        print(f"\n  WARNING: {no_stop} position(s) had NO stop loss recorded. "
              f"Every order this\n           bot sends carries one, so these "
              f"were either opened by hand or\n           had their stop "
              f"removed — they are excluded from the R figures.")
    print()


def sync_orders(engine, symbol: str, tf_minutes: int):
    """Send any order the engine is holding that the broker does not have.

    place() only ever ran for signals returned on a NEW bar. The warm-up feeds
    1000 candles and throws its signals away -- correctly, they are history --
    but the ORDERS the engine built from the last few of them stay in
    engine.orders, live and unfilled and sitting right at the current price.
    Nothing sent them anywhere, so the bot printed

        limit orders resting: 2
          BUY I.VR @ 4640.90 ...

    while the terminal's Trade tab was empty. This closes that gap, and
    because it runs every bar it also retries an order whose send failed."""
    if DRY_RUN or not engine.orders:
        return
    info = mt5.symbol_info(symbol)
    if info is None:
        return
    resting, holding = broker_state(symbol)
    for o in list(engine.orders):
        # Matched at the broker's own precision. The engine's entry is a raw
        # zone edge (4650.3847) and the broker stores what it was sent, rounded
        # to the symbol's digits (4650.38) -- so the old 1e-6 comparison always
        # said "not there", and this function re-sent the same order on every
        # single bar. That is where the three identical orders on one price
        # came from; it was this line, not the engine.
        here = any(o.side == ("buy" if r.type in (mt5.ORDER_TYPE_BUY_LIMIT,
                                                  mt5.ORDER_TYPE_BUY) else "sell")
                   and _same_price(r.price_open, o.entry, info) for r in resting)
        if here or any(_same_price(p.price_open, o.entry, info) for p in holding):
            continue
        # Affordability is checked HERE rather than inside place(), because
        # this runs every bar for every unsent order: an order the balance
        # cannot carry used to reprint the same SKIPPED paragraph on every
        # single bar for as long as it lived. Three unaffordable orders filled
        # the console with the same three refusals over and over, which is what
        # made a working bot look broken. The state is reported once, on the
        # waiting line, instead of being shouted here.
        vol, _why = lots_for_risk(symbol, abs(o.entry - o.sl), RISK_PCT)
        if vol is None:
            continue
        print(f"  syncing to broker: {o.side.upper()} {o.zone} @ {o.entry:.2f}")
        place(Signal(o.bar, o.side, "PO2" if o.po2 else "limit", o.zone,
                     o.entry, o.sl, o.tp1, o.tp2, o.tp3, o.rank),
              symbol, tf_minutes)


def reconcile(engine, symbol: str):
    """Make the engine's book agree with the broker's, every bar.

    The engine simulates its own fills from candle data: a limit "fills" when a
    bar's wick reaches it, and from then on it holds a trade. That is exactly
    right in a backtest and dangerously wrong live, because it happens whether
    or not the order ever reached the broker. When a send failed -- and until
    this commit every send failed, on a NameError -- the engine still opened
    its imaginary trade, and _has_order_or_trade then blocked that zone for as
    long as the trade "ran". The console said it plainly and nobody could
    read it:

        AT THE BROKER: 0 order(s), 0 position(s)
        trades running: 1
        BLOCKED: already has an order or a running trade

    A zone in that state never re-arms. So each bar, any engine trade with no
    matching position at the broker is closed in the engine's book. Both cases
    that produce it want the same answer: if the trade never existed the zone
    must be freed, and if it existed and has since closed the zone must be
    freed too.

    The reverse direction is handled as well -- a resting order at the broker
    that the engine has expired is cancelled, so the two expiries cannot drift
    apart and leave an order nobody is managing."""
    if DRY_RUN:
        return
    info = mt5.symbol_info(symbol)
    if info is None:
        return
    resting, holding = broker_state(symbol)

    for t in engine.trades:
        if t.closed:
            continue
        if any(_fill_matches(p.price_open, t, info) for p in holding):
            continue
        # Still resting means it simply has not filled yet -- the engine is
        # early, not wrong, and the order is where it should be.
        if any(_fill_matches(r.price_open, t, info) for r in resting):
            continue
        phantom = t.stat == 0
        t.closed = True
        # Was this setup ever one the account could have taken? That decides
        # whether the zone gets its turn back.
        vol, _ = lots_for_risk(symbol, t.risk0, RISK_PCT)
        if phantom and vol is not None:
            t.exit_px = t.entry          # never happened: neither win nor loss
            # It COULD have been sent and was not, so the zone gets its turn
            # back. sig_touch is what holds a zone to one order per touch;
            # leaving it set would retire a zone over a trade that did not take
            # place. A trade the engine closed ITSELF on a target or a stop is
            # a real result and keeps its mark.
            for z in engine.zones:
                if z.uid == t.uid:
                    z.sig_touch = -1
        elif phantom:
            # The stop was wider than this balance can carry. Freeing the zone
            # here made it re-arm on the very next bar, be refused again, and
            # print the same refusal for as long as price stayed on that side
            # of it -- the wall of SKIPPED. The zone keeps its mark and gets a
            # fresh chance on its next real touch, when the stop will be
            # measured again and may well be narrower.
            t.exit_px = t.entry
        print(f"  reconciled: the {t.side.upper()} {t.zone} @ {t.entry:.2f} "
              f"is not at the broker — freeing its zone to arm again")

    # Everything the engine still expects to be at the broker: its own resting
    # orders AND the entries of trades it has already filled -- because a fill
    # the engine reads off a closed bar can be a tick ahead of the broker's,
    # and cancelling that order would delete a live setup a moment before it
    # triggered. The three legs of a split entry all rest at one price, so one
    # engine order covers all three.
    mine = {round(o.entry, info.digits) for o in engine.orders}
    mine |= {round(t.entry, info.digits) for t in engine.trades if not t.closed}
    for r in resting:
        if round(r.price_open, info.digits) in mine:
            continue
        res = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": r.ticket})
        bad = _retcode(res)
        print(f"  cancelling #{r.ticket} @ {r.price_open} — the engine has "
              f"expired this setup" + (f"  FAILED - {bad}" if bad else ""))

    # The ratchet's rungs are only needed while the trade they belong to is
    # open. Nothing removed them, so the file grew by two entries per trade
    # for as long as the bot ran and was reloaded in full at every restart.
    if LADDER:
        alive = {str(p.ticket) for p in holding}
        alive |= {f"{'buy' if p.type == mt5.POSITION_TYPE_BUY else 'sell'}@"
                  f"{round(p.price_open, info.digits)}" for p in holding}
        alive |= {str(r.ticket) for r in resting}
        alive |= {f"{'buy' if r.type in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY) else 'sell'}@"
                  f"{round(r.price_open, info.digits)}" for r in resting}
        stale = [k for k in LADDER if k not in alive]
        if stale:
            for k in stale:
                LADDER.pop(k, None)
            ladder_save()


def waiting_line(engine, symbol: str, price: float) -> str:
    """One line saying what the bot is waiting FOR.

    "(no new setup)" on every bar for hours is indistinguishable from a bot
    that has quietly died, and the waits are long by nature: measured over 70
    days, M1 goes a typical 31 minutes between signals and one wait in ten runs
    past 2.5 hours; on M5 the typical wait is 165 minutes. So the silence needs
    to show its work -- the nearest zone, how far price is from it, and whether
    anything of ours is at the broker."""
    live = [z for z in engine.zones if not z.dead]
    bits = [f"{len(live)} zones"]
    # ...and what state they are IN. This line only ever named the single
    # nearest zone, and the nearest zone is biased: a level flips exactly where
    # price is, so a freshly flipped one waiting for its pullback is almost
    # always the closest. Reading the log it looked as though every zone on the
    # chart was stuck on the same rule, when in fact each was a DIFFERENT zone
    # that clears in about 15 bars (measured: 4789 of them got their pullback
    # on M1, only 5 were still waiting after 70 days). Counting the whole set
    # says plainly how many are genuinely ready and how many are waiting.
    if live:
        st = engine.zone_states()
        state = [f"{st['armed']} armed"]
        for key, word in (("pullback", "awaiting pullback"),
                          ("breaking", "mid-break"),
                          ("touches", "need touches")):
            if st[key]:
                state.append(f"{st[key]} {word}")
        bits[0] += " (" + ", ".join(state) + ")"
    if live:
        near = min(live, key=lambda z: abs(price - (z.top + z.bot) / 2.0))
        # the NEAR edge: below the zone that is its bottom, above it its top.
        # This had them the wrong way round, so a price sitting just above a
        # zone was reported as further away than it was.
        edge = near.bot if price < near.bot else (
            near.top if price > near.top else price)
        bits.append(f"nearest {near.kind} {near.bot:.2f}-{near.top:.2f} "
                    f"({abs(price - edge):.2f} away)")
        # ...and, when price is AT that zone, why it is not firing. Measured
        # over 70 days of M1, what blocks a zone price is standing in:
        #   26% waiting for the pullback swing after a break
        #   31% the trend filter
        #   15% a break still pending
        #   the rest is touches not yet earned, sweeps, and caps
        if engine.candles:
            c = engine.candles[-1]
            if c.low <= near.top and c.high >= near.bot:
                bits.append("BLOCKED: " + engine.why_blocked(
                    near, c, len(engine.candles) - 1))
    engine_orders = len(engine.orders)
    if engine_orders:
        bits.append(f"{engine_orders} order(s) armed")
        # ...and how many of those the balance can actually carry. Without this
        # the line said "2 order(s) armed" next to an empty Trade tab and gave
        # no hint why, while the reason -- the stop is wider than this account
        # can pay for -- never appeared again after the first bar.
        if not DRY_RUN:
            try:
                afford = sum(1 for o in engine.orders
                             if lots_for_risk(symbol, abs(o.entry - o.sl),
                                              RISK_PCT)[0] is not None)
                if afford < engine_orders:
                    bits.append(f"{engine_orders - afford} too wide for "
                                f"${bal_cap(symbol):.2f} max stop")
            except Exception:
                pass
    if not DRY_RUN:
        try:
            resting, holding = broker_state(symbol)
            bits.append(f"broker {len(resting)}o/{len(holding)}p")
        except Exception:
            bits.append("broker ?")
    else:
        bits.append("DRY RUN")
    led = ledger_line()
    if led:
        bits.append(led)
    return " · ".join(bits)


def show_state(engine, symbol: str = None):
    """What the engine is actually holding right now. Without this the bot
    printed one line and then sat silent for an hour, which looks broken even
    though it is working."""
    live = [z for z in engine.zones if not z.dead]
    print(f"\nzones live: {len(live)}  "
          f"({sum(1 for z in live if not z.htf)} on the chart TF, "
          f"{sum(1 for z in live if z.htf)} on the analysis TF)")
    # Two flipped levels whose pullbacks print on the same swing land on
    # EXACTLY the same band, so one price can carry three rows. They cost
    # nothing — each zone takes at most one order and the targets already
    # ignore repeats — but printing them three times reads like a bug, so
    # identical bands are folded into one line with a count.
    rows: dict = {}
    for z in sorted(live, key=lambda z: -z.top):
        key = (z.htf, round(z.top, 2), round(z.bot, 2))
        rows.setdefault(key, []).append(z)
    shown = list(rows.items())[:10]
    for (htf, top, bot), zs in shown:
        tag = "analysis" if htf else "chart   "
        names = " / ".join(sorted({z.kind for z in zs}))
        touch = max(z.touches for z in zs)
        extra = f"   (x{len(zs)} on this band)" if len(zs) > 1 else ""
        print(f"   {tag}  {names:12s}  {bot:9.2f} – {top:9.2f}   "
              f"touches {touch}{extra}")
    if len(rows) > len(shown):
        print(f"   … and {len(rows) - len(shown)} more, furthest from price")
    if engine.orders:
        print(f"limit orders resting: {len(engine.orders)}")
        for o in engine.orders:
            print(f"   {o.side.upper():4s} {o.zone:5s} @ {o.entry:9.2f}  "
                  f"SL {o.sl:9.2f}  TP1 {o.tp1:9.2f} (next zone)")
    else:
        print("limit orders resting: none — no zone currently qualifies")
    # What the BROKER has, next to what the bot thinks it has. These two used
    # to be reported separately and it was never obvious which one was real --
    # "limit orders resting: 2" with an empty Trade tab looks identical to
    # "limit orders resting: 2" with both sitting at the broker.
    if symbol and not DRY_RUN:
        try:
            resting, holding = broker_state(symbol)
            print(f"AT THE BROKER: {len(resting)} order(s), "
                  f"{len(holding)} position(s)"
                  + ("   <- nothing sent yet" if not resting and not holding
                     and engine.orders else ""))
            for r in resting:
                print(f"   order    #{r.ticket} {r.volume_current} lots "
                      f"@ {r.price_open}  SL {r.sl}  TP {r.tp}")
            for h in holding:
                print(f"   POSITION #{h.ticket} {h.volume} lots "
                      f"@ {h.price_open}  SL {h.sl}  TP {h.tp}  "
                      f"P/L {h.profit:+.2f}")
        except Exception as e:
            print(f"(could not read the broker's side: {e})")
    elif symbol and DRY_RUN:
        print("AT THE BROKER: nothing — this is a DRY RUN, use LIVE_*.bat to send")

    open_trades = [t for t in engine.trades if not t.closed]
    if open_trades:
        print(f"trades running: {len(open_trades)}")
        for t in open_trades:
            # A stop sitting exactly on the entry is the break-even move, not a
            # trade with no risk — printed bare it looked like the latter.
            tag = "  (break-even, risk free)" if t.be else ""
            print(f"   {t.side.upper():4s} {t.zone:5s} from {t.entry:9.2f}  "
                  f"SL {t.sl:9.2f}  TP1 {t.tp1:9.2f}{tag}")


def read_args():
    """Command-line overrides, so running on a different timeframe does not mean
    editing this file. Every one of these has a sane default above.

        python mt5_runner.py                    # H1, dry run
        python mt5_runner.py --tf 5             # 5-minute chart
        python mt5_runner.py --tf 5 --live      # ...and send real orders
        python mt5_runner.py --tf 5 --no-trend  # ignore "Trend is King": ~4x
                                                # the trades, slightly worse
        python mt5_runner.py --tf 5 --limit     # rest a limit on the zone
                                                # instead of entering at market
        python mt5_runner.py --report           # score this bot's OWN closed
                                                # trades against the backtest
        python mt5_runner.py --tf 5 --tp 2      # take profit at 2x the stop:
                                                # ~50% more winning trades,
                                                # measurably less profit
    """
    global SYMBOL, TIMEFRAME_MIN, RISK_PCT, DRY_RUN, MAGIC, NO_TREND, MAX_RISK_PCT
    global ENTRY_MODE, REPORT_ONLY, TP_MULT, MAX_SPREAD_R
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--live":
            DRY_RUN = False
        elif a == "--no-trend":
            NO_TREND = True
        elif a == "--tp":
            # where to take profit, as a multiple of the stop. Measured on
            # both halves of both timeframes -- see FIXED_R_NOTE below.
            i += 1
            TP_MULT = float(args[i])
        elif a == "--report":
            REPORT_ONLY = True
        elif a == "--limit":
            ENTRY_MODE = "limit"
        elif a == "--market":
            ENTRY_MODE = "market"
        elif a in ("-h", "--help"):
            print(read_args.__doc__)
            raise SystemExit(0)
        elif i + 1 < len(args):
            v = args[i + 1]
            if a == "--tf":
                TIMEFRAME_MIN = int(v)
            elif a == "--symbol":
                SYMBOL = v
            elif a == "--risk":
                RISK_PCT = float(v)
            elif a == "--max-spread":
                # what share of a trade's R the spread may eat before the
                # setup is refused. A 10-second chart needs this raised or it
                # takes nothing at all -- see the seconds-mode banner.
                MAX_SPREAD_R = float(v)
            elif a in ("--max-risk", "--risk-cap"):
                MAX_RISK_PCT = float(v)
            else:
                raise SystemExit(f"unknown option: {a}   (try --help)")
            i += 1
        else:
            raise SystemExit(f"{a} needs a value   (try --help)")
        i += 1
    MAGIC = MAGIC_BASE + TIMEFRAME_MIN


def main():
    read_args()
    ladder_load()               # the rungs of any trade that outlived a restart
    if mt5 is None:
        raise SystemExit("MetaTrader5 package not installed (Windows only): pip install MetaTrader5")
    if not mt5.initialize():
        raise SystemExit(f"MT5 init failed: {mt5.last_error()}")

    # A symbol that is not in Market Watch answers every quote with None, and
    # order_send fails with an error that does not mention the symbol at all.
    if not mt5.symbol_select(SYMBOL, True):
        raise SystemExit(f"{SYMBOL} is not available on this account "
                         f"({mt5.last_error()}). Check the exact name in "
                         f"Market Watch — some brokers use XAUUSD.fix / .zero.")

    if REPORT_ONLY:
        # --report is a question about history, not a trading session: no
        # warm-up, no orders, no waiting for a bar.
        report(SYMBOL)
        mt5.shutdown()
        return

    acc = mt5.account_info()
    # DEMO or REAL is decided by the account MetaTrader is logged into, NOT by
    # which launcher was double-clicked. Those are two independent switches and
    # confusing them is the one mistake that costs real money, so the banner
    # says which is which every single time.
    modes = {0: "DEMO", 1: "CONTEST", 2: "REAL MONEY"}
    kind = modes.get(getattr(acc, "trade_mode", 0), "UNKNOWN")
    print(f"MT5 connected: account {acc.login} ({acc.server}), "
          f"balance {acc.balance} {acc.currency}")
    print(f"account type: *** {kind} ***")
    line = what_fits(SYMBOL, TIMEFRAME_MIN)
    if line:
        print(line)
    try:
        allpos, others = family_exposure(SYMBOL)
        if others:
            tfs = sorted({p.magic - MAGIC_BASE for p in others})
            print(f"NOTE: other SNRZ bots are already holding "
                  f"{len(others)} position(s) on {SYMBOL} "
                  f"(timeframes {', '.join(str(t) + 'm' for t in tfs)}). "
                  f"Each bot risks its own {RISK_PCT}% per trade, so the "
                  f"account carries all of them AT ONCE.")
    except Exception:
        pass
    if not DRY_RUN and not getattr(acc, "trade_allowed", True):
        raise SystemExit("this account cannot trade (trade_allowed is false) — "
                         "on the terminal side that is usually the Algo Trading "
                         "button being off.")
    print(f"symbol {SYMBOL} · chart TF {TIMEFRAME_MIN}m · risk {RISK_PCT}% "
          f"· {'DRY RUN — nothing is sent' if DRY_RUN else 'SENDING ORDERS'}")
    if not DRY_RUN:
        info = mt5.symbol_info(SYMBOL)
        print(f"\n  contract {info.trade_contract_size} · volume "
              f"{info.volume_min} to {info.volume_max} step {info.volume_step} "
              f"· digits {info.digits}")
        print(f"  the book's exit needs at least {4 * info.volume_step} lots so "
              f"it can be split half / quarter / quarter — every single-exit "
              f"plan measured NEGATIVE.")
        if kind == "REAL MONEY":
            print("\n" + "!" * 60)
            print("  ORDERS WILL BE SENT TO A REAL-MONEY ACCOUNT.")
            print("  Losses here are your own money, not demo money.")
            print("!" * 60)
        else:
            print(f"\n  Orders will be sent, but this is a {kind} account — "
                  f"nothing here is real money.")
        input("\nPress Enter to go on, or Ctrl+C to stop: ")
        resting, holding = broker_state(SYMBOL)
        if resting or holding:
            print(f"  note: this bot already has {len(resting)} order(s) and "
                  f"{len(holding)} position(s) at the broker; they are left "
                  f"alone and managed, not duplicated.")

    tf = mt5_timeframe(TIMEFRAME_MIN)
    # the analysis timeframe follows the captain's ladder from the chart we run
    # on — two rungs up, the middle one skipped
    cfg = Config(chart_minutes=TIMEFRAME_MIN, entry_mode=ENTRY_MODE)
    if TP_MULT > 0:
        cfg.fixed_r_mult = TP_MULT
    # Which exit plan is this account physically able to run?
    #
    # place() already decides per trade: if the size cannot be divided into
    # half / quarter / quarter it sends ONE position and ratchets the stop up
    # behind the targets instead. But the ENGINE was left on "scale" whatever
    # the account could do -- so on a balance that can only ever trade the
    # minimum lot, the broker ran the ratchet while the engine's own copy of
    # the trade ran the scaled plan. Every line the console printed about a
    # running trade described a plan the account was not executing.
    #
    # They measure almost the same (M1 +0.082 vs +0.085R, M5 +0.287 vs
    # +0.282R), so this is not about returns -- it is about the console and
    # the reconciler describing the trade that actually exists.
    #
    # Only the SCALED plan needs replacing -- a fixed-R exit is one target at
    # one price, which an undividable position runs exactly as written.
    info0 = mt5.symbol_info(SYMBOL)
    if info0 is not None and cfg.exit_policy == "scale" \
            and split_volume(info0.volume_min, info0) is None:
        cfg.exit_policy = "ratchet"
    if NO_TREND:
        cfg.trend_filter = False
        print("trend filter OFF — about 4x the trades, measured slightly "
              "lower quality each")
    engine = SnrzEngine(cfg)
    if cfg.exit_policy == "ratchet":
        print("exit: ONE position, stop ratcheting up behind each target —\n"
              "      this balance cannot split a position into half/quarter/\n"
              "      quarter, so that is the plan, and the engine now runs it too.")
    if cfg.exit_policy == "fixed_r":
        print(f"exit: the whole position comes off at {cfg.fixed_r_mult:g}x the "
              f"stop distance.\n"
              f"      Measured on data the choice was NOT made on: about one "
              f"trade in five\n"
              f"      finishes green (was one in eight), on the same number of "
              f"trades.\n"
              f"      Banking earlier buys more winners and sells profit -- at "
              f"1R about half\n"
              f"      finish green but the account LOSES money. 3R is where "
              f"both halves agree.")
    print("what to expect: roughly half of all trades still hit the stop and "
          "about a\nthird come back to break-even for nothing. It is "
          "profitable because the\nwinners are larger than the losers, so a "
          "run of losses is normal here and\nis not a sign the bot is broken.")
    if ENTRY_MODE == "market":
        print("entry: AT MARKET the moment the setup prints — no resting order.\n"
              "       Measured better per trade (M5 +0.206 -> +0.242R, M15\n"
              "       +0.082 -> +0.201R, H1 -0.070 -> +0.199R) because a limit\n"
              "       only fills when price comes back, and it often comes back\n"
              "       because the zone is failing.\n"
              "       It also needs a WIDER stop, so a small account can afford\n"
              "       fewer of them. Use --limit for the book's resting order.")
    else:
        print("entry: a LIMIT resting on the zone (the book's way) — "
              "use --market to enter straight away instead.")
    if cfg.single_tf:
        print(f"zones marked, confirmed and traded all on {TIMEFRAME_MIN}m")
    else:
        print(f"zones marked on {SnrzEngine.analysis_minutes(TIMEFRAME_MIN)}m, "
              f"refined and traded on {TIMEFRAME_MIN}m")

    # warm up with history
    rates = mt5.copy_rates_from_pos(SYMBOL, tf, 1, 1000)
    if rates is None or len(rates) < 200:
        raise SystemExit(f"only {0 if rates is None else len(rates)} candles "
                         f"available for {SYMBOL} on this timeframe. Open that "
                         f"chart in the terminal once so it downloads history.")
    # The warm-up already replays history to build the zones; counting what it
    # produces on the way past turns "it has not traded all night, is it
    # broken?" into a number available in the first second.
    warm = 0
    for r in rates:
        warm += len(engine.on_candle(
            Candle(int(r["time"]), r["open"], r["high"], r["low"], r["close"])))
    last_time = rates[-1]["time"]
    hours = len(rates) * TIMEFRAME_MIN / 60.0
    print(f"warmed up with {len(rates)} candles")
    print(f"\nSELF-TEST — replaying those {len(rates)} bars (about {hours:.0f} "
          f"hours) through the\n              same rules the live run uses:")
    print(f"      {warm} setups, i.e. about {24 * warm / max(hours, 1):.1f} a day "
          f"on this timeframe")
    if warm == 0:
        print("      ZERO. The rules found nothing in this history, so waiting\n"
              "      longer will not help -- it is the symbol, the timeframe or\n"
              "      the settings, not the connection.")
    else:
        # ...and how many of them this balance could actually have sized.
        acc2 = mt5.account_info()
        risks = []
        for sg in engine.signals[-warm:]:
            vol, info2 = lots_for_risk(SYMBOL, abs(sg.price - sg.sl), RISK_PCT)
            if vol is not None and acc2 and acc2.balance > 0:
                risks.append(100.0 * info2 / acc2.balance)
        if risks:
            risks.sort()
            mid = risks[len(risks) // 2]
            print(f"      typical trade puts {mid:.1f}% of the account at "
                  f"stake (worst here {risks[-1]:.1f}%)")
            if mid >= 5.0:
                print(f"      About half of all trades hit their stop, so "
                      f"expect runs of\n      losses at that size. "
                      f"--risk-cap 3 skips the big ones instead.")
        print("      If the live run now sends nothing while this says setups\n"
              "      exist, the reason is a guard at send time and the "
              "'since start:'\n      tally on each status line names which one.")
    if getattr(engine, "gap_unreachable", False):
        print("\nNOTE: GAP zones are switched on but cannot be drawn in this\n"
              "      configuration (they are analysis-timeframe only, and this\n"
              "      runs single-timeframe). They contribute nothing either way.")

    # Whatever the balance has done, this says whether THIS bot did it.
    try:
        mine, closed, pnl = bot_ledger(SYMBOL)
        if not mine:
            print(f"this bot's own trades at the broker (last 30 days): NONE — "
                  f"it has not touched the balance")
        else:
            print(f"this bot's own trades at the broker (last 30 days): "
                  f"{len(closed)} closed, net {pnl:+.2f} {acc.currency}")
    except Exception as e:
        print(f"(could not read the trade history: {e})")

    show_state(engine, SYMBOL)
    # The warm-up rebuilt the engine's book from history and knows nothing
    # about what is actually at the broker, so the two are squared up before a
    # single order is sent. Orders the warm-up left resting are live setups at
    # today's prices and belong at the broker before the first new bar arrives.
    reconcile(engine, SYMBOL)
    sync_orders(engine, SYMBOL, TIMEFRAME_MIN)
    print(f"\nwatching for the next closed {TIMEFRAME_MIN}m bar — on this timeframe "
          f"that is one check every {TIMEFRAME_MIN} minutes, so silence is normal.")
    print("Ctrl+C to stop.\n")

    beat = 0
    while True:
        # One second, not five. The stop and the target are already AT the
        # broker, so those are enforced tick by tick whatever this loop does --
        # but the break-even move and the ratchet are decided HERE, and every
        # second they are late is a second the trade is riskier than the plan
        # says. Entries stay on the closed bar: the zones, the ATR and the
        # touch counts are all built from finished candles, and deciding from
        # half a candle would be trading something nobody measured.
        time.sleep(POLL_SECONDS)
        # Image 41's break-even is software in the backtest; live, nothing moves
        # a stop unless it is asked to. The measured numbers assume it happens,
        # so it is checked every pass, not once a bar.
        if not DRY_RUN:
            try:
                manage_open_positions(SYMBOL)
            except Exception as e:            # never let housekeeping kill the run
                print(f"  (break-even check failed this pass: {e})")

        bars = mt5.copy_rates_from_pos(SYMBOL, tf, 1, 1)  # last CLOSED bar
        if bars is None or len(bars) == 0:
            # The terminal drops the connection on its own now and then. Without
            # this the bot span forever on None and looked alive while doing
            # nothing at all.
            if mt5.terminal_info() is None:
                print("  terminal connection lost — reconnecting …")
                mt5.shutdown()
                time.sleep(10)
                if mt5.initialize() and mt5.symbol_select(SYMBOL, True):
                    print("  reconnected")
            continue
        b = bars[0]
        if b["time"] == last_time:
            beat += 1
            # A live line every second WHILE A TRADE IS ON, so the seconds
            # between bars are not a black box: where price is, what the trade
            # is worth, and how far it has left to run either way. It rewrites
            # one line instead of scrolling.
            if not DRY_RUN and beat % max(1, int(1 / POLL_SECONDS)) == 0:
                try:
                    _, holding = broker_state(SYMBOL)
                    tick = mt5.symbol_info_tick(SYMBOL)
                    if holding and tick:
                        p = holding[0]
                        px = tick.bid if p.type == mt5.POSITION_TYPE_BUY else tick.ask
                        to_sl = abs(px - p.sl) if p.sl else 0.0
                        to_tp = abs(p.tp - px) if p.tp else 0.0
                        way = "BUY " if p.type == mt5.POSITION_TYPE_BUY else "SELL"
                        print(f"\r  live · {way} from {p.price_open:.2f} · now "
                              f"{px:.2f} · P/L {p.profit:+.2f} · "
                              f"{to_sl:.2f} to the stop, {to_tp:.2f} to target"
                              f"   ", end="", flush=True)
                        beat = beat % 100000
                        continue
                except Exception:
                    pass
            if beat % max(1, int(300 / POLL_SECONDS)) == 0:   # every ~5 minutes
                nxt = datetime.fromtimestamp(last_time + TIMEFRAME_MIN * 60,
                                             tz=timezone.utc)
                print(f"  … alive, price {mt5.symbol_info_tick(SYMBOL).bid:.2f}, "
                      f"next bar closes about {nxt:%H:%M} UTC")
            continue
        beat = 0
        last_time = b["time"]
        # every new zone produces its own signal, so several may arrive on one
        # bar — each gets its own limit order at its own zone
        sigs = engine.on_candle(Candle(int(b["time"]), b["open"], b["high"],
                                       b["low"], b["close"]))
        stamp = datetime.fromtimestamp(int(b["time"]), tz=timezone.utc)
        if sigs:
            print(f"[{stamp:%Y-%m-%d %H:%M} UTC] bar closed {b['close']:.2f}")
        else:
            print(f"[{stamp:%Y-%m-%d %H:%M} UTC] {b['close']:.2f}  "
                  f"no setup — {waiting_line(engine, SYMBOL, b['close'])}")
        if sigs:
            # How many slots are actually free at the BROKER, not in the
            # engine's book -- that is what decides whether a new order can go
            # out. Choosing happens once, over all of this bar's setups
            # together, so the strongest one gets the slot.
            if DRY_RUN:
                free = engine.cfg.max_open
            else:
                resting, holding = broker_state(SYMBOL)
                prices = {round(o.price_open, 2) for o in resting}
                prices |= {round(p.price_open, 2) for p in holding}
                free = max(0, engine.cfg.max_open - len(prices))
            choose_and_place(sigs, SYMBOL, TIMEFRAME_MIN, free)
        reconcile(engine, SYMBOL)
        sync_orders(engine, SYMBOL, TIMEFRAME_MIN)
        if sigs:
            show_state(engine, SYMBOL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # The tally is the whole point of keeping it -- print it where it
        # cannot be missed, on the way out.
        print("\n\n" + "=" * 62)
        print("  " + (ledger_line() or "no setups were produced this run"))
        print("=" * 62)
