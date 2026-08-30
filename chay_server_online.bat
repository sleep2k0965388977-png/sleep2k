@echo off
title SLEEP2K - AI Voice & Subtitles Online Server (Cloudflare Tunnel)
color 0A
chcp 65001 >nul
cls
echo ======================================================================
echo           SLEEP2K AI VOICE & SPEECH-TO-TEXT ONLINE SERVER
echo ======================================================================
echo.
echo  [*] Đang khởi động máy chủ SLEEP2K Backend trên máy tính...
start /b "" python app.py
timeout /t 2 /nobreak >nul
echo.
echo  [*] Đang kết nối đường truyền Cloudflare Tunnel tốc độ cao...
echo.
echo ======================================================================
echo  ĐƯỜNG LINK ONLINE HTTPS CỦA BẠN SẼ XUẤT HIỆN Ở BÊN DƯỚI:
echo  (Bạn có thể sao chép link .trycloudflare.com gửi cho bất kỳ ai dùng)
echo ======================================================================
echo.
tools\cloudflared.exe tunnel --url http://127.0.0.1:5000
pause
