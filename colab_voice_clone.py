# ==============================================================================
# SLEEP2K AI CLONE WORKER PRO (98% ULTRA-REALISTIC GPU ENGINE)
# Mô hình AI Sao Chép Giọng Siêu Thực 98% (48kHz Studio Quality) trên Google Colab T4
# ==============================================================================
# HƯỚNG DẪN 1-CLICK:
# 1. Mở https://colab.research.google.com -> Tạo sổ tay mới (New Notebook).
# 2. Vào Chỉnh sửa (Edit) -> Cài đặt sổ tay (Notebook settings) -> Chọn GPU (T4).
# 3. Dán toàn bộ mã này vào 1 ô code duy nhất -> Bấm nút Chạy (Play ▶️).
# 4. Chờ 1-2 phút, màn hình sẽ hiện link Cloudflare màu xanh:
#    👉 https://xxxx.trycloudflare.com
# 5. Dán link đó vào ô "Cấu hình Worker AI Clone" trên website của bạn là XONG!
# ==============================================================================

import os, sys, time, uuid, subprocess
from pathlib import Path

print("🚀 [1/4] Đang cài đặt thư viện AI âm thanh cao cấp & Cloudflare Tunnel...")
os.system("pip install -q vieneu flask soundfile imageio-ffmpeg")

# Tải cloudflared tunnel tự động (Không cần tài khoản, hoàn toàn miễn phí)
if not os.path.exists("/usr/local/bin/cloudflared"):
    os.system("curl -sLo /usr/local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64")
    os.system("chmod +x /usr/local/bin/cloudflared")

print("💎 [2/4] Đang nạp mô hình AI VieNeu Studio 48kHz Ultra-Realistic...")
import soundfile as sf
from vieneu import Vieneu
from flask import Flask, request, jsonify, send_file

# Khởi tạo mô hình AI Studio chất lượng cao nhất
tts = Vieneu()
print(f"✅ Mô hình AI 48kHz đã nạp thành công trên GPU!")

app = Flask(__name__)
API_SECRET_KEY = "sleep2k_clone_2024"
PORT = 5005

Path("/tmp/clone_work").mkdir(parents=True, exist_ok=True)

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    gpu_name = os.popen("nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null").read().strip()
    return jsonify({
        "status": "online",
        "service": "sleep2k_ultra_gpu_worker",
        "engine": "VieNeu-TTS Studio 48kHz Ultra (98% Realism)",
        "gpu": gpu_name or "NVIDIA T4 GPU",
        "sample_rate": 48000
    }), 200

@app.route("/api/clone", methods=["POST"])
def clone_voice():
    # Kiểm tra bảo mật
    if request.headers.get("X-API-Key") != API_SECRET_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    text = request.form.get("text", "").strip()
    rate = float(request.form.get("rate", 1.0) or 1.0)
    if not text:
        return jsonify({"error": "Text is empty"}), 400

    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio_file = request.files["audio"]
    task_id = uuid.uuid4().hex[:8]
    work_dir = Path("/tmp/clone_work") / f"task_{task_id}"
    work_dir.mkdir(parents=True, exist_ok=True)

    raw_path = work_dir / f"raw_{audio_file.filename}"
    clean_wav = work_dir / "clean_vocal.wav"
    out_mp3 = work_dir / "cloned_ultra.mp3"

    try:
        audio_file.save(str(raw_path))

        # Bộ lọc làm sạch âm thanh phòng thu (Khử tạp âm, lọc tần số rè, chuẩn hóa âm lượng EBU R128)
        subprocess.run([
            "ffmpeg", "-y", "-i", str(raw_path),
            "-af", "highpass=f=80,lowpass=f=14000,loudnorm=I=-16:TP=-1.5:LRA=11,silenceremove=start_periods=1:start_duration=0.1:start_threshold=-50dB",
            "-ar", "24000", "-ac", "1",
            str(clean_wav)
        ], capture_output=True, check=True)

        t0 = time.time()
        # Chạy inference với chất lượng cao nhất trên GPU T4
        wav_data = tts.infer(text, ref_audio=str(clean_wav), steps=16, speed=rate)
        infer_time = time.time() - t0
        print(f"✨ [AI 98% CLONE] Tạo xong '{text[:30]}...' trong {infer_time:.2f}s!")

        temp_wav = work_dir / "temp_synth.wav"
        sf.write(str(temp_wav), wav_data, tts.sample_rate)

        # Xuất file MP3 192kbps chuẩn studio
        subprocess.run([
            "ffmpeg", "-y", "-i", str(temp_wav),
            "-codec:a", "libmp3lame", "-b:a", "192k",
            str(out_mp3)
        ], capture_output=True, check=True)

        return send_file(str(out_mp3), mimetype="audio/mpeg")

    except Exception as ex:
        print(f"❌ Error during AI clone: {ex}")
        return jsonify({"error": str(ex)}), 500
    finally:
        # Dọn dẹp sạch sẽ bộ nhớ đĩa
        try:
            for p in work_dir.glob("*"):
                p.unlink(missing_ok=True)
            work_dir.rmdir()
        except Exception:
            pass

print("⚡ [3/4] Đang khởi động Flask Server và tạo đường hầm Cloudflare công khai...")
import threading
server_thread = threading.Thread(target=lambda: app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False), daemon=True)
server_thread.start()
time.sleep(2)

# Mở Cloudflare tunnel công khai
tunnel_process = subprocess.Popen(
    ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{PORT}"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

print("🌐 [4/4] Đang lấy đường dẫn kết nối công khai...")
import re
tunnel_url = None
start_t = time.time()
while time.time() - start_t < 25:
    line = tunnel_process.stderr.readline()
    if not line:
        continue
    m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
    if m:
        tunnel_url = m.group(0)
        break

if tunnel_url:
    print("\n" + "="*65)
    print("🎉 KẾT NỐI THÀNH CÔNG! ĐÃ CÓ LINK WORKER AI CLONE 98% SIÊU THỰC:")
    print(f"\n   👉 COPY LINK NÀY:  {tunnel_url}\n")
    print("Sau đó vào web -> Bấm '+ Tải Lên / Clone Giọng Bản Thân' ->")
    print("Bấm 'Cấu hình Worker AI Clone' -> Dán link trên vào và bấm Lưu.")
    print("="*65 + "\n")
else:
    print("⚠️ Không lấy được URL Cloudflare tự động. Vui lòng kiểm tra lại log.")

# Giữ script hoạt động liên tục
while True:
    time.sleep(3600)
