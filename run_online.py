import os
import sys
import time
import re
import webbrowser
import subprocess
import atexit

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

os.chdir(os.path.dirname(os.path.abspath(__file__)))

print("=================================================================")
print("      SLEEP2K AI VOICE & SUBTITLES - MAY CHU ONLINE")
print("=================================================================")
print("\n[*] Dang khoi dong Backend Python...")

app_proc = subprocess.Popen([sys.executable, "app.py"])

def cleanup():
    try:
        app_proc.terminate()
    except Exception:
        pass

atexit.register(cleanup)

time.sleep(2)
print("[*] Dang ket noi Cloudflare Tunnel...")

cf_cmd = [os.path.join("tools", "cloudflared.exe"), "tunnel", "--url", "http://127.0.0.1:5000"]
cf_proc = subprocess.Popen(
    cf_cmd,
    stderr=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True,
    encoding="utf-8",
    errors="ignore"
)

def cleanup_all():
    cleanup()
    try:
        cf_proc.terminate()
    except Exception:
        pass

atexit.register(cleanup_all)

tunnel_url = None
start_time = time.time()
while time.time() - start_time < 30:
    line = cf_proc.stderr.readline()
    if not line:
        time.sleep(0.2)
        continue
    m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
    if m:
        tunnel_url = m.group(0)
        break

if tunnel_url:
    print("\n" + "=" * 65)
    print(" >>> DUONG LINK TRUY CAP TRANG WEB ONLINE CUA BAN: <<<")
    print(f"\n     {tunnel_url}\n")
    print("=" * 65)
    
    with open("DUONG_LINK_ONLINE.txt", "w", encoding="utf-8") as f:
        f.write(f"{tunnel_url}\n")
        
    print("\n[*] Dang tu dong mo trinh duyet vao web...")
    try:
        webbrowser.open(tunnel_url)
    except Exception:
        pass
        
    print("\n[*] LUU Y: GIU CUA SO NAY LUON MO DE WEB HOAT DONG ONLINE.")
    print("[*] Nhan Ctrl+C khi muon tat may chu.\n")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Dang tat may chu...")
else:
    print("[!] Khong the tao duong link Cloudflare Tunnel. Vui long kiem tra mang internet.")
    input("\nNhan Enter de thoat...")
