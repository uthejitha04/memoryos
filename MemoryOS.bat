@echo off
rem MemoryOS desktop app launcher
cd /d "%~dp0desktop"
start "" /min cmd /c "npx electron ."
