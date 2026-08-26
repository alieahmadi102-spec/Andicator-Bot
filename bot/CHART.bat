@echo off
REM ===========================================================================
REM  SNRZ -- draw what the ENGINE sees, as a picture.
REM
REM  Same snrz_core the bot trades with, so this is not a description of the
REM  zones, it IS them. Every box carries its exact price range, and a thick
REM  tick marks the candle it was drawn from.
REM
REM  Nothing is sent anywhere and no order is placed. It only reads the CSV
REM  files in the data folder and writes PNG images next to them.
REM ===========================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"
title SNRZ chart

echo.
echo   SNRZ -- draw the zones
echo   ------------------------------------

python -c "print(1)" >nul 2>&1
if errorlevel 1 goto :no_python
for /f "delims=" %%V in ('python --version 2^>^&1') do echo   [ok] %%V

REM matplotlib is what turns the numbers into a picture. Nothing else needs it,
REM so it is installed here rather than in requirements.txt.
python -c "import matplotlib" >nul 2>&1
if not errorlevel 1 goto :have_mpl
echo   ... installing matplotlib, one moment
python -m pip install --quiet matplotlib
python -c "import matplotlib" >nul 2>&1
if errorlevel 1 goto :no_mpl
:have_mpl
echo   [ok] matplotlib

set "DATA=..\data"
if not exist "%DATA%" goto :no_data
set "COUNT=0"
for %%F in ("%DATA%\*.csv") do set /a COUNT+=1
if "%COUNT%"=="0" goto :no_data
echo   [ok] %COUNT% candle file(s)
echo.

REM Last 200 candles of each timeframe. Add --hidden to include the FRESH
REM zones the chart normally leaves off, or --at "2026-08-26 21:00" to rewind
REM to a particular moment:
REM     python chart.py ..\data\XAUUSDM5.csv --at "2026-08-26 21:00"
for %%F in ("%DATA%\*.csv") do (
    echo   drawing %%~nxF ...
    python chart.py "%%F" --bars 200 --out "%DATA%\%%~nF.png"
)

echo.
echo   Done. The pictures are in:
echo       %~dp0..\data
echo.
echo   Each box shows its name and its exact price range, and the thick tick
echo   on its left is the candle it was drawn from. If a zone looks wrong,
echo   read its two numbers off the label -- that is what to report.
goto :done

:no_python
echo.
echo   [X] Python is not installed, or PATH points at the Microsoft Store stub.
echo       Get it from  https://www.python.org/downloads/windows/
echo       Tick "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1

:no_mpl
echo.
echo   [X] pip could not install matplotlib. Run this to see the real error:
echo           python -m pip install matplotlib
echo.
pause
exit /b 1

:no_data
echo   [X] There are no candle files in %~dp0..\data
echo.
echo       In MT5:  View ^> Symbols (Ctrl+U) ^> pick the symbol ^> Bars tab
echo                ^> set the period and dates ^> Request ^> Export Bars
echo       Save as  XAUUSDM5.csv  in the data folder.
echo.
pause
exit /b 1

:done
echo.
echo ============================================================
echo   Finished.
echo ============================================================
echo.
pause
