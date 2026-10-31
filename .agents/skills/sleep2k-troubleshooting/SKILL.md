---
name: sleep2k-troubleshooting
description: >-
  Use this skill whenever the user reports errors or unexpected behavior in the SLEEP2K TTS/STT application
  (https://sleep2k.up.railway.app/). Automatically scans for common root causes: premature 150s watchdog timeouts on large text (>200k chars),
  stacking red error toasts, YouTube datacenter IP bot-blocking (requiring cookie or proxy), Edge-TTS 403 Forbidden on Cloud IPs,
  and Gunicorn multi-worker state split-brain. Provides instant automated diagnosis and targeted remediation runbooks.
---

# SLEEP2K TTS & STT Troubleshooting & Self-Healing Skill

This skill provides comprehensive operational runbooks and automated diagnostic procedures for the **SLEEP2K** Web App deployed on Railway / Docker.

## Automated Scanner
Run the diagnostic script to quickly scan the codebase for regressions:
```bash
python .agents/skills/sleep2k-troubleshooting/scripts/diagnose.py
```

---

## Common Issues & Immediate Fixes

### 1. Large Text TTS Generation Aborts Halfway (~45% - 50%) with "Tiến trình bị quá thời gian chờ (Timeout)"
* **Root Cause:**
  A hardcoded watchdog in `app.py` checking `time.time() - started > 150` or in `index.html` checking `stalledTicks >= 120`.
  For large texts (> 200,000 characters, 1,600+ chunks), processing takes 8–15 minutes. An arbitrary 150s counter will kill valid running jobs!
* **Correct Fix:**
  - In `app.py` `run_tts_job`: Track `last_active = time.time()` whenever any chunk finishes.
  - In `get_job_status`: Check `time.time() - last_active > 300` (5 minutes of absolute freezing), NEVER hardcode a total elapsed time limit.
  - In `templates/index.html`: Update watchdog to check both `p === lastProgress && currentMsg === lastMessage` with `stalledTicks >= 500`.

---

### 2. "Không tạo được giọng Edge-TTS" / 403 Invalid response status
* **Root Cause:**
  Microsoft Bing Speech (`wss://speech.platform.bing.com`) blocks datacenter/cloud IP ranges (Railway, AWS, GCP, etc.).
* **Correct Fix:**
  - Fallback to CapCut Vietnamese Neural voices (`BV421_vivn_streaming` for female, `BV422_vivn_streaming` for male).
  - In `/api/preview_voice` and `fetch_chunk_audio`: If Edge-TTS fails with 403, immediately query CapCut's natural voices instead of returning silence or failing.

---

### 3. "YouTube chặn IP máy chủ Cloud (yêu cầu xác thực bot)" on YouTube STT Tab
* **Root Cause:**
  YouTube blocks datacenter IPs from calling `yt-dlp` stream extraction anonymously.
* **Correct Fix:**
  - Direct the user to the yellow **"Cookie YouTube"** button in the UI (`#modalYoutubeCookie`) or inject cookies via `YOUTUBE_COOKIES` environment variable.
  - Fallback to local worker (`/api/youtube_stream`) running on residential IP.

---

### 4. Progress Aborts or Resets to 0% Randomly (/api/job_status 404)
* **Root Cause:**
  Gunicorn running with `--workers 2` or more. Request 1 hits Worker A (which has `JOBS[job_id]`), Request 2 hits Worker B (which doesn't have it).
* **Correct Fix:**
  - Ensure `Dockerfile` runs:
    `CMD ["gunicorn", "--workers", "1", "--threads", "8", "--bind", "0.0.0.0:7860", "--timeout", "300", "app:app"]`
  - Persist checkpoints on disk using `save_atomic_checkpoint(job_id, data)` and disk fallback in `get_job_status`.

---

### 5. Stacking / Repeating Red Toasts on Frontend
* **Root Cause:**
  `window.alert` is overridden to `showToast(...)`. Multiple polling intervals running concurrently without singleton tracking (`window._activeTtsInterval`), or server restart triggering multiple 502 error toasts.
* **Correct Fix:**
  - Track `window._activeTtsInterval` as a singleton in `startGeneration()`.
  - Use `window._hasAlertedPollError` latch to prevent duplicate alerts.
  - Unmute `audioPlayer.muted = false` and set volume to 1.0 when generation completes.

---

### 6. Video Downloader Network Lag / Weak Wi-Fi Drops ("Không truy cập được trang" / Flooding Chrome Downloads)
* **Root Cause:**
  - Weak Wi-Fi or packet loss causes temporary network stalls during large (1GB+) video downloads.
  - If `/api/download_video_stream` does not support HTTP Range requests (`Range: bytes=...`, returning `206 Partial Content`), Chrome's "Tiếp tục" (Resume) button fails with "Không truy cập được trang".
  - If frontend `downloadVideoDirect` catches errors and returns `true`, `startBulkDownload` mistakenly assumes the video finished and launches the next video, flooding Chrome with 40+ concurrent downloads that crash the user's network.
* **Correct Fix:**
  - In `app.py`: Support `Range: bytes=start-end` in `/api/download_video_stream` and forward it to upstream CDN with HTTP 206 response headers (`Content-Range`, `Accept-Ranges: bytes`).
  - Add self-healing stream generator (`generate_resilient_douyin_stream`) that auto-reconnects with Range up to 5 times on connection drops without closing the client socket.
  - In `templates/index.html`: Implement resumable Range-based fetch retry loop in `downloadVideoDirect` (up to 8 retries) preserving all previously buffered chunks.
  - Never return `true` on download failure in `downloadVideoDirect` to prevent bulk download queue flood.

---

### 7. Google Colab AI Clone Worker Crash (`External data path validation failed for initializer: ...vieneu_backbone_shared.data` / ONNXRuntimeError)
* **Root Cause:**
  - `onnxruntime` >= 1.18.0 added a strict security check blocking external data files that reside outside the main ONNX model directory.
  - When VieNeu downloads models from HuggingFace to `~/.cache/huggingface/hub/...`, the shared weights (`vieneu_backbone_shared.data`) trigger `[ONNXRuntimeError] : 1 : FAIL : External data path validation failed for initializer`.
* **Correct Fix:**
  - In Colab notebook (`sleep2k_ai_worker.ipynb`) and `local_clone_worker.py`:
    Always downgrade `onnxruntime` before loading models:
    ```bash
    pip uninstall -y onnxruntime onnxruntime-gpu onnxruntime-training
    pip install -q onnxruntime-gpu==1.17.3
    ```
  - Use `os.execv()` self-restart in Python scripts if version >= 1.18 is detected in memory.
  - Once the Cloudflare tunnel is created, worker automatically registers its URL with website via `POST /api/set_clone_worker_url`.
  - Frontend checks status via `GET /api/clone_worker_status`. If the user has the browser tab open while Colab is booting, a simple page refresh (F5) or clicking "Lưu URL" pulls the registered worker URL into the UI.

