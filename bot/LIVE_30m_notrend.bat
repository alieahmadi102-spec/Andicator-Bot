@echo off
REM ===========================================================================
REM  SNRZ -- 30 minute chart, REAL ORDERS.
REM
REM  Double-click this file. It checks the things that actually go wrong,
REM  names the one that failed, and holds the window open so you can read it.
REM
REM  The bot prints the account and waits for Enter before it sends anything,
REM  so a double-click on its own cannot place a trade.
REM ===========================================================================

setlocal
cd /d "%~dp0"
title SNRZ 30 minute chart  (trend filter OFF)

echo.
echo   SNRZ -- 30 minute chart  (trend filter OFF)
echo   ------------------------------------

REM --- 1) does python actually RUN? ------------------------------------------
REM  "where python" is not enough: Windows ships a Microsoft Store stub called
REM  python.exe that exists, is on PATH, and does nothing but open the Store.
python -c "print(1)" >nul 2>&1
if errorlevel 1 goto :no_python
for /f "delims=" %%V in ('python --version 2^>^&1') do echo   [ok] %%V

REM --- 2) 64-bit? the MetaTrader5 package needs it ---------------------------
python -c "import platform,sys; sys.exit(0 if platform.architecture()[0]=='64bit' else 1)" >nul 2>&1
if errorlevel 1 goto :not_64bit
echo   [ok] 64-bit

REM --- 3) the MetaTrader5 package --------------------------------------------
python -c "import MetaTrader5" >nul 2>&1
if not errorlevel 1 goto :have_pkg
echo   ... installing the MetaTrader5 package, one moment
python -m pip install --quiet --upgrade pip
python -m pip install --quiet MetaTrader5
python -c "import MetaTrader5" >nul 2>&1
if errorlevel 1 goto :no_pkg
:have_pkg
echo   [ok] MetaTrader5 package

REM --- 4) the terminal itself -------------------------------------------------
REM  Two names because 32-bit terminals exist. Written with && rather than
REM  nested ifs: inside a parenthesised block cmd expands errorlevel at PARSE
REM  time, so a nested "if errorlevel" there reads the value from before the
REM  block ever ran.
set "MT5RUN="
tasklist /FI "IMAGENAME eq terminal64.exe" 2>nul | find /I "terminal64.exe" >nul && set "MT5RUN=1"
tasklist /FI "IMAGENAME eq terminal.exe"   2>nul | find /I "terminal.exe"   >nul && set "MT5RUN=1"
if not defined MT5RUN goto :no_mt5
echo   [ok] MetaTrader 5 is running
echo.

REM --- 5) go ------------------------------------------------------------------
REM  Extra options can be typed after the file name, e.g.
REM      LIVE_30m_notrend.bat --symbol XAUUSD.m
python mt5_runner.py --tf 30 --no-trend --live %*
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

:not_64bit
echo.
echo   [X] This is 32-bit Python. The MetaTrader5 package needs 64-bit.
echo       Install the "Windows installer (64-bit)" from python.org.
echo.
pause
exit /b 1

:no_pkg
echo.
echo   [X] pip could not install MetaTrader5.
echo       Run this to see the real error:
echo           python -m pip install MetaTrader5
echo.
pause
exit /b 1

:no_mt5
echo.
echo   [X] MetaTrader 5 does not look like it is running.
echo       Open it, log in to the account you want, and try again.
echo       The bot talks to the terminal -- it cannot log in by itself.
echo.
pause
exit /b 1

:done
echo.
echo ============================================================
echo   The bot has stopped.
echo   If that was an error, the reason is printed just above.
echo ============================================================
echo.
pause
