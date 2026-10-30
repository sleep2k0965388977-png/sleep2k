@echo off
chcp 65001 >nul
title SLEEP2K - TU DONG SUA LOI & KHOI PHUC CHUAN GOC
cls
cd /d "D:\sleep2k-deploy"
python auto_heal.py
pause
