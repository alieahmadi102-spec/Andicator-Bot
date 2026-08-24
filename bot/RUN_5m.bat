@echo off
REM  SNRZ on the 5 minutes chart -- DRY RUN, nothing is sent to the broker.
REM  Double-click it. For real orders use LIVE_5m.bat instead.
call "%~dp0START_BOT.bat" --tf 5
