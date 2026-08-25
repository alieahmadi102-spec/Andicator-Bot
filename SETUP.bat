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
echo   Ready-made launchers, one per timeframe -- just double-click one.
echo   All of them send REAL orders to whatever account MetaTrader is logged
echo   into, and all of them print the account and wait for Enter first.
echo.
echo       LIVE_1m.bat   LIVE_5m.bat   LIVE_15m.bat
echo       LIVE_30m.bat  LIVE_1h.bat   LIVE_4h.bat
echo.
echo       LIVE_5m_notrend.bat  ...   the same, ignoring "Trend is King":
echo                                  about 4x the trades, each slightly worse
echo.
echo   DEMO or REAL is decided by the account you log MetaTrader into, NOT by
echo   which file you click. The bot prints which one it found, every time.
echo.
echo   It sizes each trade from the balance on its own -- there is no risk
echo   setting to pick. It targets 1%%, scales the lot down as the stop
echo   widens, and where the broker's smallest lot leaves nothing to scale it
echo   takes the trade only while the forced risk stays under 3%%.
echo.
echo   WHAT BALANCE THIS NEEDS, measured on real XAUUSD, not guessed:
echo   standard gold moves $1 per $1 of stop at the smallest lot (0.01), and
echo   a typical setup stops $5 away on the 5m chart, $1.75 on the 1m. So:
echo       $10, $25, $50   -^> the 3%% ceiling is $0.30/$0.75/$1.50 and almost
echo                          nothing fits. It will sit there taking no trades.
echo       $100            -^> 5m works (+0.30R measured); 1m about break-even
echo       $200 and up     -^> 5m is comfortable, 15m starts to fit
echo   If the balance is too small the bot says so at startup and SEARCHES the
echo   account for a smaller gold contract (XAUUSD.m and the like) that fits.
echo.
echo   Next:
echo     1. open MetaTrader 5 and log in
echo     2. double-click LIVE_5m.bat in the folder that just opened
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
