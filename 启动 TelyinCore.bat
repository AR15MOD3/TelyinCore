@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0" || exit /b 2
set "probe=import sys,sysconfig; assert sys.implementation.name == 'cpython' and sys.version_info[:2] == (3,11) and sysconfig.get_platform() == 'win-amd64'"
set "script=%~dp0core\scripts\windows_acceptance.py"
set "runtime=%~dp0core\.venv\Scripts\python.exe"
if exist "%runtime%" goto use_runtime
set "bundled=%LOCALAPPDATA%\TelyinCore\Python311"
py -3.11-64 -c "%probe%" >nul 2>nul
if not errorlevel 1 goto use_py
set "runtime=%bundled%\python.exe"
if exist "%runtime%" goto check_bundled
set "runtime=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if exist "%runtime%" (
    "%runtime%" -c "%probe%" >nul 2>nul
    if not errorlevel 1 goto use_runtime
)
for /f "delims=" %%P in ('where python.exe 2^>nul ^| findstr /v /i /l /c:"WindowsApps"') do (
    "%%P" -c "%probe%" >nul 2>nul
    if not errorlevel 1 (
        set "runtime=%%P"
        goto use_runtime
    )
)
set "installer=%~dp0runtime\python-3.11.9-amd64.exe"
if not exist "%installer%" goto missing_python
echo.
echo   T E L Y I N   C O R E
echo   ----------------------------
echo   [setup] Installing CPython 3.11 x64...
start "" /wait "%installer%" /passive InstallAllUsers=0 TargetDir="%bundled%" Include_launcher=0 PrependPath=0 Include_pip=1 Include_test=0
if errorlevel 1 goto install_failed
set "runtime=%bundled%\python.exe"
:check_bundled
"%runtime%" -c "%probe%" >nul 2>nul
if errorlevel 1 goto install_failed
goto use_runtime
:use_py
py -3.11-64 -u "%script%" %*
goto finished
:use_runtime
"%runtime%" -u "%script%" %*
:finished
set "result=%errorlevel%"
if not %result%==0 (
    echo.
    set /p "closed=  Press Enter to close this window..."
)
exit /b %result%
:missing_python
echo   [error] CPython 3.11 x64 was not found. Use the complete package or install Python.
set /p "closed=  Press Enter to close this window..."
exit /b 2
:install_failed
echo   [error] Python setup did not finish. Check the installer result and retry.
set /p "closed=  Press Enter to close this window..."
exit /b 2
