@echo off
rem Omi Transcriber launcher: starts the server and opens the app.
cd /d "%~dp0"
start "" http://localhost:8090
.venv\Scripts\python server.py %*
