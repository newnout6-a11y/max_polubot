@echo off
title MAX Polubot - Authentication
echo ==============================================
echo Running MAX Polubot Authentication Helper...
echo ==============================================
if not exist venv\Scripts\python.exe (
    echo Error: Local virtual environment not found! 
    echo Please make sure you have a venv folder in this directory.
    pause
    exit /b
)
venv\Scripts\python.exe auth.py
pause
