@echo off
rem Izvlechenie faktov iz pisem.
rem Lezhit v korne papki Umnik. Puti otschityvayutsya ot mesta fayla.
rem
rem Zapusk iz PowerShell ili cmd:
rem     F:\Umnik\fakty.bat --limit 10      probnyy progon na 10 pismah
rem     F:\Umnik\fakty.bat                 vse ostavshiesya pisma
rem
rem Povtornyy zapusk ne peredelyvaet uzhe razobrannoe.

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

"%PY%" "%UMNIK%scripts\extractor.py" ^
  "%UMNIK%data\parsed\letters.jsonl" ^
  "%UMNIK%data\parsed\facts.jsonl" %*

pause
