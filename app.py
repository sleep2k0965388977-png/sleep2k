import os
import re
import json
import time
import uuid
import asyncio
import io
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import requests
import subprocess
import tempfile
import soundfile as sf
import edge_tts
import speech_recognition as sr
from flask import Flask, render_template, request, jsonify, send_from_directory
from capcut_tts_api import CapCutClient, CapCutError

# ── VieNeu-TTS Official Neural Model (20 distinct preset voices) ──
_vieneu_tts_instance = None
_vieneu_lock = threading.Lock()

VIENEU_PRESET_MAP = {
    "vieneu_truc_ly": "Trúc Ly",
    "vieneu_ngoc_linh": "Ngọc Linh",
    "vieneu_doan_trang": "Đoan Trang",
    "vieneu_mai_anh": "Mai Anh",
    "vieneu_quynh_anh": "Quỳnh Anh",
    "vieneu_ngoc_huyen": "Ngọc Huyền",
    "vieneu_thuy_dung": "Thùy Dung",
    "vieneu_thuc_doan": "Thục Đoan",
    "vieneu_my_duyen": "Mỹ Duyên",
    "vieneu_kim_thanh": "Kim Thanh",
    "vieneu_ngoc_tran": "Ngọc Trân",
    "vieneu_minh_duc": "Minh Đức",
    "vieneu_pham_tuyen": "Phạm Tuyên",
    "vieneu_thanh_binh": "Thanh Bình",
    "vieneu_thai_son": "Thái Sơn",
    "vieneu_xuan_vinh": "Xuân Vĩnh",
    "vieneu_minh_triet": "Minh Triết",
    "vieneu_duc_tri": "Đức Trí",
    "vieneu_adam": "Adam",
    "vieneu_quang_son": "Quang Sơn",
}

VIENEU_SAMPLE_TEXTS = {
    "vieneu_truc_ly": "Xin chào, tôi là Trúc Ly, giọng đọc tự nhiên miền Bắc.",
    "vieneu_ngoc_linh": "Ngày xửa ngày xưa, ở một ngôi làng nhỏ bên triền đồi xanh mát...",
    "vieneu_doan_trang": "Chào bạn, tôi là Đoan Trang, rất vui được đồng hành cùng bạn.",
    "vieneu_mai_anh": "Kính chào quý vị, đây là bản tin thời sự hôm nay.",
    "vieneu_quynh_anh": "Đêm đã về khuya, không gian yên tĩnh và lắng đọng từng trang sách.",
    "vieneu_ngoc_huyen": "Xin chào, tôi là Ngọc Huyền, giọng đọc nhẹ nhàng và trong trẻo.",
    "vieneu_thuy_dung": "Xin kính chào quý khán giả đang theo dõi bản tin phát thanh trực tiếp.",
    "vieneu_thuc_doan": "Hôm nay em xin gửi tới quý thính giả một câu chuyện thật ấm áp.",
    "vieneu_my_duyen": "Gió thoảng qua rặng dừa xanh, sông nước miền Tây êm đềm trôi.",
    "vieneu_kim_thanh": "Kính mời quý thính giả cùng lắng nghe trọn vẹn chương truyện sau đây.",
    "vieneu_ngoc_tran": "Dạ em chào anh chị, giọng em là giọng Huế miền Trung thương nhớ.",
    "vieneu_minh_duc": "Kính chào quý vị và các bạn đang theo dõi bản tin thời sự truyền hình.",
    "vieneu_pham_tuyen": "Xin chào tất cả các bạn, chúc các bạn một ngày làm việc thật hiệu quả.",
    "vieneu_thanh_binh": "Trong ký ức của tôi, những ngày tháng tuổi thơ ấy thật khó phai mờ.",
    "vieneu_thai_son": "Chào bà con cô bác, bữa nay tôi xin kể cho bà con nghe một câu chuyện vui.",
    "vieneu_xuan_vinh": "Chào bạn nha, đây là giọng đọc miền Nam gần gũi và mộc mạc.",
    "vieneu_minh_triet": "Chào quý khán giả, chương trình tiêu điểm kinh tế hôm nay xin được bắt đầu.",
    "vieneu_duc_tri": "Bóng đêm dần buông xuống cánh đồng bao la, chỉ còn tiếng dế kêu rả rích.",
    "vieneu_adam": "Xin chào các bạn, chúc các bạn có những giây phút trải nghiệm tuyệt vời.",
    "vieneu_quang_son": "Chào bà con miền Trung khúc ruột thân thương, chúc mọi người luôn bình an.",
}

