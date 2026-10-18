@echo off
title SLEEP2K - AI Voice & Subtitles Online Server
color 0A
chcp 65001 >nul
cd /d "%~dp0"
python run_online.py
pause
