# ============================================================
# SLEEP2K Voice Cloning Server — Google Colab
# ============================================================
# 1. Mở https://colab.research.google.com
# 2. Runtime → Change runtime type → GPU (T4)
# 3. Paste code này vào 1 cell → bấm Run ▶️
# 4. Đợi ~2 phút load model → copy URL Ngrok
# 5. Dán URL vào app SLEEP2K TTS
# ============================================================

import os, time, uuid
from pathlib import Path

# ── CẤU HÌNH ──
NGROK_AUTH_TOKEN = ""  # Lấy tại https://dashboard.ngrok.com
API_SECRET_KEY = "sleep2k_clone_2024"  # Đổi key tuỳ ý
PORT = 5050

# ── Cài đặt ──
os.system("pip install -q vieneu flask pyngrok")

print("🔄 Loading VieNeu-TTS v3 Turbo...")
from vieneu import Vieneu
tts = Vieneu()
voices = tts.list_preset_voices()
print(f"✅ Ready! {len(voices)} preset voices")

from flask import Flask, request, jsonify, send_file
app = Flask(__name__)
Path("/tmp/clone").mkdir(exist_ok=True)
Path("/tmp/uploads").mkdir(exist_ok=True)

@app.route("/health")
def health():
    gpu = os.popen("nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null").read().strip()
    return jsonify({"status": "online", "gpu": gpu or "CPU", "voices": len(voices)})

@app.route("/api/clone", methods=["POST"])
def clone():
    if request.headers.get("X-API-Key") != API_SECRET_KEY:
        return jsonify({"error": "Invalid key"}), 401
    text = request.form.get("text", "").strip()
    if not text:
        return jsonify({"error": "No text"}), 400
    if "audio" not in request.files:
        return jsonify({"error": "No audio file"}), 400
    f = request.files["audio"]
    suffix = Path(f.filename).suffix or ".wav"
    ref = Path(f"/tmp/uploads/ref_{uuid.uuid4().hex[:8]}{suffix}")
    f.save(str(ref))
    try:
        t0 = time.time()
        audio = tts.infer(text, ref_audio=str(ref))
        out = Path(f"/tmp/clone/clone_{uuid.uuid4().hex[:8]}.wav")
        tts.save(audio, str(out))
        dur = len(audio) / 48000
        print(f"✅ Clone: {time.time()-t0:.1f}s, audio {dur:.1f}s")
        return send_file(str(out), mimetype="audio/wav", as_attachment=True, download_name=out.name)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        ref.unlink(missing_ok=True)

@app.route("/api/preset_clone", methods=["POST"])
def preset():
    if request.headers.get("X-API-Key") != API_SECRET_KEY:
        return jsonify({"error": "Invalid key"}), 401
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    voice = data.get("voice", "Hải Đăng")
    if not text:
        return jsonify({"error": "No text"}), 400
    try:
        t0 = time.time()
        audio = tts.infer(text, voice=voice)
        out = Path(f"/tmp/clone/p_{uuid.uuid4().hex[:8]}.wav")
        tts.save(audio, str(out))
        print(f"✅ Preset '{voice}': {time.time()-t0:.1f}s")
        return send_file(str(out), mimetype="audio/wav", as_attachment=True, download_name=out.name)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/voices")
def list_v():
    return jsonify({"voices": [{"label": l, "id": v} for l, v in voices]})

# ── Ngrok Tunnel ──
from pyngrok import ngrok, conf
if NGROK_AUTH_TOKEN:
    conf.get_default().auth_token = NGROK_AUTH_TOKEN
tunnel = ngrok.connect(PORT, bind_tls=True)
url = tunnel.public_url

print(f"\n{'='*60}")
print(f"🚀 VOICE CLONING SERVER SẴN SÀNG!")
print(f"{'='*60}")
print(f"🔗 URL: {url}")
print(f"🔑 Key: {API_SECRET_KEY}")
print(f"📋 Copy URL → dán vào SLEEP2K TTS app")
print(f"{'='*60}")
print(f"Test: {url}/health")
print(f"Clone: POST {url}/api/clone")
print(f"{'='*60}")

app.run(host="0.0.0.0", port=PORT)
