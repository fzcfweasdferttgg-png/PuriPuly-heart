@echo off
setlocal enabledelayedexpansion
set count=0
echo Cleaning __pycache__ in app\src ...
echo.
for /d /r "%~dp0app\src" %%d in (__pycache__) do (
    if exist "%%d" (
        echo   %%d
        rd /s /q "%%d"
        set /a count+=1
    )
)
echo.
if !count! equ 0 (
    echo Nothing to clean.
) else (
    echo Deleted !count! directories.
)
echo.
echo Press any key to close...
pause >nul
