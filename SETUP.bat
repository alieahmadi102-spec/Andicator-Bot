@echo off
REM ===========================================================================
REM  SNRZ -- download / update, in pure cmd.
REM
REM  Uses curl and tar, both shipped with Windows 10 (1803+) and Windows 11.
REM  No PowerShell, no git, no admin rights.
REM
REM  Puts the project on the Desktop, copies the MT5 indicator into the
REM  terminal's own folder, and opens the bot folder when it is done.
REM  Safe to run again any time -- it replaces the old copy with the latest.
REM ===========================================================================

setlocal
title SNRZ setup

set "URL=https://github.com/alieahmadi102-spec/Andicator-Bot/archive/refs/heads/claude/project-repository-question-x3o8f0.zip"
set "DEST=%USERPROFILE%\Desktop\snrz"
set "ZIP=%TEMP%\snrz.zip"

echo.
echo   SNRZ setup
echo   ----------

where curl >nul 2>&1
if errorlevel 1 goto :no_curl
where tar  >nul 2>&1
if errorlevel 1 goto :no_curl

echo   downloading ...
curl -L --fail --silent --show-error -o "%ZIP%" "%URL%"
if errorlevel 1 goto :no_net

if exist "%DEST%" rmdir /s /q "%DEST%"
mkdir "%DEST%"
echo   extracting ...
tar -xf "%ZIP%" -C "%DEST%"
if errorlevel 1 goto :bad_zip
del "%ZIP%" >nul 2>&1

REM the archive unpacks into one folder whose name carries the branch
set "SRC="
for /d %%D in ("%DEST%\*") do set "SRC=%%D"
if not defined SRC goto :bad_zip

REM --- the MT5 indicator, into whichever terminal folder has an MQL5 tree ----
set "PUTIND="
for /d %%T in ("%APPDATA%\MetaQuotes\Terminal\*") do if exist "%%T\MQL5\Indicators" (
    copy /y "%SRC%\mt5\SNRZ_Indicator.mq5" "%%T\MQL5\Indicators\" >nul
    set "PUTIND=%%T\MQL5\Indicators"
)

echo.
echo   [ok] project     %SRC%
echo   [ok] bot folder  %SRC%\bot
if defined PUTIND echo   [ok] indicator   %PUTIND%
if not defined PUTIND echo   [--] MT5 folder not found -- copy mt5\SNRZ_Indicator.mq5 by hand
echo.
echo   Ready-made launchers, one per timeframe -- just double-click one:
echo.
echo       RUN_1m.bat    RUN_5m.bat    RUN_15m.bat        dry run, nothing sent
echo       RUN_30m.bat   RUN_1h.bat    RUN_4h.bat
echo.
echo       LIVE_5m.bat   LIVE_15m.bat  ...                REAL orders
echo.
echo   The bot sizes itself from the account balance -- no risk setting to
echo   pick. It scales the lot down as the stop widens, and where the
echo   broker's minimum lot leaves nothing to scale it takes the trade only
echo   while the forced risk stays under 3%%, skipping the rest.
echo.
echo   Simulated on 156 days of real XAUUSD, M5, sizing itself:
echo       $50   -^> no trades fit
echo       $115  -^> 256 trades, +149%%, worst drawdown 25%%
echo       $300  -^> 867 trades, +192%%, worst drawdown 41%%
echo   M1 was measured NEGATIVE at every balance -- use RUN_5m / LIVE_5m.
echo.
echo   Next:
echo     1. open MetaTrader 5 and log in
echo     2. double-click RUN_5m.bat in the folder that just opened
echo.

explorer "%SRC%\bot"
pause
exit /b 0

:no_curl
echo.
echo   [X] curl or tar is missing. They ship with Windows 10 build 1803 and
echo       later. On an older Windows, download the zip in a browser instead:
echo       %URL%
echo.
pause
exit /b 1

:no_net
echo.
echo   [X] The download failed -- no internet, or GitHub is blocked here.
echo       Try opening this in a browser to check:
echo       %URL%
echo.
pause
exit /b 1

:bad_zip
echo.
echo   [X] The archive did not unpack. Delete "%ZIP%" and run this again.
echo.
pause
exit /b 1
