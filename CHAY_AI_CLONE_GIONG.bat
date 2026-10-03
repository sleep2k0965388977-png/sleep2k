@echo off
chcp 65001 >nul
title SLEEP2K - KET NOI AI CLONE GIONG NOI
cls
echo ================================================================
echo   SLEEP2K TTS - HE THONG KET NOI AI CLONE GIONG NOI
echo   Tan dung 16GB RAM tren may de nhan ban giong chuan 100%%
echo ================================================================
echo.
echo Dang khoi dong AI Worker va duong ham ket noi...
echo.
python "%~dp0run_clone_system.py"
pause
