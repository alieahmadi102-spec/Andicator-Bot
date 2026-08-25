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

# The ceiling on what ONE trade may risk when the broker's minimum lot leaves
# no room to scale down. Below some balance 0.01 lots is the only position
# there is, so the choice is take it at whatever it risks or take nothing.
# Measured on a $114 account over 83 days: a 2% ceiling took 13 trades, 3% took
# 75 and finished flat with a 23% drawdown, 5% took 617 and lost a quarter of
# the account with a 75% drawdown. 3 is the last value that is not simply a
# faster way down.
MAX_RISK_PCT = 3.0


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

    # No room to scale: the smallest position the broker sells is the only one.
    floor_lots = info.volume_min
    floor_risk = floor_lots * loss_per_lot
    floor_pct = 100.0 * floor_risk / acc.balance if acc.balance > 0 else 999.0
    if floor_pct <= MAX_RISK_PCT:
        return floor_lots, floor_risk
    return None, (
        f"would risk {floor_pct:.1f}% of the account and the ceiling is "
        f"{MAX_RISK_PCT:.1f}% — the smallest lot ({floor_lots}) risks "
        f"${floor_risk:.2f} on a ${sl_distance:.2f} stop. A tighter stop fits; "
        f"this one does not.")


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
    """Whether this balance can trade this symbol on this timeframe — answered
    against the stop sizes the strategy actually produces, not in the abstract.

    The old version of this printed the widest stop that would fit and left the
    reader to guess whether the strategy ever produces one that narrow. It
    does not, on a small account, and saying so plainly is the whole point:
    a bot that silently skips every setup looks exactly like a broken one."""
    acc = mt5.account_info()
    per = cheapest_lot_risk(symbol)
    info = mt5.symbol_info(symbol)
    if acc is None or per is None or info is None:
        return ""
    typical = MEDIAN_STOP.get(tf_minutes, 5.0)
    forced = per * typical                       # what the minimum lot must risk
    pct = 100.0 * forced / acc.balance if acc.balance > 0 else 999.0
    target_lots = (acc.balance * RISK_PCT / 100.0) / (per / info.volume_min)

    lines = [f"sizing: target {RISK_PCT:g}% of the balance, hard ceiling "
             f"{MAX_RISK_PCT:g}%",
             f"        smallest lot {info.volume_min} risks ${per:.2f} for every "
             f"$1 the stop is wide",
             f"        a typical {tf_minutes}m setup here stops ${typical:.2f} "
             f"away, so that lot risks ${forced:.2f} = {pct:.1f}% of "
             f"${acc.balance:.2f}"]
    if target_lots >= info.volume_min:
        lines.append(f"        -> there is room to size: about "
                     f"{target_lots:.2f} lots on the typical setup")
    elif pct <= MAX_RISK_PCT:
        lines.append(f"        -> no room to size down, but {pct:.1f}% is under "
                     f"the ceiling: it trades at the minimum lot")
    else:
        need = forced * 100.0 / MAX_RISK_PCT
        lines.append(f"        -> {pct:.1f}% is OVER the {MAX_RISK_PCT:g}% "
                     f"ceiling, so most setups here will be SKIPPED.")
        lines.append(f"           On {symbol} this timeframe needs about "
                     f"${need:.0f} to trade properly.")
        # ...and rather than stopping at that, look for a smaller contract.
        try:
            opts = [o for o in affordable_symbols(acc.balance, typical)
                    if o[3] <= MAX_RISK_PCT]
        except Exception:
            opts = []
        if opts:
            lines.append("           This account DOES carry a smaller gold "
                         "contract that fits:")
            for forced2, name, per2, pct2 in opts[:4]:
                lines.append(f"             --symbol {name:<16} minimum lot "
                             f"risks ${forced2:.2f} = {pct2:.1f}%")
        else:
            lines.append("           No smaller gold contract on this account "
                         "fits either — checked every XAU symbol in Market Watch.")
    return "\n".join(lines)


# ── the pieces the order path is built from ────────────────────────────────
# These four -- LADDER, split_volume, _filling, _retcode -- were CALLED by
# place() and manage_open_positions() and defined nowhere. Python resolves a
# global name when the line runs, not when the file loads, so the module
# imported cleanly, the bot started, printed its banner, found zones and
# announced signals -- and then raised NameError on the first signal that got
# as far as sizing. That is why no order was ever sent: not a rule that was too
# strict, a function that did not exist.
LADDER: dict = {}


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


