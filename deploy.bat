@echo off
chcp 65001 >nul
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel% equ 0 (
    if "%~1"=="" (
        py -3 tools\deploy_local.py open
    ) else (
        py -3 tools\deploy_local.py %*
    )
) else (
    if "%~1"=="" (
        python tools\deploy_local.py open
    ) else (
        python tools\deploy_local.py %*
    )
)

if errorlevel 1 (
    echo.
    echo 运行失败。请确认已安装 Python 3.10 或更高版本。
    pause
)
