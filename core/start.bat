@echo off
REM TelyinCore launcher -- a thin ASCII shell. The real work is in start.ps1,
REM because cmd.exe uses the OEM codepage (mojibake) while PowerShell is UTF-8.
REM Keep this file ASCII-only and BOM-less.
chcp 65001 >nul
title TelyinCore
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 pause
exit /b %errorlevel%
