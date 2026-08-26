@echo off
REM ===========================================================================
REM  SNRZ -- backtest. Replays saved candles through the SAME rules the live
REM  bot uses and prints how the setups resolved.
REM
REM  This places NO orders and does not talk to MetaTrader at all. It only
REM  reads the CSV files in the data folder.
REM
REM  The window stays open at the end whatever happens, so an error can be
REM  read instead of vanishing.
REM ===========================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"
title SNRZ backtest

echo.
echo   SNRZ -- backtest
echo   ------------------------------------

REM --- 1) does python actually RUN? ------------------------------------------
REM  "where python" is not enough: Windows ships a Microsoft Store stub called
REM  python.exe that exists, is on PATH, and does nothing but open the Store.
python -c "print(1)" >nul 2>&1
if errorlevel 1 goto :no_python
for /f "delims=" %%V in ('python --version 2^>^&1') do echo   [ok] %%V

REM --- 2) is there any data to replay? ---------------------------------------
REM  The data folder is NOT part of the download -- candle files are large and
REM  belong to your own broker. On a fresh copy it is empty, and the old
REM  backtest died on a missing file the instant it started, which by
REM  double-click looked like a crash. Check first and say what to do.
set "DATA=..\data"
if not exist "%DATA%" mkdir "%DATA%"

set "FILES="
set "COUNT=0"
for %%F in ("%DATA%\*.csv") do (
    set "FILES=!FILES! "%%F""
    set /a COUNT+=1
)
if "%COUNT%"=="0" goto :no_data
echo   [ok] %COUNT% candle file(s) in the data folder
echo.

REM --- 3) go ------------------------------------------------------------------
REM  --spread 0.14 charges the broker's real bid/ask gap to every trade. Change
REM  it to the spread your own Market Watch shows on gold; leaving it out
REM  flatters every result, badly on the 1 minute chart.
REM
REM  The run is split in two on purpose:
REM    train = the first 70%% of the candles -- the part the rules were chosen on
REM    test  = the last 30%%  -- never used to choose anything, so it is the
REM            only honest estimate of what the rules do on candles they have
REM            not seen. Judge the bot on the TEST line.
echo   TRAIN (first 70%% -- the rules were chosen on these, so they flatter)
echo   ----------------------------------------------------------------------
python backtest.py %FILES% --spread 0.14 --split train
echo.
echo   TEST (last 30%% -- never used to choose anything. THIS is the honest one)
echo   ----------------------------------------------------------------------
python backtest.py %FILES% --spread 0.14 --split test
echo.
echo   green%% = reached a take profit.  E = average result per trade, in
echo   multiples of the amount risked. E above 0 means the rules made money
echo   on those candles, after the spread.
goto :done

:no_python
echo.
echo   [X] Python is not installed, or PATH points at the Microsoft Store stub.
echo.
echo       Get it from  https://www.python.org/downloads/windows/
echo       Pick "Windows installer (64-bit)".
echo       During setup TICK "Add python.exe to PATH", then reopen cmd.
echo.
pause
exit /b 1

:no_data
echo   [X] There are no candle files yet.
echo.
echo       The data folder is empty:
echo           %~dp0..\data
echo.
echo       Export the candles from MetaTrader 5 -- it takes a minute:
echo.
echo         1. In MT5:  View ^> Symbols        (or press Ctrl+U)
echo         2. Pick the symbol you trade      (XAUUSD, XAUUSDm, GOLD ...)
echo         3. Open the "Bars" tab at the top
echo         4. Choose the period (M1, M5, M15 ...) and the dates, press Request
echo         5. Press "Export Bars" and save into the data folder above as
echo                XAUUSDM5.csv        (M1 -^> XAUUSDM1.csv, and so on)
echo.
echo       The file NAME is what tells the backtest which chart it is, so keep
echo       the M1 / M5 / M15 / M30 / H1 / H4 ending exactly.
echo.
echo       Then double-click this file again.
echo.
pause
exit /b 1

:done
echo.
echo ============================================================
echo   Backtest finished.
echo   If that was an error, the reason is printed just above.
echo ============================================================
echo.
pause
