"""
SLEEP2K AI CLONE WORKER - LOCAL / COLAB GPU WORKER
Chạy trên máy tính cá nhân (16GB RAM) hoặc Google Colab (GPU T4 miễn phí)
Nhận request nhân bản giọng nói từ Railway App và trả về audio chuẩn 100% âm sắc gốc.
"""
import os, sys, io, time, uuid, subprocess, tempfile
from pathlib import Path
from flask import Flask, request, jsonify, send_file
import soundfile as sf

app = Flask(__name__)
CLONE_API_KEY = os.environ.get("CLONE_API_KEY", "sleep2k_clone_2024")

print("⏳ Đang tải bộ máy AI VieNeu Voice Cloning...")
from vieneu import Vieneu
try:
    tts = Vieneu(mode="v3nano")
    print("✅ Đã khởi động thành công bộ máy VieNeu v3 Nano (24kHz Studio Quality)!")
except Exception as e:
    print(f"Lỗi khởi động Vieneu: {e}")
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
        audio_file.save(str(raw_path))

        subprocess.run([
            FFMPEG, "-y", "-i", str(raw_path),
            "-ar", "24000", "-ac", "1",
            str(clean_wav)
        ], capture_output=True, check=True)

        t0 = time.time()
        wav_data = tts.infer(text, ref_audio=str(clean_wav), steps=8, speed=rate)
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5005))
    print(f"🚀 Worker đang lắng nghe tại cổng {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
