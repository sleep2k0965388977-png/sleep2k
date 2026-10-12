import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import subprocess, time, re, os, requests, json
from pathlib import Path

CLONE_API_KEY = "sleep2k_clone_2024"
RAILWAY_URL = "https://sleep2k.up.railway.app"
LOCAL_APP_URL = "http://localhost:5000"

CLOUDFLARED = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"
if not Path(CLOUDFLARED).exists():
    CLOUDFLARED = "cloudflared"

BASE_DIR = Path(__file__).parent
WORKER_SCRIPT = BASE_DIR / "local_clone_worker.py"
if not WORKER_SCRIPT.exists():
    WORKER_SCRIPT = Path(r"D:\sleep2k-deploy\local_clone_worker.py")

CONFIG_FILE = BASE_DIR / "worker_config.json"
DEPLOY_CONFIG_FILE = Path(r"D:\sleep2k-deploy\worker_config.json")

print("=" * 70)
print("  🚀 SLEEP2K - HỆ THỐNG TỰ ĐỘNG KẾT NỐI AI CLONE WORKER")
print("  ⚡ Tận dụng sức mạnh máy tính để nhân bản giọng đọc chuẩn 100%")
print("  ✨ TỰ ĐỘNG KẾT NỐI - KHÔNG CẦN COPY/DÁN URL THỦ CÔNG")
print("=" * 70)

# 1. Khởi động AI Worker cục bộ trên cổng 5005
print("\n[1/3] Đang khởi động AI Clone Worker trên máy tính...")
worker_proc = subprocess.Popen(
    [sys.executable, str(WORKER_SCRIPT)],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding='utf-8',
    errors='replace',
    bufsize=1
)

# Chờ worker sẵn sàng
ready = False
for _ in range(35):
    try:
        r = requests.get("http://localhost:5005/health", timeout=1)
        if r.status_code == 200:
            ready = True
            break
    except Exception:
        pass
    time.sleep(1)

if not ready:
    print("❌ Lỗi: Không thể khởi động worker local trên cổng 5005.")
    worker_proc.terminate()
    sys.exit(1)

print("✅ Worker nội bộ đã sẵn sàng tại http://localhost:5005!")

# 2. Mở đường hầm bảo mật Cloudflare Tunnel
print("\n[2/3] Đang tạo đường hầm kết nối bảo mật (Cloudflare Tunnel)...")
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
while time.time() - t0 < 30:
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

print(f"✅ Đường hầm công khai đã tạo: {tunnel_url}")

# Lưu vào file config nội bộ
try:
    for cfg in [CONFIG_FILE, DEPLOY_CONFIG_FILE]:
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"worker_url": tunnel_url}, f, indent=2)
except Exception:
    pass

# 3. Tự động đăng ký URL lên Railway & Local App
print("\n[3/3] Đang tự động kết nối và đăng ký với Web App...")

def register_url(target_base):
    try:
        resp = requests.post(
            f"{target_base}/api/set_clone_worker_url",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": CLONE_API_KEY
            },
            json={
                "worker_url": tunnel_url,
                "api_key": CLONE_API_KEY
            },
            timeout=15
        )
        return resp.status_code == 200 and resp.json().get("status") == "success"
    except Exception:
        return False

railway_ok = register_url(RAILWAY_URL)
local_ok = register_url(LOCAL_APP_URL)

print("\n" + "=" * 70)
if railway_ok:
    print("  🎉 KẾT NỐI TỰ ĐỘNG THÀNH CÔNG VỚI WEB RAILWAY!")
    print(f"  🌐 Web App: {RAILWAY_URL}")
    print(f"  🟢 Trạng thái Web: ONLINE (Tự động nhận diện)")
    print(f"  🔗 URL Worker: {tunnel_url}")
    print("  ✨ BẠN KHÔNG CẦN COPY HAY DÁN GÌ NỮA! Web đã kết nối thẳng 100%!")
elif local_ok:
    print("  🎉 KẾT NỐI THÀNH CÔNG VỚI WEB CỤC BỘ (http://localhost:5000)!")
else:
    print(f"  ⚠️ Đang kết nối lại với {RAILWAY_URL}...")
print("=" * 70)
print("\n💡 Giữ cửa sổ này mở trong khi sử dụng tạo giọng. Nhấn Ctrl+C để dừng.")

last_ping = time.time()
try:
    while True:
        time.sleep(2)
        if time.time() - last_ping > 20:
            last_ping = time.time()
            register_url(RAILWAY_URL)
            if local_ok:
                register_url(LOCAL_APP_URL)
except KeyboardInterrupt:
    print("\nĐang tắt an toàn hệ thống...")
    tunnel_proc.terminate()
    worker_proc.terminate()
    print("✅ Đã tắt an toàn.")
