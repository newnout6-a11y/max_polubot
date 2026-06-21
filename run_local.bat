@echo off
title MAX Polubot - Run Local
echo ==============================================
echo Running MAX Polubot Locally...
echo ==============================================
if not exist venv\Scripts\python.exe (
    echo Error: Local virtual environment not found!
    pause
    exit /b
)
venv\Scripts\python.exe main.py
pause
