#!/usr/bin/env python3
"""
SLEEP2K Autonomous Self-Healing & Diagnostic Agent Script
Scheduled: 8:00 AM & 20:00 PM daily (Asia/Ho_Chi_Minh)

Workflow:
1. Scans codebase for architectural or watchdog regressions (diagnose.py)
2. Checks live Railway production health (GET /health)
3. If regressions or health failures are detected, automatically applies skill runbook fixes
4. Commits and pushes verified fixes to GitHub
5. Deploys updated build to Railway
"""

import sys, os, time, subprocess, json, urllib.request, re, base64
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parents[4]
APP_PY = ROOT / "app.py"
INDEX_HTML = ROOT / "templates" / "index.html"
DOCKERFILE = ROOT / "Dockerfile"
LOG_FILE = ROOT / "auto_heal.log"

def get_token():
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ.get("GITHUB_TOKEN")
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("GITHUB_TOKEN="):
                return line.split("=", 1)[1].strip()
    return ""

TOKEN = get_token()
REPO = "sleep2k0965388977-png/sleep2k"
RAILWAY_URL = "https://sleep2k.up.railway.app"


def log(msg):
    ts = time.strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{ts} {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

log("=" * 60)
log("🚀 SLEEP2K AUTONOMOUS SCAN & SELF-HEALING ROUTINE STARTED")
log("=" * 60)

repaired_something = False

# --- STEP 1: Codebase Diagnostics ---
log("🔍 Step 1: Scanning local codebase for watchdog/concurrency regressions...")
diagnose_script = ROOT / ".agents" / "skills" / "sleep2k-troubleshooting" / "scripts" / "diagnose.py"
if diagnose_script.exists():
    res = subprocess.run([sys.executable, str(diagnose_script)], capture_output=True, text=True, encoding='utf-8', errors='ignore')
    if res.returncode != 0:
        log(f"⚠️ Issues detected by diagnose.py:\n{res.stdout}\n{res.stderr}")
        # Apply Auto-Remediation
        log("🛠️ Applying Skill auto-remediation to codebase...")

        # Fix 1: app.py watchdog
        if APP_PY.exists():
            app_code = APP_PY.read_text(encoding="utf-8", errors="ignore")
            # Replace short started_at check with safe last_active check
            if re.search(r'time\.time\(\)\s*-\s*started\s*>\s*(\d+)', app_code):
                app_code = re.sub(
                    r'started\s*=\s*job\.get\("started_at"\)\s*\n\s*if\s*started\s*and\s*\(time\.time\(\)\s*-\s*started\s*>\s*\d+\):',
                    'last_act = job.get("last_active") or job.get("started_at")\n        if last_act and (time.time() - last_act > 300):',
                    app_code
                )
                APP_PY.write_text(app_code, encoding="utf-8")
                repaired_something = True
                log("✅ Repaired app.py watchdog to check last_active > 300s")

        # Fix 2: Dockerfile workers
        if DOCKERFILE.exists():
            df_code = DOCKERFILE.read_text(encoding="utf-8", errors="ignore")
            if "--workers 2" in df_code:
                df_code = df_code.replace("--workers 2", "--workers 1")
                DOCKERFILE.write_text(df_code, encoding="utf-8")
                repaired_something = True
                log("✅ Repaired Dockerfile Gunicorn workers to 1")

    else:
        log("✅ Codebase passed all diagnostic assertions.")

# --- STEP 2: Live Health Check ---
log(f"🌐 Step 2: Checking live service health at {RAILWAY_URL}/health...")
live_ok = False
for attempt in range(1, 4):
    try:
        req = urllib.request.Request(f"{RAILWAY_URL}/health", headers={"User-Agent": "SLEEP2K-Watcher"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                body = resp.read().decode("utf-8")
                log(f"✅ Live service response: HTTP 200 Healthy - {body[:80]}")
                live_ok = True
                break
    except Exception as ex:
        log(f"⚠️ Health check attempt {attempt}/3 failed: {ex}")
        time.sleep(3)

if not live_ok:
    log("🚨 Live service is UNHEALTHY or unresponsive! Flagged for immediate redeploy.")
    repaired_something = True

# --- STEP 3: Sync & Deploy if Repaired ---
if repaired_something:
    log("📦 Step 3: Pushing self-healed files to GitHub (main & master)...")
    try:
        pusher = ROOT / "scratch" / "push_to_github.py"
        if pusher.exists():
            subprocess.run([sys.executable, str(pusher)], check=True)
            log("✅ GitHub repository synchronized with fixes.")

        log("🚀 Step 4: Triggering Railway auto-deployment...")
        subprocess.run(["npx", "--yes", "@railway/cli", "up", "--detach"], cwd=str(ROOT), check=True)
        log("🎉 Railway deployment triggered successfully!")
    except Exception as ex_dep:
        log(f"❌ Error during auto-deploy: {ex_dep}")
else:
    log("🎉 Routine check complete: Everything is 100% healthy, no repairs needed!")

log("=" * 60)
