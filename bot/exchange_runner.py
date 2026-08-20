"""
Crypto-exchange live runner (skeleton) — connects the SNRZ engine to any
exchange supported by ccxt (Binance, Bybit, OKX, KuCoin, …).

    pip install ccxt

هشدار: اسکلت اولیه است؛ اول در حالت paper/testnet اجرا کن.
"""
from __future__ import annotations

import time

import ccxt

from snrz_core import Candle, Config, SnrzEngine

EXCHANGE = "binance"
SYMBOL = "BTC/USDT"
# Feed the engine the ANALYSIS timeframe — the book draws zones on 1H/4H/D and
# only monitors below. (The indicators pick this automatically from the chart;
# here it is explicit. Separate lower-TF confirmation is the next step.)
TIMEFRAME = "1h"
RISK_PCT = 1.0
DRY_RUN = True              # True = فقط سیگنال چاپ می‌شود، سفارشی ارسال نمی‌شود


def main():
    ex = getattr(ccxt, EXCHANGE)({
        "enableRateLimit": True,
        # "apiKey": "...", "secret": "...",
    })
    engine = SnrzEngine(Config())

    ohlcv = ex.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=1000)
    for t, o, h, l, c, _ in ohlcv[:-1]:          # last row may be unclosed
        engine.on_candle(Candle(t, o, h, l, c))
    last_time = ohlcv[-2][0]
    print(f"warmed up with {len(ohlcv)-1} candles, polling…")

    while True:
        time.sleep(10)
        rows = ex.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=3)
        closed = rows[-2]                        # last fully closed candle
        if closed[0] == last_time:
            continue
        last_time = closed[0]
        t, o, h, l, c, _ = closed
        for sig in engine.on_candle(Candle(t, o, h, l, c)):
            print("SIGNAL:", sig)
            if DRY_RUN:
                continue
            amount = risk_amount(ex, sig)
            side = "buy" if sig.side == "buy" else "sell"
            order = ex.create_order(SYMBOL, "market", side, amount)
            print("order:", order["id"])
            # SL/TP: بسته به صرافی از stop-market / OCO استفاده کن


def risk_amount(ex, sig) -> float:
    balance = ex.fetch_balance()["USDT"]["free"]
    risk_money = balance * RISK_PCT / 100.0
    sl_dist = abs(sig.price - sig.sl)
    if sl_dist <= 0:
        return 0.0
    return round(risk_money / sl_dist, 6)


if __name__ == "__main__":
    main()
