"""
SLEEP2K AI CLONE WORKER - LOCAL / COLAB GPU WORKER
Chạy trên máy tính cá nhân (16GB RAM) hoặc Google Colab (GPU T4 miễn phí)
Nhận request nhân bản giọng nói từ Railway App và trả về audio chuẩn 100% âm sắc gốc.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import os, time, uuid, subprocess, tempfile, hashlib
from pathlib import Path

# ── AUTO-FIX: onnxruntime >= 1.18 blocks vieneu external data path ──────────
# vieneu dùng external ONNX data files → bị chặn bởi onnxruntime >= 1.18.0
# Fix: tự động downgrade xuống 1.17.3 rồi re-exec script này
def _fix_onnxruntime_if_needed():
    try:
        import onnxruntime as _ort
        ver = _ort.__version__
        # So sánh version tuple để chắc chắn
        parts = [int(x) for x in ver.split('.')[:2]]
        if parts[0] > 1 or (parts[0] == 1 and parts[1] >= 18):
            print(f"⚠️  onnxruntime {ver} không tương thích với vieneu (cần < 1.18)")
            print("🔧 Đang tự động downgrade onnxruntime xuống 1.17.3...")
            subprocess.run(
                [sys.executable, '-m', 'pip', 'uninstall', '-y',
                 'onnxruntime', 'onnxruntime-gpu'],
                capture_output=True
            )
            r = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '-q', 'onnxruntime==1.17.3'],
                capture_output=True, text=True
            )
            if r.returncode != 0:
                # Fallback: thử onnxruntime CPU
                subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', '-q', 'onnxruntime==1.17.3'],
                    capture_output=True
                )
            print("✅ Downgrade xong! Đang khởi động lại script...")
            # Re-exec để load đúng version
            os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            print(f"✅ onnxruntime {ver} — OK (< 1.18)")
    except ImportError:
        print("📦 onnxruntime chưa cài, tiếp tục...")

_fix_onnxruntime_if_needed()
# ─────────────────────────────────────────────────────────────────────────────

from flask import Flask, request, jsonify, send_file
import soundfile as sf

app = Flask(__name__)
CLONE_API_KEY = os.environ.get("CLONE_API_KEY", "sleep2k_clone_2024")

print("⏳ Đang tải bộ máy AI VieNeu Voice Cloning...")
from vieneu import Vieneu
tts = None
for _mode in ["v3turbo", "v3nano"]:
    try:
        tts = Vieneu(mode=_mode)
        print(f"✅ Đã khởi động VieNeu {_mode} ({getattr(tts, 'sample_rate', '?')}Hz)!")
        break
    except Exception as _e:
        err_s = str(_e)
        print(f"⚠️  {_mode} lỗi: {err_s[:120]}")
        if "External data path" in err_s:
            print("❌ Lỗi onnxruntime path — không thể tự fix sau khi exec. Restart lại script.")
            sys.exit(1)

if tts is None:
    print("❌ Không khởi động được bất kỳ mode nào của Vieneu. Thoát.")
    sys.exit(1)

def get_ffmpeg_exe():
    import shutil
    cmd = shutil.which("ffmpeg")
    if cmd:
        return cmd
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    return "ffmpeg"

FFMPEG = get_ffmpeg_exe()

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "online", "service": "sleep2k_ai_clone_worker", "device": "local"}), 200

@app.route("/api/clone", methods=["POST"])
def clone_voice():
    key = request.headers.get("X-API-Key", "")
    if key != CLONE_API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    text = request.form.get("text", "").strip()
    rate = float(request.form.get("rate", 1.0) or 1.0)
    if not text:
        return jsonify({"error": "Text is empty"}), 400

    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio_file = request.files["audio"]
    temp_dir = Path(tempfile.gettempdir()) / f"worker_{uuid.uuid4().hex[:8]}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    raw_path = temp_dir / f"raw_{audio_file.filename}"
    clean_wav = temp_dir / "clean_ref.wav"
    out_mp3 = temp_dir / "cloned_result.mp3"

    try:
        audio_bytes = audio_file.read()
        audio_hash = hashlib.md5(audio_bytes).hexdigest()[:12]
        cache_dir = Path(tempfile.gettempdir()) / "sleep2k_ref_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached_clean_wav = cache_dir / f"clean_{audio_hash}.wav"

        if not cached_clean_wav.exists() or cached_clean_wav.stat().st_size < 500:
            raw_path = temp_dir / f"raw_{audio_hash}.wav"
            raw_path.write_bytes(audio_bytes)
            subprocess.run([
                FFMPEG, "-y", "-i", str(raw_path),
                "-af", "highpass=f=60,lowpass=f=12000,loudnorm=I=-16:TP=-1.5:LRA=11",
                "-ar", "24000", "-ac", "1",
                str(cached_clean_wav)
            ], capture_output=True, check=True)
            raw_path.unlink(missing_ok=True)
        clean_wav = cached_clean_wav

        t0 = time.time()
        # Tối ưu hóa siêu thực:
        # - temperature=0.40: phát âm dứt khoát, chuẩn dấu thanh tiếng Việt, không bị nuốt âm
        # - top_p=0.85: loại bỏ tạp âm khẩu hình
        # - silence_p=0.20: tạo khoảng lấy hơi tự nhiên chuẩn tiếng Việt giữa các vế câu
        # - apply_watermark=False: giữ nguyên sóng âm 48kHz trong trẻo
        infer_kwargs = {
            "ref_audio": str(clean_wav),
            "speed": rate,
            "temperature": 0.40,
            "top_p": 0.85,
            "silence_p": 0.20,
            "denoise": True,
            "use_ref_codes": True,
            "apply_watermark": False
        }
        try:
            wav_data = tts.infer(text, **infer_kwargs)
        except TypeError:
            # Fallback nếu backend v3nano/cũ không nhận tham số nâng cao
            wav_data = tts.infer(text, ref_audio=str(clean_wav), steps=16, speed=rate)

        print(f"Synthesized '{text[:30]}...' in {time.time()-t0:.2f}s")

        temp_synth_wav = temp_dir / "temp_synth.wav"
        sf.write(str(temp_synth_wav), wav_data, tts.sample_rate)
        subprocess.run([
            FFMPEG, "-y", "-i", str(temp_synth_wav),
            "-codec:a", "libmp3lame", "-b:a", "192k",
            str(out_mp3)
        ], capture_output=True, check=True)

        return send_file(str(out_mp3), mimetype="audio/mpeg")

    except Exception as ex:
        print(f"Worker clone error: {ex}")
        return jsonify({"error": str(ex)}), 500
    finally:
        try:
            for p in temp_dir.glob("*"):
                p.unlink(missing_ok=True)
            temp_dir.rmdir()
        except Exception:
            pass

@app.route("/api/youtube_stream", methods=["POST"])
def worker_youtube_stream():
    key = request.headers.get("X-API-Key", "")
    if key != CLONE_API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is empty"}), 400

    from yt_dlp import YoutubeDL
    client_candidates = [
        ['android'],
        ['tv_embedded'],
        ['android_vr']
    ]
    for clients in client_candidates:
        try:
            ydl_opts = {
                'skip_download': True,
                'quiet': True,
                'no_warnings': True,
                'extractor_args': {
                    'youtube': {
                        'player_client': clients
                    }
                }
            }
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                stream_url = info.get('url')
                if not stream_url and 'formats' in info:
                    audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none' and f.get('url')]
                    if audio_formats:
                        stream_url = audio_formats[-1].get('url')
                if stream_url:
                    return jsonify({
                        "status": "success",
                        "stream_url": stream_url,
                        "duration": info.get("duration", 0),
                        "title": info.get("title", ""),
                        "subtitles": info.get("subtitles", {}),
                        "automatic_captions": info.get("automatic_captions", {})
                    }), 200
        except Exception as ex:
            print(f"Worker yt-dlp client {clients} error: {ex}")

    return jsonify({"status": "error", "message": "Could not extract stream on worker"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5005))
    print(f"🚀 Worker đang lắng nghe tại cổng {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
