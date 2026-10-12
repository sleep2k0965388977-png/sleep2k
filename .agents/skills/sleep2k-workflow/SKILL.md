---
name: sleep2k-workflow
description: Cẩm nang hướng dẫn đầy đủ về logic kiến trúc, quy trình sửa mã nguồn qua Git, quy trình deploy lên Railway, và cách thiết lập hệ thống Sleep2K TTS & AI Voice Clone khi chuyển sang máy tính mới.
---

# 🚀 SLEEP2K TTS & AI CLONE - CẨM NANG TOÀN DIỆN VỀ KIẾN TRÚC, GIT & DEPLOY

Tài liệu này được biên soạn để bất kỳ khi nào bạn chuyển sang máy tính mới hoặc cần bảo trì, bạn (hoặc AI) đều nắm rõ 100% logic vận hành, cách sửa code, cách đẩy lên Git và deploy lên Railway mà không bị lỗi.

---

## 1. 🏗️ KIẾN TRÚC TỔNG THỂ & LOGIC HOẠT ĐỘNG

Hệ thống Sleep2K hoạt động theo mô hình **Hybrid Cloud + Local Worker**:

```
[ Máy tính cá nhân của bạn ]                        [ Cloud Railway ]
┌─────────────────────────────┐                    ┌────────────────────────────┐
│ 1. Vieneu AI Model (5005)   │                    │ Web App Flask (app.py)     │
│    (Chạy local_clone_worker)│                    │ https://sleep2k.up.railway.app
│              │              │                    │                            │
│ 2. Cloudflare Tunnel        │                    │ API: /api/set_clone_worker │
│    (Sinh ra trycloudflare)  │                    │ API: /api/clone_worker_st. │
│              │              │                    │                            │
│ 3. run_clone_system.py      │── Tự động POST URL ─> Lưu URL & Heartbeat       │
│    (Tự động kết nối 1-click)│                    │                            │
└─────────────────────────────┘                    └────────────────────────────┘
```

