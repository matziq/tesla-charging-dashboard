@echo off
python "%~dp0dairy_charging.py"
if errorlevel 1 (
    echo Could not start the dashboard. Verify Python and Tkinter are installed.
    pause
)
