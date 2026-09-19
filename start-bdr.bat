@echo off
title BDR Pipeline
cd /d "%~dp0"
echo Starting BDR Pipeline at http://localhost:8503 ...
echo (Close this window to stop the app. Your results are saved on disk and survive restarts.)
start "" http://localhost:8503
.venv\Scripts\python.exe -m streamlit run app\main.py --server.port 8503 --server.headless true
