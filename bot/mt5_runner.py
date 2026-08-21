"""
MT5 live runner (skeleton) — connects the SNRZ engine to MetaTrader 5.

Requirements (Windows, or Linux+Wine):
    pip install MetaTrader5

هشدار: این یک اسکلت اولیه است. قبل از پول واقعی، حتماً روی حساب دمو تست کن.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:  # keeps the repo importable on non-Windows dev machines
    mt5 = None

from snrz_core import Candle, Config, SnrzEngine

SYMBOL = "XAUUSD"
# ANALYSIS timeframe — the book draws zones on 1H/4H/Daily and only monitors
# below. M15 is a confirmation timeframe, not a zone timeframe.
TIMEFRAME_MIN = 60
RISK_PCT = 1.0              # max 1% risk per trade (book rule)
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


def place(signal, symbol: str, tf_minutes: int):
    """Book p41/p42: the entry is a LIMIT order resting AT the zone, not a
    market order. signal.price is that zone price — the engine and the
    backtester both assume the fill happens there, so sending a market order
    here would make the live bot trade something the numbers never measured.
    The order expires the same way it does in the backtest."""
    tick = mt5.symbol_info_tick(symbol)
    market = tick.ask if signal.side == "buy" else tick.bid
    volume, info = lots_for_risk(symbol, abs(signal.price - signal.sl), RISK_PCT)
    if volume is None:
        print(f"  SKIPPED — {info}")
        return
    expiry = datetime.now(timezone.utc) + timedelta(
        minutes=tf_minutes * ORDER_EXPIRY_BARS)

    if signal.side == "buy":
        # price is already at or below the zone -> the limit would fill instantly
        kind = mt5.ORDER_TYPE_BUY_LIMIT if market > signal.price else mt5.ORDER_TYPE_BUY
    else:
        kind = mt5.ORDER_TYPE_SELL_LIMIT if market < signal.price else mt5.ORDER_TYPE_SELL
    pending = kind in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_SELL_LIMIT)

    if DRY_RUN:
        print(f"  DRY_RUN — would place {signal.side.upper()}"
              f"{' LIMIT' if pending else ' (market, price already there)'} "
              f"{volume} {symbol} @ {signal.price:.2f}  SL {signal.sl:.2f}  "
              f"TP1 {signal.tp1:.2f}"
              + (f"  expires {expiry:%Y-%m-%d %H:%M} UTC" if pending else ""))
        return

    req = {
        "action": mt5.TRADE_ACTION_PENDING if pending else mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": kind,
        "price": signal.price if pending else market,
        "sl": signal.sl,
        "tp": signal.tp1,   # the next zone, not a 1R guess
        "magic": MAGIC,
        "comment": f"SNRZ {signal.kind} {signal.zone}",
    }
    if pending:
        req["type_time"] = mt5.ORDER_TIME_SPECIFIED
        req["expiration"] = expiry
    else:
        req["deviation"] = 20
        req["type_time"] = mt5.ORDER_TIME_GTC
        req["type_filling"] = mt5.ORDER_FILLING_IOC
    res = mt5.order_send(req)
    print("order_send:", res)


def show_state(engine):
    """What the engine is actually holding right now. Without this the bot
    printed one line and then sat silent for an hour, which looks broken even
    though it is working."""
    live = [z for z in engine.zones if not z.dead]
    print(f"\nzones live: {len(live)}  "
          f"({sum(1 for z in live if not z.htf)} on the chart TF, "
          f"{sum(1 for z in live if z.htf)} on the analysis TF)")
    for z in sorted(live, key=lambda z: -z.top)[:10]:
        tag = "analysis" if z.htf else "chart   "
        print(f"   {tag}  {z.kind:5s}  {z.bot:9.2f} – {z.top:9.2f}   touches {z.touches}")
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
            print(f"   {t.side.upper():4s} {t.zone:5s} from {t.entry:9.2f}  "
                  f"SL {t.sl:9.2f}  TP1 {t.tp1:9.2f}")


def main():
    if mt5 is None:
        raise SystemExit("MetaTrader5 package not installed (Windows only): pip install MetaTrader5")
    if not mt5.initialize():
        raise SystemExit(f"MT5 init failed: {mt5.last_error()}")

    acc = mt5.account_info()
    print(f"MT5 connected: account {acc.login} ({acc.server}), "
          f"balance {acc.balance} {acc.currency}")
    print(f"symbol {SYMBOL} · analysis TF {TIMEFRAME_MIN}m · risk {RISK_PCT}% "
          f"· mode {'DRY RUN (no orders sent)' if DRY_RUN else '*** LIVE ORDERS ***'}")
    if not DRY_RUN:
        input("DRY_RUN is off, real orders will be sent. Press Enter to go on, "
              "or Ctrl+C to stop: ")

    tf = mt5_timeframe(TIMEFRAME_MIN)
    engine = SnrzEngine(Config())

    # warm up with history
    rates = mt5.copy_rates_from_pos(SYMBOL, tf, 1, 1000)
    for r in rates:
        engine.on_candle(Candle(int(r["time"]), r["open"], r["high"], r["low"], r["close"]))
    last_time = rates[-1]["time"]
    print(f"warmed up with {len(rates)} candles")
    show_state(engine)
    print(f"\nwatching for the next closed {TIMEFRAME_MIN}m bar — on this timeframe "
          f"that is one check every {TIMEFRAME_MIN} minutes, so silence is normal.")
    print("Ctrl+C to stop.\n")

    beat = 0
    while True:
        time.sleep(5)
        bars = mt5.copy_rates_from_pos(SYMBOL, tf, 1, 1)  # last CLOSED bar
        if bars is None or len(bars) == 0:
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
            place(sig, SYMBOL, TIMEFRAME_MIN)
        if sigs:
            show_state(engine)


if __name__ == "__main__":
    main()
