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
# The CHART timeframe — the one the bot trades on. The analysis timeframe is
# derived from it by the captain's ladder (two rungs up, middle one skipped),
# the same way mt5_runner and both indicators do it.
TIMEFRAME = "1h"
TIMEFRAME_MIN = 60
RISK_PCT = 1.0
DRY_RUN = True              # True = فقط سیگنال چاپ می‌شود، سفارشی ارسال نمی‌شود


def main():
    ex = getattr(ccxt, EXCHANGE)({
        "enableRateLimit": True,
        # "apiKey": "...", "secret": "...",
    })
    # was SnrzEngine(Config()) — with no chart_minutes the engine fell back to
    # a flat htf_mult of 3 instead of following the ladder, so this runner was
    # marking zones on a different timeframe than mt5_runner and the indicators.
    engine = SnrzEngine(Config(chart_minutes=TIMEFRAME_MIN))
    print(f"zones marked on {SnrzEngine.analysis_minutes(TIMEFRAME_MIN)}m, "
          f"refined and traded on {TIMEFRAME_MIN}m")

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
            # کتاب ص۴۱/۴۲: ورود یک اردر LIMIT روی خود زون است، نه مارکت.
            # sig.price همان قیمت زون است — اگر مارکت بزنیم، ربات چیزی را
            # معامله می‌کند که بک‌تست هیچ‌وقت اندازه نگرفته.
            order = ex.create_order(SYMBOL, "limit", sig.side, amount, sig.price)
            print("limit order:", order["id"], "@", sig.price)
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