LANGUAGE_FALLBACK_VOICE = {
    "vi": "vi-VN-HoaiMyNeural",
    "en": "en-US-JennyNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ja": "ja-JP-NanamiNeural",
    "jp": "ja-JP-NanamiNeural",
    "th": "th-TH-PremwadeeNeural",
    "id": "id-ID-GadisNeural",
    "pt": "pt-BR-FranciscaNeural",
    "br": "pt-BR-FranciscaNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "es": "es-ES-ElviraNeural",
}

def get_vieneu_tts():
    global _vieneu_tts_instance
    if _vieneu_tts_instance is None:
        with _vieneu_lock:
            if _vieneu_tts_instance is None:
                from vieneu.v3turbo import V3TurboVieNeuTTS
                _vieneu_tts_instance = V3TurboVieNeuTTS()
    return _vieneu_tts_instance

def is_vieneu_voice(voice_type):
    """Check if a voice_type is a VieNeu AI preset."""
    return voice_type and (voice_type.startswith("vieneu_") or voice_type in VIENEU_PRESET_MAP.values())

def is_edge_tts_voice(voice_type):
    """Check if a voice_type is an Edge-TTS neural voice (e.g. vi-VN-HoaiMyNeural, Nam Minh)."""
    return bool(voice_type and ("Neural" in str(voice_type) or str(voice_type).startswith("edge_")))

def edge_tts_synthesize_audio(text, voice_type, rate="1.0"):
    """Synthesize voice using Microsoft Edge-TTS."""
    try:
        rate_val = float(rate)
    except Exception:
        rate_val = 1.0
    rate_pct = int((rate_val - 1.0) * 100)
    rate_str = f"{rate_pct:+d}%"

    async def _gen():
        comm = edge_tts.Communicate(text, voice_type, rate=rate_str)
        buf = io.BytesIO()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        return buf.getvalue()

    return asyncio.run(_gen())

