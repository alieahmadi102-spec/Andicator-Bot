"""
MT5 live runner (skeleton) — connects the SNRZ engine to MetaTrader 5.

Requirements (Windows, or Linux+Wine):
    pip install MetaTrader5

هشدار: این یک اسکلت اولیه است. قبل از پول واقعی، حتماً روی حساب دمو تست کن.
"""
from __future__ import annotations

import math
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
MAGIC = 20260819
ORDER_EXPIRY_BARS = 40      # same as Config.order_expiry_bars in snrz_core

# Nothing is sent to the broker while this is True. Turn it off only after you
# have watched it print signals for a while ON A DEMO ACCOUNT and you agree
# with them. The crypto runner defaults the same way.
DRY_RUN = True


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
    """Returns (lots, risk_money) or (None, why) when the account is too small.

    The old version quietly fell back to the broker minimum. On a $150 account
    with an H1 gold stop of about $70, the smallest lot the broker allows
    (0.01) risks the whole $70 — 47% of the account, when the book's rule is
    1%. Two losses and the account is gone. Refusing the trade is the only
    honest answer; silently taking 47% risk is not."""
    info = mt5.symbol_info(symbol)
    acc = mt5.account_info()
    risk_money = acc.balance * risk_pct / 100.0
    tick_value = info.trade_tick_value
    tick_size = info.trade_tick_size
    if sl_distance <= 0 or tick_value <= 0 or tick_size <= 0:
        return None, "cannot size the trade (broker gave no tick value)"

    loss_per_lot = sl_distance / tick_size * tick_value
    want = risk_money / loss_per_lot
    steps = math.floor(want / info.volume_step)
    lots = round(steps * info.volume_step, 8)

    if lots < info.volume_min:
        min_loss = info.volume_min * loss_per_lot
        return None, (
            f"account too small for this stop: the smallest lot the broker "
            f"allows ({info.volume_min}) risks ${min_loss:.2f} on a ${sl_distance:.2f} "
            f"stop, which is {100 * min_loss / acc.balance:.0f}% of your "
            f"${acc.balance:.2f} — the {risk_pct}% rule allows ${risk_money:.2f}. "
            f"Use a CENT account, a smaller timeframe, or more balance.")
    return min(lots, info.volume_max), risk_money


# Intermediate targets for positions that could not be split, so the stop can
# be ratcheted behind them.  {(side, entry): (tp1, tp2)}
LADDER: dict = {}


def _retcode(res) -> str:
    """order_send returns a struct, not an exception. Printing it raw hides the
    one field that matters."""
    if res is None:
        return f"no reply from the terminal ({mt5.last_error()})"
    ok = (mt5.TRADE_RETCODE_DONE, getattr(mt5, "TRADE_RETCODE_PLACED", 10008))
    if res.retcode in ok:
        return ""
    known = {
        10004: "requote",
        10006: "the broker rejected it",
        10013: "invalid request",
        10014: "invalid volume",
        10015: "invalid price",
        10016: "invalid stops — SL/TP too close to the price",
        10018: "the market is closed",
        10019: "not enough money",
        10027: "algo trading is DISABLED in the terminal (the Algo Trading button)",
        10030: "this filling mode is not supported by the broker",
    }
    return known.get(res.retcode, f"retcode {res.retcode}") + f" [{res.comment}]"