### Chi tiết các thành phần:
1. **Web App trên Railway (`https://sleep2k.up.railway.app`)**:
   - Chạy ứng dụng Flask từ file [app.py](file:///D:/sleep2k-deploy/app.py).
   - Đảm nhiệm: Giao diện web, Text-to-Speech bằng Edge-TTS (siêu nhanh, miễn phí), YouTube Speech-to-Text tách file âm thanh streaming từng 60s, quản lý thẻ giọng.
   - Khi người dùng cần **Nhân bản giọng (Clone Voice)**: Server Railway sẽ chuyển file vocal mẫu và văn bản tới Worker URL đang kết nối.

2. **Local Worker trên máy tính (`local_clone_worker.py`)**:
   - Sử dụng model AI mã nguồn mở chất lượng cao **VieNeu** (v3 Nano, 24kHz Studio).
   - Lắng nghe tại cổng nội bộ `http://127.0.0.1:5005`.
   - Nhận diện âm sắc giọng nói từ đoạn vocal 5-15s và tổng hợp âm thanh giống giọng thật 100%.

3. **Cơ chế Tự Động Kết Nối (Zero-Click Auto Connection)**:
   - File [run_clone_system.py](file:///D:/sleep2k-deploy/run_clone_system.py) đảm nhiệm toàn bộ quy trình:
     + Bật worker cổng 5005.
     + Bật Cloudflare Tunnel bảo mật ra ngoài internet.
     + Tự động đọc URL tunnel vừa sinh ra (ví dụ: `https://xxxx.trycloudflare.com`).
     + Tự động gửi request POST tới `https://sleep2k.up.railway.app/api/set_clone_worker_url` kèm bảo mật `X-API-Key: sleep2k_clone_2024`.
     + Duy trì heartbeat mỗi 20 giây để giữ kết nối luôn xanh (`🟢 Đã kết nối tự động`).
   - Người dùng chỉ cần click đúp vào file `CHAY_AI_CLONE_GIONG.bat` trên Desktop là xong, không cần copy hay dán link gì cả.

---

## 2. 📂 CẤU TRÚC THƯ MỤC DỰ ÁN

| Đường dẫn / Tên file | Vai trò |
| :--- | :--- |
| `app.py` | Backend chính của Flask, xử lý toàn bộ API, TTS, YouTube STT, proxy clone |
| `templates/index.html` | Giao diện chính của Web App |
| `local_clone_worker.py` | Server AI Clone VieNeu chạy trên máy tính (cổng 5005) |
| `run_clone_system.py` | Script tự động khởi động Worker, tạo Cloudflare Tunnel và đăng ký với Web |
| `CHAY_AI_CLONE_GIONG.bat` | File shortcut 1-click để khởi động worker trên máy tính |
| `MO_APP_TTS_SLEEP2K.bat` | File shortcut mở web app chạy trực tiếp trên máy (`http://localhost:5000`) |
| `worker_config.json` | File lưu URL worker hiện tại |
| `youtube_cookies.txt` | Cookie YouTube dùng để vượt bot check khi bóc phụ đề video dài |
| `Dockerfile` / `railway.json` | Cấu hình build và deploy container cho Railway |

---

## 3. 🔄 QUY TRÌNH SỬA CODE & QUẢN LÝ GIT

Kho lưu trữ GitHub: `https://github.com/sleep2k0965388977-png/sleep2k.git` (nhánh chính: `main`).

### Bước 1: Mở thư mục dự án trong Terminal
```powershell
cd /d "D:\sleep2k-deploy"
```

### Bước 2: Kéo code mới nhất về trước khi sửa
```powershell
git pull origin main
```

### Bước 3: Kiểm tra trạng thái các file đã chỉnh sửa
```powershell
git status
```

### Bước 4: Lưu thay đổi và Commit
```powershell
git add .
git commit -m "Mô tả tính năng hoặc lỗi bạn vừa sửa"
```

### Bước 5: Đẩy lên GitHub
```powershell
git push origin main
```
> ⚡ **Lưu ý:** Ngay khi bạn gõ `git push origin main`, Railway đã được cài đặt tự động lắng nghe GitHub nên sẽ **TỰ ĐỘNG BUILD VÀ DEPLOY BẢN MỚI** ngay lập tức mà bạn không cần thao tác thêm.

---

## 4. 🚢 QUY TRÌNH DEPLOY LÊN RAILWAY

Có 2 cách deploy:

### Cách 1: Tự động (Khuyên dùng)
- Bạn chỉ cần thực hiện `git push origin main`.
- Vào trang quản lý: `https://railway.com` -> Mở dự án `sleep2k-tts` để xem tiến trình build.
- Thông thường mất từ 1-2 phút là bản cập nhật tự động lên sóng tại:
  `https://sleep2k.up.railway.app`

### Cách 2: Deploy thủ công bằng lệnh `railway cli` (Khi cần cập nhật khẩn cấp)
Nếu bạn vừa sửa code trên máy mà muốn đẩy thẳng lên Railway không chờ GitHub:
```powershell
# 1. Di chuyển vào thư mục dự án
cd /d "D:\sleep2k-deploy"

# 2. Đẩy trực tiếp mã nguồn lên Railway
railway up --detach

# 3. Xem trạng thái triển khai
railway status
```

---

## 5. 💻 HƯỚNG DẪN THIẾT LẬP KHI ĐỔI SANG MÁY TÍNH MỚI

Khi bạn sang máy tính khác (ở văn phòng, máy ở nhà hoặc laptop), thực hiện lần lượt các bước sau:

### Bước 1: Cài đặt các công cụ nền tảng
1. **Python 3.10 hoặc 3.11 hoặc 3.12** (Lúc cài tích chọn ✅ *Add Python to PATH*).
2. **Git for Windows**: Tải từ `https://git-scm.com/download/win`.
3. **Cloudflared**:
   - Tải file `cloudflared-windows-amd64.exe` từ GitHub của Cloudflare.
   - Đặt tại đường dẫn: `C:\Program Files (x86)\cloudflared\cloudflared.exe` (hoặc đặt cùng thư mục dự án và thêm vào PATH).
4. **FFmpeg**: Cài đặt FFmpeg để cắt ghép audio.

### Bước 2: Clone dự án về máy mới
Mở PowerShell hoặc Command Prompt và chạy:
```powershell
cd /d "D:\"
git clone https://github.com/sleep2k0965388977-png/sleep2k.git sleep2k-deploy
cd sleep2k-deploy
```

### Bước 3: Cài đặt các thư viện Python
```powershell
pip install -r requirements.txt
pip install flask requests soundfile vieneu torch torchaudio imageio-ffmpeg yt-dlp edge-tts
```

### Bước 4: Tạo lối tắt ngoài Desktop
Tạo một file `CHAY_AI_CLONE_GIONG.bat` ngoài màn hình Desktop với nội dung:
```bat
@echo off
chcp 65001 >nul
title SLEEP2K - KET NOI AI CLONE GIONG NOI
cls
echo ================================================================
echo   SLEEP2K TTS - HE THONG KET NOI AI CLONE GIONG NOI
echo   TU DONG KET NOI VOI WEB - KHONG CAN DAN URL THU CONG!
echo ================================================================
echo.
cd /d "D:\sleep2k-deploy"
python run_clone_system.py
pause
```

### Bước 5: Sử dụng
1. Mở trình duyệt vào `https://sleep2k.up.railway.app`.
2. Click đúp vào file `CHAY_AI_CLONE_GIONG.bat` trên Desktop.
3. Khi màn hình console hiện:
   `🎉 KẾT NỐI TỰ ĐỘNG THÀNH CÔNG VỚI WEB RAILWAY!`
   `🟢 Trạng thái Web: ONLINE`
4. Trở lại web, bạn sẽ thấy đèn trạng thái chuyển sang xanh và có thể tạo giọng clone ngay lập tức!
