@echo off
rem ============================================================
rem  Screen Recorder - build single-file exe (ASCII-safe)
rem  Usage: run build.bat from command prompt / double-click
rem ============================================================
setlocal
cd /d "%~dp0"

echo [1/2] PyInstaller build...
".venv\Scripts\python.exe" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name ScreenRecorder ^
    --icon assets\icon.ico ^
    --add-data "assets;assets" ^
    --collect-all av ^
    --collect-all dxcam ^
    --collect-data sounddevice ^
    main.py
if errorlevel 1 (
    echo BUILD FAILED
    exit /b 1
)

echo [2/2] selftest...
"dist\ScreenRecorder.exe" --selftest
set ST=%errorlevel%
echo selftest exit code = %ST%
if "%ST%"=="0" (
    echo DONE: %cd%\dist\ScreenRecorder.exe
) else (
    echo SELFTEST FAILED, see %%TEMP%%\screenrecord_selftest.log
)
exit /b %ST%
