import os
import json
import time
import uuid
from pathlib import Path
import requests
from flask import Flask, render_template, request, jsonify, send_from_directory
from capcut_tts_api import CapCutClient, CapCutError

app = Flask(__name__)

# Base output directory for saving downloaded MP3 files
OUTPUT_DIR = Path(__file__).parent / "output_audio"
OUTPUT_DIR.mkdir(exist_ok=True)

PREVIEW_DIR = OUTPUT_DIR / "previews"
PREVIEW_DIR.mkdir(exist_ok=True)

VOICE_JSON_PATH = Path(__file__).parent / "Voice.json"
client = CapCutClient()

def load_voices():
    if VOICE_JSON_PATH.exists():
        with open(VOICE_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/voices", methods=["GET"])
def get_voices():
    voices = load_voices()
    return jsonify({"status": "success", "voices": voices})

@app.route("/api/generate", methods=["POST"])
def generate():
    try:
        data = request.json or {}
        text = data.get("text", "").strip()
        voice = data.get("voice", "BV421_vivn_streaming")
        rate = data.get("rate", "1.0")

        if not text:
            return jsonify({"status": "error", "message": "Vui lòng nhập văn bản cần chuyển thành giọng nói."}), 400

        # Step 1: Submit TTS task
        create_res = client.create_tts_task(texts=text, voice=voice, rate=rate)
        tasks = (create_res.get("data") or {}).get("tasks") or []
        if not tasks:
            return jsonify({"status": "error", "message": f"Không nhận được task ID từ CapCut API: {create_res}"}), 500

        task_id = tasks[0]["id"]
        token = tasks[0]["token"]

        # Step 2: Query until status is succeed
        speech_url = None
        duration = 0
        max_attempts = 15
        
        for attempt in range(max_attempts):
            query_res = client.query_tts_task(task_id, token)
            query_tasks = (query_res.get("data") or {}).get("tasks") or []
            if query_tasks:
                qtask = query_tasks[0]
                qstatus = qtask.get("status")
                if qstatus in ("succeed", "success"):
                    payload_raw = qtask.get("payload", "{}")
                    try:
                        payload_data = json.loads(payload_raw)
                        subtitles = payload_data.get("audio_subtitles", [])
                        if subtitles:
                            speech_url = subtitles[0].get("speech_url")
                            duration = subtitles[0].get("duration", 0)
                    except Exception as e:
                        print("Error parsing payload:", e)
                    break
                elif qstatus in ("failed", "error"):
                    return jsonify({"status": "error", "message": f"CapCut API xử lý thất bại: {query_res}"}), 500
            time.sleep(1.0)

        if not speech_url:
            return jsonify({"status": "error", "message": "Quá thời gian chờ (Timeout) hoặc không tìm thấy URL âm thanh."}), 500

        # Step 3: Download MP3 file locally (download_mp3.py flow)
        mp3_resp = requests.get(speech_url, timeout=30)
        if mp3_resp.status_code != 200:
            return jsonify({"status": "error", "message": f"Tải file MP3 thất bại với mã lỗi HTTP {mp3_resp.status_code}"}), 500

        file_id = str(uuid.uuid4())[:8]
        filename = f"tts_{file_id}_{int(time.time())}.mp3"
        local_path = OUTPUT_DIR / filename
        
        with open(local_path, "wb") as f:
            f.write(mp3_resp.content)

        return jsonify({
            "status": "success",
            "task_id": task_id,
            "speech_url": speech_url,
            "filename": filename,
            "download_url": f"/output/{filename}",
            "duration_ms": duration,
            "text": text,
            "voice": voice
        })

    except CapCutError as ce:
        return jsonify({"status": "error", "message": f"Lỗi CapCut API: {str(ce)}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Lỗi hệ thống: {str(e)}"}), 500

@app.route("/api/preview_voice", methods=["POST"])
def preview_voice():
    try:
        data = request.json or {}
        voice = data.get("voice", "BV421_vivn_streaming")
        lan = (data.get("lan") or "vi").lower()

        clean_voice = "".join(c for c in voice if c.isalnum() or c in ("_", "-"))
        preview_filename = f"preview_{clean_voice}.mp3"
        preview_file_path = PREVIEW_DIR / preview_filename

        if preview_file_path.exists():
            return jsonify({
                "status": "success",
                "download_url": f"/output/previews/{preview_filename}"
            })

        if "en" in lan:
            sample_text = "Hello, this is a voice preview."
        elif "zh" in lan:
            sample_text = "你好，这是语音试听。"
        else:
            sample_text = "Xin chào, đây là giọng đọc thử nghiệm."

        create_res = client.create_tts_task(texts=sample_text, voice=voice, rate="1.0")
        tasks = (create_res.get("data") or {}).get("tasks") or []
        if not tasks:
            return jsonify({"status": "error", "message": "Không nhận được task ID"}), 500

        task_id = tasks[0]["id"]
        token = tasks[0]["token"]

        speech_url = None
        for attempt in range(12):
            query_res = client.query_tts_task(task_id, token)
            query_tasks = (query_res.get("data") or {}).get("tasks") or []
            if query_tasks:
                qtask = query_tasks[0]
                if qtask.get("status") in ("succeed", "success"):
                    payload_data = json.loads(qtask.get("payload", "{}"))
                    subtitles = payload_data.get("audio_subtitles", [])
                    if subtitles:
                        speech_url = subtitles[0].get("speech_url")
                    break
            time.sleep(0.8)

        if not speech_url:
            return jsonify({"status": "error", "message": "Không thể tạo bản nghe thử"}), 500

        mp3_resp = requests.get(speech_url, timeout=20)
        with open(preview_file_path, "wb") as f:
            f.write(mp3_resp.content)

        return jsonify({
            "status": "success",
            "download_url": f"/output/previews/{preview_filename}"
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/output/<filename>")
def serve_audio(filename):
    return send_from_directory(OUTPUT_DIR, filename)

@app.route("/output/previews/<filename>")
def serve_preview_audio(filename):
    return send_from_directory(PREVIEW_DIR, filename)

@app.route("/api/history", methods=["GET"])
def get_history():
    files = []
    for p in sorted(OUTPUT_DIR.glob("*.mp3"), key=os.path.getmtime, reverse=True):
        files.append({
            "filename": p.name,
            "download_url": f"/output/{p.name}",
            "created_at": time.strftime('%H:%M:%S %d/%m/%Y', time.localtime(p.stat().st_mtime)),
            "size_kb": round(p.stat().st_size / 1024, 1)
        })
    return jsonify({"status": "success", "files": files})

if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("CapCut TTS Web Interface running at http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
