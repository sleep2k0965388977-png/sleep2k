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
if exist "D:\sleep2k-deploy\run_clone_system.py" (
    cd /d "D:\sleep2k-deploy"
    python run_clone_system.py
) else (
    cd /d "D:\tự động tạo gmail"
    python run_clone_system.py
)
pause
