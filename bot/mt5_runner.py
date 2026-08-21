"""
MT5 live runner (skeleton) — connects the SNRZ engine to MetaTrader 5.

Requirements (Windows, or Linux+Wine):
    pip install MetaTrader5

هشدار: این یک اسکلت اولیه است. قبل از پول واقعی، حتماً روی حساب دمو تست کن.
"""
from __future__ import annotations

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


def lots_for_risk(symbol: str, sl_distance: float, risk_pct: float) -> float:
    info = mt5.symbol_info(symbol)
    acc = mt5.account_info()
    risk_money = acc.balance * risk_pct / 100.0
    tick_value = info.trade_tick_value
    tick_size = info.trade_tick_size
    if sl_distance <= 0 or tick_value <= 0:
        return info.volume_min
    loss_per_lot = sl_distance / tick_size * tick_value
    lots = max(info.volume_min, round(risk_money / loss_per_lot / info.volume_step) * info.volume_step)
    return min(lots, info.volume_max)


def place(signal, symbol: str, tf_minutes: int):
    """Book p41/p42: the entry is a LIMIT order resting AT the zone, not a
    market order. signal.price is that zone price — the engine and the
    backtester both assume the fill happens there, so sending a market order
    here would make the live bot trade something the numbers never measured.
    The order expires the same way it does in the backtest."""
    tick = mt5.symbol_info_tick(symbol)
    market = tick.ask if signal.side == "buy" else tick.bid
    volume = lots_for_risk(symbol, abs(signal.price - signal.sl), RISK_PCT)
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
        "tp": signal.tp1,
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
    print(f"warmed up with {len(rates)} candles, waiting for new bars…")

    while True:
        time.sleep(5)
        bars = mt5.copy_rates_from_pos(SYMBOL, tf, 1, 1)  # last CLOSED bar
        if bars is None or len(bars) == 0:
            continue
        b = bars[0]
        if b["time"] == last_time:
            continue
        last_time = b["time"]
        for sig in engine.on_candle(Candle(int(b["time"]), b["open"], b["high"], b["low"], b["close"])):
            print("SIGNAL:", sig)
            place(sig, SYMBOL, TIMEFRAME_MIN)


if __name__ == "__main__":
    main()
