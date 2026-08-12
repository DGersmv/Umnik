@echo off
rem Proverka ustanovki. Zapuskaetsya PERVOY na novoy mashine.
rem Nichego ne menyaet i ne kachaet - tolko smotrit, chto na meste.

set UMNIK=%~dp0

set PYDIR=
for /d %%D in ("%UMNIK%python\WPy64-*") do set PYDIR=%%D
if "%PYDIR%"=="" (
  echo.
  echo   [NET] Ne nayden WinPython v %UMNIK%python\
  echo         Papka skopirovana ne polnostyu.
  echo.
  pause
  exit /b 1
)

set PY=%PYDIR%\python\python.exe
if not exist "%PY%" (
  echo   [NET] Ne nayden %PY%
  pause
  exit /b 1
)

set HF_HOME=%UMNIK%models

"%PY%" "%UMNIK%scripts\proverka.py"

echo.
pause
