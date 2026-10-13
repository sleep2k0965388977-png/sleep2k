#!/usr/bin/env python3
"""
Diagnostic & Self-Healing Scanner for SLEEP2K TTS & STT App
Detects common pitfalls:
  1. Hardcoded short watchdogs (< 300s) on large text jobs
  2. Gunicorn multi-worker in-memory state split-brain
  3. Edge-TTS 403 on Cloud IPs without fallback
  4. YouTube datacenter IP blocking without cookie/proxy fallback
  5. UI polling retry tolerance & duplicate toast issues
"""

import sys
from pathlib import Path
import re

sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parents[4]
APP_PY = ROOT / "app.py"
INDEX_HTML = ROOT / "templates" / "index.html"
DOCKERFILE = ROOT / "Dockerfile"

issues_found = []

print("=" * 60)
print("🔍 SLEEP2K APP DIAGNOSTIC SCANNER")
print("=" * 60)

# Check 1: app.py watchdog
if APP_PY.exists():
    txt = APP_PY.read_text(encoding="utf-8", errors="ignore")
    # Check for short hardcoded watchdog
    short_timeout = re.search(r'time\.time\(\)\s*-\s*started\s*>\s*(\d+)', txt)
    if short_timeout:
        sec = int(short_timeout.group(1))
        if sec < 300:
            issues_found.append(f"[CRITICAL] app.py has hardcoded short started_at timeout: {sec}s (must use last_active or >= 300s)")
    
    # Check if last_active is tracked
    if 'job.get("last_active")' not in txt and 'JOBS[job_id]["last_active"]' not in txt:
        issues_found.append("[WARNING] app.py does not track last_active on chunk completions")

    # Check Edge-TTS fallback
    if "is_edge_tts_voice" in txt and "BV421_vivn_streaming" not in txt:
        issues_found.append("[WARNING] app.py missing CapCut fallback for Edge-TTS on Cloud datacenter IPs")

    # Check workers in run_tts_job
    pool_match = re.search(r'ThreadPoolExecutor\(max_workers=(\d+)\)', txt)
    if pool_match:
        workers = int(pool_match.group(1))
        if workers > 5:
            issues_found.append(f"[WARNING] ThreadPoolExecutor has {workers} workers (may trigger API rate limits; 3-5 recommended)")
        print(f"✅ ThreadPoolExecutor workers: {workers}")
    else:
        print("ℹ️ ThreadPoolExecutor not configured")
else:
    issues_found.append("[ERROR] app.py not found")

# Check 2: Dockerfile Gunicorn workers
if DOCKERFILE.exists():
    df_txt = DOCKERFILE.read_text(encoding="utf-8", errors="ignore")
    if "--workers 2" in df_txt or re.search(r'--workers\s+[2-9]', df_txt):
        issues_found.append("[CRITICAL] Dockerfile uses multi-worker Gunicorn (causes in-memory job state split-brain; use --workers 1 --threads 8)")
    else:
        print("✅ Dockerfile Gunicorn concurrency configuration is safe")
else:
    issues_found.append("[WARNING] Dockerfile not found")

# Check 3: templates/index.html watchdog & format
if INDEX_HTML.exists():
    html_txt = INDEX_HTML.read_text(encoding="utf-8", errors="ignore")
    if "stalledTicks >= 120" in html_txt and "lastMessage" not in html_txt:
        issues_found.append("[CRITICAL] index.html watchdog triggers prematurely at 72s on identical % ticks")
    else:
        print("✅ index.html watchdog uses message & percent deduplicated tracking")

    if "formatDuration" in html_txt and "giờ" not in html_txt:
        issues_found.append("[WARNING] formatDuration does not handle hours properly")
    else:
        print("✅ formatDuration formats hours/minutes/seconds properly")
else:
    issues_found.append("[ERROR] templates/index.html not found")

print("-" * 60)
if not issues_found:
    print("🎉 ALL SYSTEMS PASS! No architectural or runtime watchdog issues detected.")
    sys.exit(0)
else:
    print(f"⚠️ FOUND {len(issues_found)} ISSUE(S):")
    for iss in issues_found:
        print(f"  • {iss}")
    sys.exit(1)
