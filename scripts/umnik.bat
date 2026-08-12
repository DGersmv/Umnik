@echo off
rem Zapusk okna "Umnik". Fayl lezhit v korne papki Umnik.
rem Vse puti otschityvayutsya ot mesta etogo fayla: bukva diska ne vpisana.
rem Kirillicy v fayle net namerenno - komandnyy interpretator Windows portit ee.

set UMNIK=%~dp0

rem Ischem WinPython, ne vpisyvaya nomer versii
set PYDIR=
for /d %%D in ("%UMNIK%python\WPy64-*") do set PYDIR=%%D
if "%PYDIR%"=="" (
  echo OSHIBKA: ne nayden WinPython v %UMNIK%python\
  pause
  exit /b 1
)

set PY=%PYDIR%\python\python.exe
if not exist "%PY%" (
  echo OSHIBKA: ne nayden %PY%
  pause
  exit /b 1
)

rem Kuda klast vesa modeley - vnutr papki, a ne na disk C:
set HF_HOME=%UMNIK%models

"%PY%" "%UMNIK%scripts\gui.py"

if errorlevel 1 (
  echo.
  echo Okno zavershilos s oshibkoy. Tekst vyshe.
  pause
)
