@echo off
REM Navidrome Cleanup - Organize Files Cron Script (Windows)
REM
REM This script runs the organize files script every hour to:
REM - Move completed downloads to appropriate directories
REM - Clean up database entries for deleted files
REM - Maintain proper file organization
REM

REM Set working directory to the project root
cd /d "%~dp0"

REM Log file with timestamp
if not exist "logs" mkdir logs
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set datetime=%%I
set LOG_FILE=logs\organize_files_cron_%datetime:~0,8%.log

echo [%date% %time%] Starting hourly organize files cron job >> "%LOG_FILE%"

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo [%date% %time%] Python not found >> "%LOG_FILE%"
    exit /b 1
)

REM Run the organize files script
echo [%date% %time%] Running organize files script... >> "%LOG_FILE%"

python scripts\organise_files.py --auto-mode >> "%LOG_FILE%" 2>&1
set exit_code=%errorlevel%

if %exit_code% equ 0 (
    echo [%date% %time%] Organize files completed successfully >> "%LOG_FILE%"
) else (
    echo [%date% %time%] Organize files failed with exit code: %exit_code% >> "%LOG_FILE%"
)

REM Clean up old log files (keep last 7 days)
forfiles /p logs /m organize_files_cron_*.log /d -7 /c "cmd /c del @path" 2>nul

echo [%date% %time%] Hourly organize files cron job completed >> "%LOG_FILE%"