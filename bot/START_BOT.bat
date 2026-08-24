@echo off
REM ===========================================================================
REM  SNRZ bot -- double-click this file to start it.
REM
REM  Why a .bat and not the .ps1 directly: Windows refuses to run a .ps1 on
REM  double-click (it opens it in Notepad instead), and a .py that crashes
REM  closes its own window before you can read the error. This does neither --
REM  it launches PowerShell with the execution policy unblocked for this one
REM  run, and holds the window open afterwards so the last message stays on
REM  screen whether it finished or failed.
REM
REM  DRY RUN by default: nothing is sent to the broker. To send real orders you
REM  have to type the command yourself -- see the bottom of this file. That is
REM  deliberate, so a stray double-click can never place a trade.
REM
REM  Want a different timeframe? Right-click this file -> Create shortcut, then
REM  in the shortcut's Properties add the argument at the end of Target:
REM       ...\START_BOT.bat -Tf 60
REM ===========================================================================

setlocal
cd /d "%~dp0"
title SNRZ bot - DRY RUN

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_windows.ps1" %*

echo.
echo ============================================================
echo   The bot has stopped.
echo.
echo   If it stopped because of an error, the reason is printed
echo   just above this box.
echo.
echo   To send REAL orders instead of a dry run, open PowerShell
echo   here and type:
echo       .\run_windows.ps1 -Live
echo ============================================================
echo.
pause
