@echo off
chcp 65001 >nul
title SLEEP2K - KET NOI AI CLONE GIONG NOI
cls
echo ================================================================
echo   SLEEP2K TTS - HE THONG KET NOI AI CLONE GIONG NOI
echo   Tan dung RAM & GPU tren may de nhan ban giong chuan 100%%
echo   TU DONG KET NOI VOI WEB - KHONG CAN DAN URL THU CONG!
echo ================================================================
echo.
cd /d "%~dp0"
if exist "run_clone_system.py" (
    python run_clone_system.py
) else (
    cd /d "D:\tune chuyển văn bản thành giọng nói"
    python run_clone_system.py
)
pause
