@echo off
REM  SNRZ on the 1 minute chart -- REAL ORDERS.
REM
REM  The bot still prints the account and waits for Enter before it sends
REM  anything, so a double-click on its own cannot place a trade.
call "%~dp0START_BOT.bat" --tf 1 --live
