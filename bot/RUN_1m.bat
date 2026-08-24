@echo off
REM  SNRZ on the 1 minute chart -- DRY RUN, nothing is sent to the broker.
REM  Double-click it. For real orders use LIVE_1m.bat instead.
call "%~dp0START_BOT.bat" --tf 1
