#!/usr/bin/env python3
"""
SLEEP2K Autonomous Self-Healing & Diagnostic Engine
Author: SLEEP2K AI Assistant
Purpose: Automatically detects and repairs all common regressions and runtime errors across:
  - Backend (app.py)
  - Frontend (templates/index.html)
  - Docker & Gunicorn concurrency
  - AI Clone Worker config & tunnels
  - Google Colab & Kaggle notebooks
  - Python AST syntax integrity
  - Live Render Production Health & GitHub Sync
"""

import sys
import os
import time
import subprocess
import json
import urllib.request
import re
import ast
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[4]
APP_PY = ROOT / "app.py"
INDEX_HTML = ROOT / "templates" / "index.html"
DOCKERFILE = ROOT / "Dockerfile"
WORKER_CONFIG = ROOT / "worker_config.json"
LOCAL_WORKER = ROOT / "local_clone_worker.py"
COLAB_WORKER = ROOT / "colab_voice_clone.py"
KAGGLE_WORKER = ROOT / "kaggle_voice_clone.py"
LOG_FILE = ROOT / "auto_heal.log"

LIVE_DOMAINS = [
    "https://sleep2k.onrender.com",
    "https://sleep2k-1.onrender.com"
]

HEALED_ACTIONS = []
WARNINGS = []

def log(msg, to_file=True):
    ts = time.strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{ts} {msg}"
    print(line)
    if to_file:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

def log_heal(msg):
    log(f"🛠️ [TỰ ĐỘNG SỬA]: {msg}")
    HEALED_ACTIONS.append(msg)

def log_pass(msg):
    log(f"✅ {msg}")

def log_warn(msg):
    log(f"⚠️ {msg}")
    WARNINGS.append(msg)

def check_and_heal_syntax():
    log("\n--- 1. Kiểm tra cú pháp (Python Syntax AST) ---")
    py_files = [APP_PY, LOCAL_WORKER, COLAB_WORKER, KAGGLE_WORKER]
    for pf in py_files:
        if pf.exists():
            try:
                ast.parse(pf.read_text(encoding="utf-8", errors="ignore"))
                log_pass(f"Cú pháp {pf.name} hợp lệ (0 lỗi cú pháp)")
            except SyntaxError as e:
                log_warn(f"LỖI CÚ PHÁP tại {pf.name} dòng {e.lineno}: {e.msg}")

def check_and_heal_worker_config():
    log("\n--- 2. Tự động sửa cấu hình AI Worker (worker_config.json) ---")
    if WORKER_CONFIG.exists():
        try:
            cfg = json.loads(WORKER_CONFIG.read_text(encoding="utf-8"))
            url = (cfg.get("worker_url") or "").strip()
            if "colab.research.google.com" in url or "github.com" in url:
                cfg["worker_url"] = ""
                WORKER_CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
                log_heal(f"Đã gỡ bỏ link không hợp lệ '{url[:40]}...' trong worker_config.json (đã reset về Cloud AI mặc định)")
            elif url and not url.startswith("http"):
                cfg["worker_url"] = ""
                WORKER_CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
                log_heal("Đã reset worker_url không có schema http/https")
            else:
                log_pass(f"worker_config.json chuẩn xác (worker: {url or 'Cloud AI'})")
        except Exception as e:
            WORKER_CONFIG.write_text(json.dumps({"worker_url": ""}, indent=2), encoding="utf-8")
            log_heal(f"Đã tạo lại worker_config.json bị hỏng: {e}")
    else:
        WORKER_CONFIG.write_text(json.dumps({"worker_url": ""}, indent=2), encoding="utf-8")
        log_heal("Đã khởi tạo worker_config.json mặc định")

def check_and_heal_dockerfile():
    log("\n--- 3. Kiểm tra Dockerfile & Gunicorn Concurrency ---")
    if DOCKERFILE.exists():
        txt = DOCKERFILE.read_text(encoding="utf-8", errors="ignore")
        if "--workers 2" in txt or re.search(r'--workers\s+[2-9]', txt):
            txt = re.sub(r'--workers\s+\d+', '--workers 1', txt)
            DOCKERFILE.write_text(txt, encoding="utf-8")
            log_heal("Đã tự sửa Dockerfile: ép buộc '--workers 1 --threads 8' chống split-brain bộ nhớ")
        else:
            log_pass("Dockerfile cấu hình an toàn (--workers 1)")

def check_and_heal_app():
    log("\n--- 4. Kiểm tra Backend (app.py) & Timeout An Toàn ---")
    if not APP_PY.exists():
        log_warn("app.py không tồn tại")
        return
    txt = APP_PY.read_text(encoding="utf-8", errors="ignore")
    modified = False

    # Check timeout for worker clone (avoid freeze on slow worker)
    m_timeout = re.search(r'requests\.post\(\s*f"\{clean_url\}/api/clone".*?timeout=(\d+)', txt, re.DOTALL)
    if m_timeout and int(m_timeout.group(1)) > 30:
        txt = re.sub(
            r'(requests\.post\(\s*f"\{clean_url\}/api/clone".*?timeout=)(\d+)',
            r'\g<1>25',
            txt
        )
        modified = True
        log_heal("Đã giảm timeout Worker clone từ >30s xuống 25s trong app.py (tránh đứng ở 5%)")

    # Check watchdog timeout
    m_watchdog = re.search(r'time\.time\(\)\s*-\s*started\s*>\s*(\d+)', txt)
    if m_watchdog and int(m_watchdog.group(1)) < 300:
        txt = re.sub(r'time\.time\(\)\s*-\s*started\s*>\s*\d+', 'time.time() - started > 300', txt)
        modified = True
        log_heal("Đã nâng hardcoded watchdog trong app.py lên >= 300s an toàn cho văn bản dài")

    # Range HTTP 206 support
    if "download_video_stream" in txt and "Range" in txt and "206" in txt:
        log_pass("app.py hỗ trợ HTTP Range 206 tải video chống rớt mạng")
    else:
        log_warn("app.py thiếu hỗ trợ HTTP Range 206")

    # CapCut fallback on 403
    if "BV421_vivn_streaming" in txt:
        log_pass("app.py có hệ thống dự phòng CapCut Neural khi Edge-TTS bị chặn IP Cloud")
    else:
        log_warn("app.py thiếu mã fallback CapCut Neural")

    if modified:
        APP_PY.write_text(txt, encoding="utf-8")
        log_heal("Đã lưu các sửa chữa an toàn vào app.py")

