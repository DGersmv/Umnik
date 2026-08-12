@echo off
rem Universalnaya zapuskalka. Zadaet PY i HF_HOME i zapuskaet lyuboy skript
rem iz papki scripts. Lezhit v korne papki Umnik.
rem
rem Primery:
rem     F:\Umnik\py.bat rollup.py "F:\Umnik\data\parsed\facts.jsonl"
rem     F:\Umnik\py.bat search.py "F:\Umnik\db"
rem     F:\Umnik\py.bat evaluate_answers.py "F:\Umnik\db" "F:\Umnik\scripts\voprosy.txt"

setlocal enabledelayedexpansion
set UMNIK=%~dp0

set PYDIR=
for /d %%D in ("%UMNIK%python\WPy64-*") do set PYDIR=%%D
if "%PYDIR%"=="" (
  echo OSHIBKA: ne nayden WinPython v %UMNIK%python\
  pause
  exit /b 1
)

set PY=%PYDIR%\python\python.exe
set HF_HOME=%UMNIK%models

if "%~1"=="" (
  echo Ukazhi imya skripta, naprimer:
  echo     py.bat rollup.py "F:\Umnik\data\parsed\facts.jsonl"
  echo.
  echo Dostupnye skripty:
  dir /b "%UMNIK%scripts\*.py"
  pause
  exit /b 1
)

rem Pervyy argument - imya skripta, ostalnye peredayutsya emu.
rem Cherez %* eto sdelat nelzya: kavychki obernuli by vsyo srazu.
set SCRIPT=%~1
shift
set ARGS=
:collect
if "%~1"=="" goto run
set ARGS=!ARGS! "%~1"
shift
goto collect

:run
"%PY%" "%UMNIK%scripts\%SCRIPT%" !ARGS!

if errorlevel 1 pause
endlocal
