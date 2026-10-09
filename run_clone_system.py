import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import subprocess, time, re, os, requests
from pathlib import Path

RAILWAY_URL = "https://sleep2k.up.railway.app"
CLOUDFLARED = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"
WORKER_SCRIPT = Path(__file__).parent / "local_clone_worker.py"

print("=" * 65)
print("🚀 HỆ THỐNG TỰ ĐỘNG KẾT NỐI AI CLONE WORKER (16GB RAM)")
print("=" * 65)

# 1. Start local worker on port 5005
print("\n[1/3] Khởi động AI Clone Worker trên máy tính cá nhân...")
worker_proc = subprocess.Popen(
    [sys.executable, str(WORKER_SCRIPT)],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding='utf-8',
    errors='replace',
    bufsize=1
)

# Wait for worker to be ready
ready = False
for _ in range(30):
    try:
        r = requests.get("http://localhost:5005/health", timeout=1)
        if r.status_code == 200:
            ready = True
            break
    except Exception:
        pass
    time.sleep(1)

if not ready:
    print("❌ Lỗi: Không thể khởi động worker.")
    worker_proc.terminate()
    sys.exit(1)

print("✅ Worker nội bộ đã sẵn sàng tại http://localhost:5005!")

# 2. Start Cloudflare Tunnel
print("\n[2/3] Mở đường hầm bảo mật Cloudflare Tunnel...")
tunnel_proc = subprocess.Popen(
    [CLOUDFLARED, "tunnel", "--url", "http://localhost:5005"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding='utf-8',
    errors='replace',
    bufsize=1
)

tunnel_url = None
t0 = time.time()
while time.time() - t0 < 25:
    line = tunnel_proc.stderr.readline()
    if not line:
        time.sleep(0.2)
        continue
    match = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line)
    if match:
        tunnel_url = match.group(0)
        break

if not tunnel_url:
    print("❌ Lỗi: Không lấy được URL Cloudflare Tunnel.")
    tunnel_proc.terminate()
    worker_proc.terminate()
    sys.exit(1)

print(f"✅ Đường hầm bảo mật công khai: {tunnel_url}")

# 3. Auto-register URL to Railway App
print("\n[3/3] Tự động liên kết URL Worker với Web App Railway...")
set_resp = requests.post(
    f"{RAILWAY_URL}/api/set_clone_worker_url",
    headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
        "Referer": f"{RAILWAY_URL}/",
        "Origin": RAILWAY_URL
    },
    json={"worker_url": tunnel_url},
    timeout=15
)

if set_resp.status_code == 200 and set_resp.json().get("status") == "success":
    print("\n" + "=" * 65)
    print("🎉 LIÊN KẾT THÀNH CÔNG VỚI WEB APP RAILWAY!")
    print(f"👉 Trạng thái trên Web: ONLINE 🟢")
    print(f"👉 URL Worker kết nối: {tunnel_url}")
    print(f"👉 Bây giờ bạn có thể vào {RAILWAY_URL} và nhân bản giọng nói thật 100%!")
    print("=" * 65)
    print("\n⚠️ Giữ cửa sổ này mở khi sử dụng app. Nhấn Ctrl+C để dừng khi không cần.")
else:
    print(f"Lỗi liên kết: {set_resp.status_code} - {set_resp.text}")

last_ping = time.time()
try:
    while True:
        time.sleep(2)
        if time.time() - last_ping > 25:
            last_ping = time.time()
            try:
                requests.post(
                    f"{RAILWAY_URL}/api/set_clone_worker_url",
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
                        "Referer": f"{RAILWAY_URL}/",
                        "Origin": RAILWAY_URL
                    },
                    json={"worker_url": tunnel_url},
                    timeout=5
                )
            except Exception:
                pass
except KeyboardInterrupt:
    print("\nĐang tắt hệ thống...")
    tunnel_proc.terminate()
    worker_proc.terminate()
    print("Đã tắt an toàn.")

