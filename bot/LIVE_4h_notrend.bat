@echo off
REM  SNRZ on the 240-minute chart, REAL ORDERS, with "Trend is King" turned OFF.
REM
REM  The trend filter is the single biggest reason a setup does not fire -- 31%%
REM  of the times price stood in a zone and nothing happened. Without it there
REM  are roughly FOUR times as many trades, each measured slightly worse:
REM  median +0.024 -> +0.002 over 83 days.
call "%~dp0START_BOT.bat" --tf 240 --live --no-trend