def place(signal, symbol: str, tf_minutes: int):
    """Book p41/p42: the entry is a LIMIT order resting AT the zone, not a
    market order. signal.price is that zone price -- the engine and the
    backtester both assume the fill happens there, so a market order here would
    make the live bot trade something the numbers never measured."""
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None:
        print(f"  SKIPPED - the terminal has no quote for {symbol}")
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
        print(f"  SKIPPED - spread is {spread:.{digits}f}, which is "
              f"{100 * spread_r:.0f}% of the {sl_distance:.{digits}f} stop "
              f"(limit {100 * MAX_SPREAD_R:.0f}%).")
        return

    # The broker refuses a stop or target closer to the price than its stops
    # level. Their spec showed 0, but a broker can change it without notice.
    stops = getattr(info, "trade_stops_level", 0) * info.point
    if stops > 0 and sl_distance < stops:
        print(f"  SKIPPED - the {sl_distance:.{digits}f} stop is inside the "
              f"broker's minimum distance of {stops:.{digits}f}")
        return

    volume, why = lots_for_risk(symbol, sl_distance, RISK_PCT)
    if volume is None:
        print(f"  SKIPPED - {why}")
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

    if signal.side == "buy":
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
            print(f"  SKIPPED - price is already {slip:.{digits}f} past the "
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
        print(f"  syncing to broker: {o.side.upper()} {o.zone} @ {o.entry:.2f}")
        place(Signal(o.bar, o.side, "PO2" if o.po2 else "limit", o.zone,
                     o.entry, o.sl, o.tp1, o.tp2, o.tp3), symbol, tf_minutes)


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
        if any(_same_price(p.price_open, t.entry, info) for p in holding):
            continue
        # Still resting means it simply has not filled yet -- the engine is
        # early, not wrong, and the order is where it should be.
        if any(_same_price(r.price_open, t.entry, info) for r in resting):
            continue
        phantom = t.stat == 0
        t.closed = True
        if phantom:
            t.exit_px = t.entry          # never happened: neither win nor loss
            # ...and the setup was never actually offered to the market, so the
            # zone gets its turn back. sig_touch is what holds a zone to one
            # order per touch; leaving it set would retire a zone over a trade
            # that did not take place. A trade the engine closed ITSELF on a
            # target or a stop is a real result and keeps its mark.
            for z in engine.zones:
                if z.uid == t.uid:
                    z.sig_touch = -1
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
    if not DRY_RUN:
        try:
            resting, holding = broker_state(symbol)
            bits.append(f"broker {len(resting)}o/{len(holding)}p")
        except Exception:
            bits.append("broker ?")
    else:
        bits.append("DRY RUN")
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
    """
    global SYMBOL, TIMEFRAME_MIN, RISK_PCT, DRY_RUN, MAGIC, NO_TREND, MAX_RISK_PCT
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--live":
            DRY_RUN = False
        elif a == "--no-trend":
            NO_TREND = True
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
            elif a == "--max-risk":
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
    cfg = Config(chart_minutes=TIMEFRAME_MIN)
    if NO_TREND:
        cfg.trend_filter = False
        print("trend filter OFF — about 4x the trades, measured slightly "
              "lower quality each")
    engine = SnrzEngine(cfg)
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
    for r in rates:
        engine.on_candle(Candle(int(r["time"]), r["open"], r["high"], r["low"], r["close"]))
    last_time = rates[-1]["time"]
    print(f"warmed up with {len(rates)} candles")

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
        time.sleep(5)
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
            if beat % 60 == 0:            # every ~5 minutes, prove it is alive
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
        for sig in sigs:
            print(f"  SIGNAL {sig.side.upper()} {sig.zone} @ {sig.price:.2f}  "
                  f"SL {sig.sl:.2f}  TP1 {sig.tp1:.2f} (next zone)")
            if not DRY_RUN:
                # After a restart the engine rebuilds its orders from history
                # and would happily stack a second set on top of the ones still
                # resting at the broker. The broker is the only truth here.
                resting, _ = broker_state(SYMBOL)
                info = mt5.symbol_info(SYMBOL)
                if info and any(_same_price(o.price_open, sig.price, info)
                                for o in resting):
                    print("  already resting at the broker — not duplicated")
                    continue
            place(sig, SYMBOL, TIMEFRAME_MIN)
        reconcile(engine, SYMBOL)
        sync_orders(engine, SYMBOL, TIMEFRAME_MIN)
        if sigs:
            show_state(engine, SYMBOL)


if __name__ == "__main__":
    main()