def _filling(info):
    """The broker publishes which filling modes it accepts as a bitmask. Sending
    IOC to a broker that only takes FOK is retcode 10030, which used to come
    back as an unreadable struct."""
    mask = getattr(info, "filling_mode", 0)
    if mask & 2:
        return mt5.ORDER_FILLING_IOC
    if mask & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def split_volume(total: float, info):
    """The book's exit plan is HALF at TP1, a QUARTER at TP2, a QUARTER at TP3,
    and that split is where the whole edge lives. Measured on 83 days with the
    real spread:

        exit plan                 median E
        half / quarter / quarter    -0.008     (M5 +0.120)
        everything at TP1           -0.078
        everything at TP2           -0.105
        everything at TP3           -0.088

    Every single-exit plan loses. So the runner must place THREE orders at the
    same price with the same stop and different targets, not one order with one
    TP -- one order IS the -0.078 plan.

    That needs at least four volume steps to divide up. Returns None when the
    account cannot be split that finely, because taking the trade anyway would
    be running the losing plan."""
    step = info.volume_step
    units = int(round(total / step))
    if units < 4:
        return None
    half = (units // 2) * step
    rest = units - (units // 2)
    q1 = (rest // 2) * step
    q2 = (rest - rest // 2) * step
    return [round(half, 8), round(q1, 8), round(q2, 8)]


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
    # A single position aims at the far target and ratchets its way there.
    targets = [signal.tp3] if ratchet else [signal.tp1, signal.tp2, signal.tp3]
    if ratchet:
        # manage_open_positions needs the intermediate targets to ratchet
        # against, and a position carries only its own sl/tp. Keyed by side and
        # entry price, which is what a filled position can be matched on.
        LADDER[(signal.side, rnd(signal.price))] = (rnd(signal.tp1),
                                                    rnd(signal.tp2))

    if DRY_RUN:
        for vol, tp, n in zip(legs, targets, (3,) if ratchet else (1, 2, 3)):
            print(f"  DRY_RUN - would place {signal.side.upper()}"
                  f"{' LIMIT' if pending else ' (market)'} {vol} {symbol} "
                  f"@ {rnd(signal.price)}  SL {rnd(signal.sl)}  TP{n} {rnd(tp)}")
        return

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
            "type_filling": _filling(info),
        }
        if pending:
            req["type_time"] = mt5.ORDER_TIME_SPECIFIED
            req["expiration"] = expiry
        else:
            req["deviation"] = 20
            req["type_time"] = mt5.ORDER_TIME_GTC
        res = mt5.order_send(req)
        bad = _retcode(res)
        if bad:
            print(f"  TP{n} leg FAILED - {bad}")
        else:
            print(f"  TP{n} leg placed: {vol} lots, ticket {res.order}")


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
        rungs = LADDER.get((side, entry))
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
    resting, holding = broker_state(symbol)
    for o in list(engine.orders):
        here = any(o.side == ("buy" if r.type in (mt5.ORDER_TYPE_BUY_LIMIT,
                                                  mt5.ORDER_TYPE_BUY) else "sell")
                   and abs(r.price_open - o.entry) < 1e-6 for r in resting)
        if here or any(abs(p.price_open - o.entry) < 1e-6 for p in holding):
            continue
        print(f"  syncing to broker: {o.side.upper()} {o.zone} @ {o.entry:.2f}")
        place(Signal(o.bar, o.side, "PO2" if o.po2 else "limit", o.zone,
                     o.entry, o.sl, o.tp1, o.tp2, o.tp3), symbol, tf_minutes)


def show_state(engine):
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
    """
    global SYMBOL, TIMEFRAME_MIN, RISK_PCT, DRY_RUN
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--live":
            DRY_RUN = False
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
            else:
                raise SystemExit(f"unknown option: {a}   (try --help)")
            i += 1
        else:
            raise SystemExit(f"{a} needs a value   (try --help)")
        i += 1


def main():
    read_args()
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

    show_state(engine)
    # Orders the warm-up left resting are live setups at today's prices, so
    # they belong at the broker before the first new bar arrives.
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
        print(f"[{stamp:%Y-%m-%d %H:%M} UTC] bar closed {b['close']:.2f}"
              f"{'' if sigs else '  (no new setup)'}")
        for sig in sigs:
            print(f"  SIGNAL {sig.side.upper()} {sig.zone} @ {sig.price:.2f}  "
                  f"SL {sig.sl:.2f}  TP1 {sig.tp1:.2f} (next zone)")
            if not DRY_RUN:
                # After a restart the engine rebuilds its orders from history
                # and would happily stack a second set on top of the ones still
                # resting at the broker. The broker is the only truth here.
                resting, _ = broker_state(SYMBOL)
                if any(abs(o.price_open - sig.price) < 1e-6 for o in resting):
                    print("  already resting at the broker — not duplicated")
                    continue
            place(sig, SYMBOL, TIMEFRAME_MIN)
        sync_orders(engine, SYMBOL, TIMEFRAME_MIN)
        if sigs:
            show_state(engine)


if __name__ == "__main__":
    main()
