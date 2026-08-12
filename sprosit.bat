@echo off
set UMNIK=%~dp0
set PY=%UMNIK%python\WPy64-3.13.12.0\python\python.exe
set HF_HOME=%UMNIK%models
set PATH=%UMNIK%python\WPy64-3.13.12.0\python\Lib\site-packages\torch\lib;%PATH%
"%PY%" "%UMNIK%scripts\answer.py" "%UMNIK%db" %*