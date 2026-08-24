@echo off
REM  SNRZ on the 1 hour chart -- DRY RUN, nothing is sent to the broker.
REM  Double-click it. For real orders use LIVE_1h.bat instead.
call "%~dp0START_BOT.bat" --tf 60
