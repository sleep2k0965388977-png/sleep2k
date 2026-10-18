# Project Guidelines: SLEEP2K TTS & STT

## Operational Rules
1. **TTS Long Text Generation Safety:**
   - Never set an arbitrary fixed watchdog timeout (< 300s) on total job execution. Texts with > 200,000 characters (1,600+ chunks) legitimately take 8–15 minutes.
   - Always check `time.time() - last_active > 300` (5 minutes of absolute freezing) instead of checking total elapsed time from `started_at`.
   - In `templates/index.html`, only trigger watchdog if both `%` and message remain unchanged for > 5 minutes (`stalledTicks >= 500`).

2. **Gunicorn Concurrency Rule:**
   - The app maintains in-memory state in `JOBS` dictionary.
   - Always keep `Dockerfile` command as `gunicorn --workers 1 --threads 8` to avoid multi-worker split-brain memory state.

3. **Edge-TTS Cloud Datacenter Fallback:**
   - Microsoft Bing Speech blocks datacenter IPs with `403 Invalid response status`.
   - Always provide automatic CapCut Neural fallback (`BV421_vivn_streaming` for female, `BV422_vivn_streaming` for male) in `/api/preview_voice` and `fetch_chunk_audio`.

4. **Frontend Polling & Player Rules:**
   - Maintain `window._activeTtsInterval` as a singleton to prevent multiple interval loops creating duplicate error toasts.
   - Always unmute and set player volume to 1.0 when generation completes.
   - Display audio duration using `formatDuration` in hours, minutes, and seconds.

5. **Self-Healing Diagnostics:**
   - Run `python .agents/skills/sleep2k-troubleshooting/scripts/diagnose.py` before and after making codebase edits to verify there are no watchdog or concurrency regressions.

6. **Resilient Video Streaming & Weak Network Resumption:**
   - Always support HTTP Range requests (`Range: bytes=start-end`, returning `206 Partial Content`) in `/api/download_video_stream` so browser pause/resume and weak networks never fail.
   - In `templates/index.html`, never return `true` on download failure in `downloadVideoDirect`, and use resumable Range retries (up to 8 times) on network drops to prevent multi-download flooding.