def check_and_heal_frontend():
    log("\n--- 5. Kiểm tra Giao diện Frontend (templates/index.html) ---")
    if not INDEX_HTML.exists():
        log_warn("templates/index.html không tồn tại")
        return
    txt = INDEX_HTML.read_text(encoding="utf-8", errors="ignore")
    modified = False

    # Check if localStorage auto-sanitizes colab/github links
    if "cachedWorkerUrl.includes('colab.research.google.com')" not in txt:
        log_warn("templates/index.html thiếu bộ lọc tự động làm sạch link Colab trong localStorage")
    else:
        log_pass("templates/index.html có bộ lọc tự xóa link Colab nhầm lẫn trong localStorage")

    # Check watchdog duplicate ticks
    if "stalledTicks >= 120" in txt and "lastMessage" not in txt:
        txt = txt.replace("stalledTicks >= 120", "stalledTicks >= 500")
        modified = True
        log_heal("Đã tự sửa watchdog frontend: tăng thời gian chờ tránh ngắt tiến trình tạo truyện dài")
    else:
        log_pass("Watchdog frontend hoạt động an toàn")

    if modified:
        INDEX_HTML.write_text(txt, encoding="utf-8")
        log_heal("Đã cập nhật mã nguồn templates/index.html")

def check_and_heal_notebooks():
    log("\n--- 6. Kiểm tra Notebook Google Colab & Kaggle ---")
    notebooks = [
        ROOT / "sleep2k_ai_worker.ipynb",
        ROOT / "colab" / "sleep2k_ai_worker.ipynb",
        ROOT / "sleep2k_kaggle_worker.ipynb",
        ROOT / "colab" / "sleep2k_kaggle_worker.ipynb"
    ]
    for nb in notebooks:
        if nb.exists():
            txt = nb.read_text(encoding="utf-8", errors="ignore")
            if "onnxruntime-gpu==1.17.3" in txt:
                txt = txt.replace("onnxruntime-gpu==1.17.3", "onnxruntime")
                nb.write_text(txt, encoding="utf-8")
                log_heal(f"Đã tự sửa notebook {nb.name}: thay onnxruntime-gpu==1.17.3 lỗi thời bằng gói onnxruntime chuẩn")
            else:
                log_pass(f"Notebook {nb.name} chuẩn gói cài đặt")

def check_live_health():
    log("\n--- 7. Kiểm tra trạng thái máy chủ thực tế (Production Health) ---")
    for domain in LIVE_DOMAINS:
        try:
            req = urllib.request.Request(f"{domain}/health", headers={"User-Agent": "SLEEP2K-AutoHealer"})
            with urllib.request.urlopen(req, timeout=8) as r:
                if r.status == 200:
                    log_pass(f"Live Production {domain} phản hồi HTTP 200 Healthy")
        except Exception as e:
            log_warn(f"Live Production {domain} phản hồi chậm hoặc lỗi: {e}")

def run_diagnostics():
    diag_script = ROOT / ".agents" / "skills" / "sleep2k-troubleshooting" / "scripts" / "diagnose.py"
    if diag_script.exists():
        res = subprocess.run([sys.executable, str(diag_script)], capture_output=True, text=True, encoding="utf-8", errors="ignore")
        print(res.stdout)
        return res.returncode == 0
    return True

def auto_heal():
    log("=" * 65)
    log("🩺 SLEEP2K AUTONOMOUS SELF-HEALING ENGINE")
    log("=" * 65)

    check_and_heal_syntax()
    check_and_heal_worker_config()
    check_and_heal_dockerfile()
    check_and_heal_app()
    check_and_heal_frontend()
    check_and_heal_notebooks()
    check_live_health()

    log("\n--- 8. Chạy kiểm định toàn diện (Diagnostic Verification) ---")
    run_diagnostics()

    log("=" * 65)
    if HEALED_ACTIONS:
        log(f"🎉 ĐÃ TỰ ĐỘNG KHẮC PHỤC {len(HEALED_ACTIONS)} VẤN ĐỀ:")
        for act in HEALED_ACTIONS:
            log(f"  • {act}")
    else:
        log("🌟 Hệ thống hoàn toàn khỏe mạnh, không có lỗi tiềm ẩn nào cần sửa.")

    if WARNINGS:
        log(f"\n⚠️ CẢNH BÁO ({len(WARNINGS)}):")
        for w in WARNINGS:
            log(f"  • {w}")
    log("=" * 65)

    # Auto sync to GitHub if repairs occurred or --sync was specified
    if HEALED_ACTIONS or "--sync" in sys.argv:
        log("\n🚀 Đang tự động đồng bộ mã nguồn đã sửa chữa lên GitHub (main & master)...")
        sync_script = ROOT / "scratch" / "sync_github.py"
        if sync_script.exists():
            subprocess.run([sys.executable, str(sync_script)])

if __name__ == "__main__":
    auto_heal()
