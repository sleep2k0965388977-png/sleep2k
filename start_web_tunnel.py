import subprocess, time, re, sys
from pathlib import Path

CLOUDFLARED = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"

proc = subprocess.Popen(
    [CLOUDFLARED, "tunnel", "--url", "http://localhost:7860"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding='utf-8',
    errors='replace'
)

tunnel_url = None
t0 = time.time()
while time.time() - t0 < 30:
    line = proc.stderr.readline()
    if not line:
        time.sleep(0.2)
        continue
    m = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line)
    if m:
        tunnel_url = m.group(0)
        break

if tunnel_url:
    print(f"WEB_URL={tunnel_url}")
    with open("D:/sleep2k-deploy/web_public_url.txt", "w", encoding="utf-8") as f:
        f.write(tunnel_url)
    # Keep tunnel alive
    while True:
        time.sleep(3600)
else:
    print("FAILED_TO_GET_TUNNEL")