def vieneu_synthesize_audio(text, voice_type):
    """Generate true distinct neural MP3 audio using official VieNeu-TTS v3 Turbo model at 48kHz."""
    preset_name = VIENEU_PRESET_MAP.get(voice_type, voice_type)
    tts = get_vieneu_tts()
    with _vieneu_lock:
        audio = tts.infer(text=text, voice=preset_name, denoise=True)
    
    buf = io.BytesIO()
    sf.write(buf, audio, tts.sample_rate, format="WAV")
    wav_bytes = buf.getvalue()
    
    try:
        proc = subprocess.Popen(
            ["ffmpeg", "-y", "-i", "pipe:0", "-f", "mp3", "-b:a", "192k", "pipe:1"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        mp3_bytes, _ = proc.communicate(input=wav_bytes)
        if mp3_bytes and len(mp3_bytes) > 0:
            return mp3_bytes
    except Exception as ex:
        print(f"FFmpeg MP3 convert warning: {ex}")
    
    return wav_bytes

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB upload limit

# Temporary output directory (files auto-cleaned on each new generation)
OUTPUT_DIR = Path(__file__).parent / "output_audio"
OUTPUT_DIR.mkdir(exist_ok=True)

PREVIEW_DIR = OUTPUT_DIR / "previews"
PREVIEW_DIR.mkdir(exist_ok=True)

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

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

def normalize_text_input(text):
    """Clean and normalize input text for robust, error-free TTS generation."""
    if not text:
        return ""
    text = text.replace("…", "...").replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    text = text.replace("–", "-").replace("—", "-")
    text = "".join(ch for ch in text if ch in ('\n', '\t') or ord(ch) >= 32)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def split_text_into_chunks(text, max_chars=180):
    """Split text intelligently at punctuation and word boundaries."""
    text = normalize_text_input(text)
    if not text:
        return []

    sentences = re.split(r'(?<=[.!?;\n])\s+', text)
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

def cleanup_all_temp_files(max_age_seconds=600):
    """Auto-delete generated audio older than 10 minutes and purge any leftover uploads to guarantee Zero-Storage footprint."""
    now = time.time()
    for p in OUTPUT_DIR.glob("tts_*.mp3"):
        try:
            if now - p.stat().st_mtime > max_age_seconds:
                p.unlink(missing_ok=True)
        except Exception:
            pass
    for p in UPLOAD_DIR.glob("*"):
        try:
            if now - p.stat().st_mtime > 300:
                p.unlink(missing_ok=True)
        except Exception:
            pass
    for p in OUTPUT_DIR.glob("temp_*"):
        try:
            if p.is_dir() and now - p.stat().st_mtime > 300:
                for sub in p.glob("*"):
                    sub.unlink(missing_ok=True)
                p.rmdir()
        except Exception:
            pass

def stitch_audio_chunks(chunk_bytes_list, output_file_path):
    """
    Concatenate audio chunks seamlessly using FFmpeg.
    Standardizes sample rate to 48kHz, 192kbps MP3 without clicks/pops.
    Falls back to binary join if FFmpeg fails.
    """
    if not chunk_bytes_list:
        return False
        
    if len(chunk_bytes_list) == 1:
        with open(output_file_path, "wb") as f:
            f.write(chunk_bytes_list[0])
        return True

    temp_dir = OUTPUT_DIR / f"temp_{uuid.uuid4().hex[:8]}"
    temp_dir.mkdir(exist_ok=True)
    try:
        concat_list_file = temp_dir / "concat_list.txt"
        with open(concat_list_file, "w", encoding="utf-8") as f_list:
            for i, chunk_bytes in enumerate(chunk_bytes_list):
                chunk_file = temp_dir / f"chunk_{i:04d}.mp3"
                with open(chunk_file, "wb") as fc:
                    fc.write(chunk_bytes)
                f_list.write(f"file '{chunk_file.resolve().as_posix()}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_file),
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            "-ar", "48000",
            str(output_file_path)
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        if proc.returncode == 0 and output_file_path.exists() and output_file_path.stat().st_size > 0:
            return True
    except Exception as ex:
        print(f"FFmpeg stitch warning: {ex}")
    finally:
        try:
            for p in temp_dir.glob("*"):
                p.unlink(missing_ok=True)
            temp_dir.rmdir()
        except Exception:
            pass

    # Direct fallback
    try:
        combined = b"".join(chunk_bytes_list)
        with open(output_file_path, "wb") as f:
            f.write(combined)
        return True
    except Exception as ex:
        print(f"Binary concat error: {ex}")
        return False

def fetch_chunk_audio(idx, text_chunk, voice, resource_id, rate, lan="vi"):
    # ── 1. VieNeu AI voices: use official VieNeu-TTS v3 Turbo model (48kHz) ──
    if is_vieneu_voice(voice):
        try:
            audio_bytes = vieneu_synthesize_audio(text_chunk, voice)
            if audio_bytes and len(audio_bytes) > 0:
                est_duration = int(len(text_chunk) / 150 * 1000)
                return idx, audio_bytes, est_duration
        except Exception as ex:
            print(f"VieNeu TTS error chunk {idx+1}: {ex}")
        try:
            fb = LANGUAGE_FALLBACK_VOICE.get(lan, "vi-VN-HoaiMyNeural")
            audio_bytes = edge_tts_synthesize_audio(text_chunk, fb, rate=rate)
            if audio_bytes and len(audio_bytes) > 0:
                return idx, audio_bytes, int(len(text_chunk) / 150 * 1000)
        except Exception:
            pass
        return idx, None, 0

    # ── 2. Edge-TTS Neural voices (e.g. Hoai My, Nam Minh, Jenny) ──
    if is_edge_tts_voice(voice):
        try:
            audio_bytes = edge_tts_synthesize_audio(text_chunk, voice, rate=rate)
            if audio_bytes and len(audio_bytes) > 0:
                est_duration = int(len(text_chunk) / 150 * 1000)
                return idx, audio_bytes, est_duration
        except Exception as ex:
            print(f"Edge TTS error chunk {idx+1}: {ex}")
        return idx, None, 0

    # ── 3. Original CapCut voices with automatic retry ──
    for retry in range(3):
        try:
            create_res = client.create_tts_task(texts=text_chunk, voice=voice, resource_id=resource_id, rate=rate)
            tasks = (create_res.get("data") or {}).get("tasks") or []
            if not tasks:
                time.sleep(0.6)
                continue

            task_id, token = tasks[0]["id"], tasks[0]["token"]

            for attempt in range(16):
                query_res = client.query_tts_task(task_id, token)
                query_tasks = (query_res.get("data") or {}).get("tasks") or []
                if query_tasks:
                    qtask = query_tasks[0]
                    qstatus = qtask.get("status")
                    if qstatus in ("succeed", "success"):
                        payload_data = json.loads(qtask.get("payload", "{}"))
                        subtitles = payload_data.get("audio_subtitles", [])
                        if subtitles:
                            speech_url = subtitles[0].get("speech_url")
                            duration = subtitles[0].get("duration", 0)
                            resp = requests.get(speech_url, timeout=25)
                            if resp.status_code == 200:
                                return idx, resp.content, duration
                        break
                    elif qstatus in ("failed", "error"):
                        break
                time.sleep(0.5)
        except Exception as ex:
            print(f"Error processing chunk {idx+1} (retry {retry+1}): {ex}")
            time.sleep(0.6)

    # ── 4. Multilingual Fallback ──
    try:
        fallback_voice = LANGUAGE_FALLBACK_VOICE.get(lan, "vi-VN-HoaiMyNeural")
        print(f"CapCut fallback chunk {idx+1} voice={voice} -> Edge-TTS {fallback_voice}")
        audio_bytes = edge_tts_synthesize_audio(text_chunk, fallback_voice, rate=rate)
        if audio_bytes and len(audio_bytes) > 0:
            est_duration = int(len(text_chunk) / 150 * 1000)
            return idx, audio_bytes, est_duration
    except Exception as ex2:
        print(f"Fallback failed chunk {idx+1}: {ex2}")

    return idx, None, 0

def run_tts_job(job_id, text, voice, resource_id, rate, lan="vi"):
    try:
        cleanup_all_temp_files()

        chunks = split_text_into_chunks(text, max_chars=180)
        total_chunks = len(chunks)

        if total_chunks == 0:
            JOBS[job_id] = {"status": "error", "message": "Văn bản rỗng hoặc không hợp lệ."}
            return

        JOBS[job_id] = {
            "status": "processing",
            "progress": 5,
            "message": f"Đã chuẩn hóa văn bản ({len(text):,} ký tự) thành {total_chunks} đoạn...",
            "total_chunks": total_chunks,
            "completed_chunks": 0,
            "result": None,
        }

        chunk_results = [None] * total_chunks
        durations = [0] * total_chunks
        completed_count = 0

        # Run chunk requests concurrently with safe thread pool
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(fetch_chunk_audio, i, chunk_text, voice, resource_id, rate, lan)
                for i, chunk_text in enumerate(chunks)
            ]

            for future in as_completed(futures):
                idx, audio_data, duration_ms = future.result()
                if audio_data:
                    chunk_results[idx] = audio_data
                    durations[idx] = duration_ms
                    completed_count += 1

                    progress_pct = int((completed_count / total_chunks) * 85) + 10
                    JOBS[job_id]["progress"] = progress_pct
                    JOBS[job_id]["completed_chunks"] = completed_count
                    JOBS[job_id]["message"] = f"Đang tạo giọng đọc song song: {completed_count}/{total_chunks} đoạn ({progress_pct}%)..."
                else:
                    JOBS[job_id]["status"] = "error"
                    JOBS[job_id]["message"] = f"Không thể xử lý đoạn {idx+1}/{total_chunks}. Vui lòng thử lại!"
                    return

        JOBS[job_id]["progress"] = 96
        JOBS[job_id]["message"] = "Đang ghép nối mượt mà toàn bộ tệp âm thanh (FFmpeg 48kHz)..."

        valid_bytes = [r for r in chunk_results if r is not None]
        if len(valid_bytes) != total_chunks:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["message"] = "Một số đoạn âm thanh chưa hoàn tất."
            return

        total_duration_ms = sum(durations)
        filename = f"tts_{job_id}_{int(time.time())}.mp3"
        local_path = OUTPUT_DIR / filename

        # Stitch all chunks smoothly with FFmpeg
        stitch_ok = stitch_audio_chunks(valid_bytes, local_path)
        if not stitch_ok or not local_path.exists():
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["message"] = "Lỗi khi lưu tệp âm thanh hoàn chỉnh."
            return

        JOBS[job_id]["progress"] = 100
        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["message"] = "Hoàn tất 100%! Đã tạo xong giọng đọc chất lượng cao."
        JOBS[job_id]["result"] = {
            "filename": filename,
            "download_url": f"/output/{filename}",
            "duration_ms": total_duration_ms,
            "text_length": len(text),
            "total_chunks": total_chunks,
            "voice": voice
        }

    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["message"] = f"Lỗi hệ thống: {str(e)}"

@app.after_request
def add_cache_control_headers(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

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
        text = normalize_text_input(data.get("text", ""))
        voice = data.get("voice", "BV421_vivn_streaming")
        resource_id = data.get("resource_id", None)
        rate = data.get("rate", "1.0")

        if not text:
            return jsonify({"status": "error", "message": "Vui lòng nhập văn bản cần đọc."}), 400

        vinfo = find_voice_info(voice)
        if vinfo and not resource_id:
            resource_id = vinfo.get("resource_id")
        lan = vinfo.get("lan", "vi") if vinfo else "vi"

        job_id = str(uuid.uuid4())[:8]
        JOBS[job_id] = {
            "status": "queued",
            "progress": 0,
            "message": "Đang phân tích văn bản không giới hạn ký tự...",
            "result": None,
        }

        t = threading.Thread(
            target=run_tts_job,
            args=(job_id, text, voice, resource_id, rate, lan),
            daemon=True
        )
        t.start()

        return jsonify({"status": "success", "job_id": job_id})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/job_status/<job_id>", methods=["GET"])
def get_job_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({
            "status": "error",
            "error_type": "job_not_found",
            "message": "Tiến trình không tồn tại. Vui lòng bấm Tạo Giọng Đọc MP3 để thử lại!"
        }), 404
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

        # ── VieNeu AI voices: preview via official VieNeu model ──
        if is_vieneu_voice(voice):
            vieneu_sample = VIENEU_SAMPLE_TEXTS.get(voice, sample_text)
            try:
                audio_bytes = vieneu_synthesize_audio(vieneu_sample, voice)
                if audio_bytes and len(audio_bytes) > 0:
                    with open(preview_file_path, "wb") as f:
                        f.write(audio_bytes)
                    return jsonify({
                        "status": "success",
                        "download_url": f"/output/previews/{preview_filename}"
                    })
            except Exception as ex:
                return jsonify({"status": "error", "message": f"Lỗi VieNeu AI: {ex}"}), 500
            return jsonify({"status": "error", "message": "Không tạo được giọng VieNeu AI."}), 500

        # ── Edge-TTS Neural voices: preview via edge-tts ──
        if is_edge_tts_voice(voice):
            try:
                audio_bytes = edge_tts_synthesize_audio(sample_text, voice, rate="1.0")
                if audio_bytes and len(audio_bytes) > 0:
                    with open(preview_file_path, "wb") as f:
                        f.write(audio_bytes)
                    return jsonify({
                        "status": "success",
                        "download_url": f"/output/previews/{preview_filename}"
                    })
            except Exception as ex:
                return jsonify({"status": "error", "message": f"Lỗi Edge-TTS: {ex}"}), 500
            return jsonify({"status": "error", "message": "Không tạo được giọng Edge-TTS."}), 500

        # ── Original CapCut voices: unchanged logic ──
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
            time.sleep(0.6)

        if not speech_url:
            # ── Fallback: CapCut failed, use Edge-TTS instead of showing error ──
            try:
                fallback_voice = "vi-VN-HoaiMyNeural"
                print(f"CapCut preview failed for voice={voice}, fallback to Edge-TTS {fallback_voice}")
                audio_bytes = edge_tts_synthesize_audio(sample_text, fallback_voice, rate="1.0")
                if audio_bytes and len(audio_bytes) > 0:
                    with open(preview_file_path, "wb") as f:
                        f.write(audio_bytes)
                    return jsonify({
                        "status": "success",
                        "download_url": f"/output/previews/{preview_filename}"
                    })
            except Exception as fb_ex:
                print(f"Edge-TTS fallback also failed: {fb_ex}")
            return jsonify({"status": "error", "message": "Giọng đọc này đang bận hoặc quá tải. Vui lòng thử giọng khác!"}), 500

        mp3_resp = requests.get(speech_url, timeout=20)
        with open(preview_file_path, "wb") as f:
            f.write(mp3_resp.content)

        return jsonify({
            "status": "success",
            "download_url": f"/output/previews/{preview_filename}"
        })
    except Exception as e:
        # ── Last resort fallback: any unhandled error, try Edge-TTS ──
        try:
            fallback_voice = "vi-VN-HoaiMyNeural"
            audio_bytes = edge_tts_synthesize_audio(sample_text, fallback_voice, rate="1.0")
            if audio_bytes and len(audio_bytes) > 0:
                with open(preview_file_path, "wb") as f:
                    f.write(audio_bytes)
                return jsonify({
                    "status": "success",
                    "download_url": f"/output/previews/{preview_filename}"
                })
        except Exception:
            pass
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/health", methods=["GET", "HEAD"])
def health_check():
    return jsonify({
        "status": "healthy",
        "service": "SLEEP2K TTS & STT",
        "timestamp": int(time.time()),
        "uptime": "ok"
    }), 200

@app.route("/output/<filename>")
def serve_audio(filename):
    return send_from_directory(OUTPUT_DIR, filename)

@app.route("/output/previews/<filename>")
def serve_preview_audio(filename):
    return send_from_directory(PREVIEW_DIR, filename)

# ── Speech to Text (Audio/Video Transcription) Engine ──

def format_timestamp_srt(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def transcribe_audio_chunk(idx, chunk_path, language="vi-VN", max_retries=3):
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 300
    recognizer.dynamic_energy_threshold = True

    for attempt in range(1, max_retries + 1):
        try:
            target_path = chunk_path
            # Attempt 2+: apply FFmpeg dynamic volume normalization & noise filter
            if attempt > 1:
                filtered_path = chunk_path.parent / f"filt_{attempt}_{chunk_path.name}"
                cmd = [
                    "ffmpeg", "-y", "-i", str(chunk_path),
                    "-af", "dynaudnorm=f=150:g=15",
                    "-ar", "16000", "-ac", "1",
                    str(filtered_path)
                ]
                subprocess.run(cmd, capture_output=True, timeout=10)
                if filtered_path.exists():
                    target_path = filtered_path

            with sr.AudioFile(str(target_path)) as source:
                audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language=language)
            return idx, (text or "").strip()
        except Exception as ex:
            if attempt < max_retries:
                time.sleep(1.0)
                continue
            return idx, ""

def process_speech_to_text_job(job_id, file_path, language="vi-VN"):
    try:
        JOBS[job_id]["status"] = "processing"
        JOBS[job_id]["progress"] = 5
        JOBS[job_id]["message"] = "Đang phân tích tệp âm thanh bằng FFmpeg..."

        # 1. Probe audio duration
        duration = 0.0
        try:
            res = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)],
                capture_output=True,
                text=True,
                timeout=20
            )
            duration = float(res.stdout.strip())
        except Exception:
            duration = 30.0

        JOBS[job_id]["progress"] = 15
        JOBS[job_id]["message"] = f"Thời lượng: {int(duration)}s. Đang chia đoạn âm thanh..."

        # 2. Split audio into 15-second WAV segments
        temp_dir = Path(tempfile.mkdtemp(prefix="stt_"))
        segment_pattern = str(temp_dir / "chunk_%04d.wav")

        proc = subprocess.run([
            "ffmpeg", "-y",
            "-i", str(file_path),
            "-f", "segment",
            "-segment_time", "15",
            "-c:a", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            segment_pattern
        ], capture_output=True, timeout=180)

        chunk_files = sorted(list(temp_dir.glob("chunk_*.wav")))
        if not chunk_files:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["message"] = "Không thể đọc dữ liệu âm thanh từ tệp tải lên."
            return

        total_chunks = len(chunk_files)
        results = [""] * total_chunks
        completed_count = 0

        # 3. Transcribe chunks with ThreadPoolExecutor & 10s watchdog auto-repair
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_chunk = {
                executor.submit(transcribe_audio_chunk, i, chunk_path, language): (i, chunk_path)
                for i, chunk_path in enumerate(chunk_files)
            }

            for future in as_completed(future_to_chunk):
                i, chunk_path = future_to_chunk[future]
                try:
                    idx, text = future.result(timeout=12)
                    results[idx] = text
                except Exception as ex:
                    print(f"Watchdog auto-recovery chunk {i+1}: {ex}, retrying...")
                    # Immediate recovery retry
                    try:
                        idx, text = transcribe_audio_chunk(i, chunk_path, language, max_retries=2)
                        results[idx] = text
                    except Exception:
                        results[i] = ""

                completed_count += 1
                prog = int(15 + (completed_count / total_chunks) * 80)
                JOBS[job_id]["progress"] = prog
                JOBS[job_id]["message"] = f"Đang nhận diện giọng nói AI: {completed_count}/{total_chunks} đoạn ({prog}%)..."

        # 4. Build full text and SRT subtitles
        valid_texts = []
        srt_blocks = []
        srt_idx = 1

        for idx, text in enumerate(results):
            if text:
                valid_texts.append(text)
                start_sec = idx * 15.0
                end_sec = min((idx + 1) * 15.0, duration) if duration > 0 else (idx + 1) * 15.0
                srt_block = f"{srt_idx}\n{format_timestamp_srt(start_sec)} --> {format_timestamp_srt(end_sec)}\n{text}\n"
                srt_blocks.append(srt_block)
                srt_idx += 1

        full_text = "\n\n".join(valid_texts) if valid_texts else "Không nhận diện được giọng nói trong tệp này."
        srt_content = "\n".join(srt_blocks) if srt_blocks else ""

        # Cleanup temp directory and uploaded file
        try:
            for p in temp_dir.glob("*"):
                p.unlink(missing_ok=True)
            temp_dir.rmdir()
            if file_path.exists():
                file_path.unlink(missing_ok=True)
        except Exception:
            pass

        words = [w for w in full_text.split() if w]
        JOBS[job_id]["progress"] = 100
        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["message"] = "Hoàn tất chuyển đổi âm thanh thành văn bản 100%!"
        JOBS[job_id]["result"] = {
            "text": full_text,
            "srt": srt_content,
            "duration": round(duration, 1),
            "word_count": len(words),
            "char_count": len(full_text),
            "total_chunks": total_chunks,
            "language": language
        }
    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["message"] = f"Lỗi xử lý âm thanh: {str(e)}"

@app.route("/api/transcribe_job", methods=["POST"])
def api_transcribe_job():
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "Vui lòng chọn tệp âm thanh hoặc video!"}), 400

    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"status": "error", "message": "Tệp không hợp lệ!"}), 400

    language = request.form.get("language", "vi-VN")
    job_id = uuid.uuid4().hex[:8]

    ext = Path(file.filename).suffix.lower() or ".mp3"
    saved_filename = f"upload_{job_id}{ext}"
    saved_path = UPLOAD_DIR / saved_filename
    file.save(str(saved_path))

    JOBS[job_id] = {
        "status": "queued",
        "progress": 0,
        "message": "Đang xếp hàng xử lý tệp âm thanh...",
        "result": None
    }

    t = threading.Thread(target=process_speech_to_text_job, args=(job_id, saved_path, language))
    t.daemon = True
    t.start()

    return jsonify({"status": "success", "job_id": job_id})

@app.route("/api/transcribe_status/<job_id>", methods=["GET"])
def api_transcribe_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"status": "error", "message": "Không tìm thấy tiến trình chuyển đổi."}), 404
    return jsonify(job)


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # Clean up any leftover temporary files on startup
    cleanup_all_temp_files()
    print("CapCut TTS Web Interface running at http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
