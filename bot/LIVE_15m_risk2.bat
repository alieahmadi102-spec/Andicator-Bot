@echo off
REM  SNRZ on the 15-minute chart, REAL ORDERS, risking 2%% per trade.
REM
REM  Why this file exists: on a small account the smallest lot the broker sells
REM  already risks more than 1%%, so at the default every trade is refused. The
REM  bot now prints the exact risk each refused trade needed -- pick the file
REM  that matches.
REM
REM  Measured on this size of account, 83 days of real XAUUSD:
REM      1%% ->   0 trades      2%% ->  13 trades, +$2
REM      3%% ->  75 trades, flat, 23%% drawdown
REM      5%% -> 617 trades, -$23, 75%% drawdown
REM  Bigger risk is not more profit here -- it is a faster ride to the floor.
call "%~dp0START_BOT.bat" --tf 15 --live --risk 2
