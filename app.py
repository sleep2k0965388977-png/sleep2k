import os
import re
import json
import time
import uuid
import threading
from pathlib import Path
import requests
from flask import Flask, render_template, request, jsonify, send_from_directory
from capcut_tts_api import CapCutClient, CapCutError

app = Flask(__name__)

# Output directories
OUTPUT_DIR = Path(__file__).parent / "output_audio"
OUTPUT_DIR.mkdir(exist_ok=True)

PREVIEW_DIR = OUTPUT_DIR / "previews"
PREVIEW_DIR.mkdir(exist_ok=True)

VOICE_JSON_PATH = Path(__file__).parent / "Voice.json"
client = CapCutClient()

# Global job status store
JOBS = {}

def load_voices():
    if VOICE_JSON_PATH.exists():
        with open(VOICE_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def find_voice_info(voice_type):
    voices = load_voices()
    for v in voices:
        if v.get("voice_type") == voice_type or v.get("display_name") == voice_type:
            return v
    return None

def split_text_into_chunks(text, max_chars=180):
    """
    Split long text into sentence-aware chunks under max_chars.
    Ensures zero truncation and no mid-word cuts.
    """
    sentences = re.split(r'(?<=[.!?;\n])\s+', text.strip())
    chunks = []
    current_chunk = ''

    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(current_chunk) + len(s) + 1 <= max_chars:
            current_chunk = (current_chunk + ' ' + s).strip()
        else:
            if current_chunk:
                chunks.append(current_chunk)
            if len(s) > max_chars:
                words = s.split(' ')
                sub = ''
                for w in words:
                    if len(sub) + len(w) + 1 <= max_chars:
                        sub = (sub + ' ' + w).strip()
                    else:
                        if sub:
                            chunks.append(sub)
                        sub = w
                current_chunk = sub if sub else ''
            else:
                current_chunk = s

    if current_chunk:
        chunks.append(current_chunk)
    return chunks if chunks else [text]

def run_tts_job(job_id, text, voice, resource_id, rate):
    """
    Background worker thread to generate TTS chunks and merge into a single MP3 file.
    """
    try:
        chunks = split_text_into_chunks(text, max_chars=180)
        total_chunks = len(chunks)

        JOBS[job_id] = {
            "status": "processing",
            "progress": 5,
            "message": f"Đã chia văn bản thành {total_chunks} đoạn nhỏ...",
            "total_chunks": total_chunks,
            "current_chunk": 0,
            "result": None
        }

        all_mp3_bytes = []
        total_duration_ms = 0

        for idx, chunk_text in enumerate(chunks):
            chunk_num = idx + 1
            progress_pct = int((idx / total_chunks) * 80) + 10
            
            JOBS[job_id]["progress"] = progress_pct
            JOBS[job_id]["current_chunk"] = chunk_num
            JOBS[job_id]["message"] = f"Đang xử lý đoạn {chunk_num}/{total_chunks}..."

            # Retry up to 3 times per chunk
            chunk_speech_url = None
            chunk_duration = 0

            for retry in range(3):
                try:
                    create_res = client.create_tts_task(texts=chunk_text, voice=voice, resource_id=resource_id, rate=rate)
                    tasks = (create_res.get("data") or {}).get("tasks") or []
                    if not tasks:
                        time.sleep(1)
                        continue

                    task_id = tasks[0]["id"]
                    token = tasks[0]["token"]

                    for attempt in range(15):
                        query_res = client.query_tts_task(task_id, token)
                        query_tasks = (query_res.get("data") or {}).get("tasks") or []
                        if query_tasks:
                            qtask = query_tasks[0]
                            if qtask.get("status") in ("succeed", "success"):
                                payload_data = json.loads(qtask.get("payload", "{}"))
                                subtitles = payload_data.get("audio_subtitles", [])
                                if subtitles:
                                    chunk_speech_url = subtitles[0].get("speech_url")
                                    chunk_duration = subtitles[0].get("duration", 0)
                                break
                            elif qtask.get("status") in ("failed", "error"):
                                break
                        time.sleep(0.8)

                    if chunk_speech_url:
                        break
                except Exception as ex:
                    print(f"Error chunk {chunk_num} retry {retry}: {ex}")
                    time.sleep(1)

            if not chunk_speech_url:
                JOBS[job_id]["status"] = "error"
                JOBS[job_id]["message"] = f"Lỗi xử lý đoạn {chunk_num}/{total_chunks}. Vui lòng thử lại."
                return

            # Download chunk MP3
            mp3_resp = requests.get(chunk_speech_url, timeout=30)
            if mp3_resp.status_code == 200:
                all_mp3_bytes.append(mp3_resp.content)
                total_duration_ms += chunk_duration

        # Merge all chunks
        JOBS[job_id]["progress"] = 92
        JOBS[job_id]["message"] = "Đang ghép nối các tệp MP3..."

        combined_data = b"".join(all_mp3_bytes)
        filename = f"tts_{job_id}_{int(time.time())}.mp3"
        local_path = OUTPUT_DIR / filename

        with open(local_path, "wb") as f:
            f.write(combined_data)

        JOBS[job_id]["progress"] = 100
        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["message"] = "Tạo âm thanh hoàn tất 100%!"
        JOBS[job_id]["result"] = {
            "filename": filename,
            "download_url": f"/output/{filename}",
            "duration_ms": total_duration_ms,
            "text": text,
            "voice": voice
        }

    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["message"] = f"Lỗi hệ thống: {str(e)}"

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/voices", methods=["GET"])
def get_voices():
    voices = load_voices()
    return jsonify({"status": "success", "voices": voices})

@app.route("/api/generate_job", methods=["POST"])
def generate_job():
    try:
        data = request.json or {}
        text = data.get("text", "").strip()
        voice = data.get("voice", "BV421_vivn_streaming")
        resource_id = data.get("resource_id", None)
        rate = data.get("rate", "1.0")

        if not text:
            return jsonify({"status": "error", "message": "Vui lòng nhập văn bản cần đọc."}), 400

        vinfo = find_voice_info(voice)
        if vinfo and not resource_id:
            resource_id = vinfo.get("resource_id")

        job_id = str(uuid.uuid4())[:8]
        JOBS[job_id] = {
            "status": "queued",
            "progress": 0,
            "message": "Khởi tạo tiến trình...",
            "result": None
        }

        # Start thread
        t = threading.Thread(target=run_tts_job, args=(job_id, text, voice, resource_id, rate), daemon=True)
        t.start()

        return jsonify({"status": "success", "job_id": job_id})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/job_status/<job_id>", methods=["GET"])
def get_job_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"status": "error", "message": "Job không tồn tại"}), 404
    return jsonify(job)

@app.route("/api/preview_voice", methods=["POST"])
def preview_voice():
    try:
        data = request.json or {}
        voice = data.get("voice", "BV421_vivn_streaming")
        lan = (data.get("lan") or "vi").lower()

        vinfo = find_voice_info(voice)
        resource_id = vinfo.get("resource_id") if vinfo else None

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

        create_res = client.create_tts_task(texts=sample_text, voice=voice, resource_id=resource_id, rate="1.0")
        tasks = (create_res.get("data") or {}).get("tasks") or []
        if not tasks:
            return jsonify({"status": "error", "message": "Không thể kết nối CapCut API"}), 500

        task_id = tasks[0]["id"]
        token = tasks[0]["token"]

        speech_url = None
        for attempt in range(18):
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
            return jsonify({"status": "error", "message": "Giọng đọc này đang bận hoặc quá tải. Vui lòng thử giọng khác!"}), 500

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
