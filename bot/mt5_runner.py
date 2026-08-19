"""
MT5 live runner (skeleton) — connects the SNRZ engine to MetaTrader 5.

Requirements (Windows, or Linux+Wine):
    pip install MetaTrader5

هشدار: این یک اسکلت اولیه است. قبل از پول واقعی، حتماً روی حساب دمو تست کن.
"""
from __future__ import annotations

import time

try:
    import MetaTrader5 as mt5
except ImportError:  # keeps the repo importable on non-Windows dev machines
    mt5 = None

from snrz_core import Candle, Config, SnrzEngine

SYMBOL = "XAUUSD"
TIMEFRAME_MIN = 15          # M15 for confirmation per the course
RISK_PCT = 1.0              # max 1% risk per trade (book rule)
MAGIC = 20260819


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


def place(signal, symbol: str):
    side = mt5.ORDER_TYPE_BUY if signal.side == "buy" else mt5.ORDER_TYPE_SELL
    tick = mt5.symbol_info_tick(symbol)
    price = tick.ask if signal.side == "buy" else tick.bid
    volume = lots_for_risk(symbol, abs(price - signal.sl), RISK_PCT)
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": side,
        "price": price,
        "sl": signal.sl,
        "tp": signal.tp1,
        "deviation": 20,
        "magic": MAGIC,
        "comment": f"SNRZ {signal.kind} {signal.zone}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    res = mt5.order_send(req)
    print("order_send:", res)


def main():
    if mt5 is None:
        raise SystemExit("MetaTrader5 package not installed (Windows only): pip install MetaTrader5")
    if not mt5.initialize():
        raise SystemExit(f"MT5 init failed: {mt5.last_error()}")

    tf = getattr(mt5, f"TIMEFRAME_M{TIMEFRAME_MIN}")
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
            place(sig, SYMBOL)


if __name__ == "__main__":
    main()
