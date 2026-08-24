@echo off
REM  SNRZ on the 4 hours chart -- DRY RUN, nothing is sent to the broker.
REM  Double-click it. For real orders use LIVE_4h.bat instead.
call "%~dp0START_BOT.bat" --tf 240
