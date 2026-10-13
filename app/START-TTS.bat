@echo off
title NovaVoice TTS Platform
cd /d "%~dp0"
echo =======================================================
echo   NovaVoice TTS Platform - Natural neural voices
echo   Uzbek (Madina/Sardor) + 150 languages - Edge Neural
echo =======================================================
echo.
echo [1/2] Checking dependencies...
pip install -r requirements.txt --quiet
echo.
echo [2/2] Starting server: http://127.0.0.1:5005
echo Opening browser automatically...
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:5005"
python app.py
pause
