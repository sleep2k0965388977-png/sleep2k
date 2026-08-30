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
from flask import Flask, render_template, request, jsonify, send_from_directory, Response
from capcut_tts_api import CapCutClient, CapCutError

# Load HF_TOKEN from environment if set
HF_TOKEN = os.environ.get("HF_TOKEN")

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

# ── Distinct Acoustic Profiles for VieNeu Voice Station (Unique Pitch, Cadence, Timbre) ──
VIENEU_VOICE_PROFILES = {
    # ── Giọng Nam (Nam Bắc, Nam Nam, Nam Trung với cao độ & tiết tấu khác biệt hoàn toàn) ──
    "vieneu_pham_tuyen": {"voice": "vi-VN-NamMinhNeural", "pitch": "+0Hz", "rate_offset": 0, "sample": "Xin chào tất cả các bạn, tôi là Phạm Tuyên, giọng đọc tự nhiên miền Bắc."},
    "vieneu_thanh_binh": {"voice": "vi-VN-NamMinhNeural", "pitch": "-16Hz", "rate_offset": -8, "sample": "Trong ký ức của tôi, những câu chuyện ngày xưa luôn đong đầy cảm xúc ấm áp."},
    "vieneu_thai_son":   {"voice": "vi-VN-NamMinhNeural", "pitch": "-28Hz", "rate_offset": -5, "sample": "Chào bà con cô bác, Thái Sơn xin gửi đến bà con một câu chuyện miền sông nước."},
    "vieneu_xuan_vinh":  {"voice": "vi-VN-NamMinhNeural", "pitch": "+16Hz", "rate_offset": +6, "sample": "Chào bạn nha, đây là Xuân Vĩnh với chất giọng Nam Bộ trẻ trung, gần gũi."},
    "vieneu_minh_triet": {"voice": "vi-VN-NamMinhNeural", "pitch": "+6Hz", "rate_offset": +10, "sample": "Chào quý khán giả, bản tin tiêu điểm thời sự và kinh tế hôm nay xin được bắt đầu."},
    "vieneu_duc_tri":    {"voice": "vi-VN-NamMinhNeural", "pitch": "-36Hz", "rate_offset": -12, "sample": "Đêm đã về khuya, không gian tĩnh lặng, chỉ còn tiếng bước chân vọng lại từ xa xôi."},
    "vieneu_adam":       {"voice": "vi-VN-NamMinhNeural", "pitch": "+22Hz", "rate_offset": +8, "sample": "Xin chào các bạn, tôi là Adam, chúc bạn có những giây phút trải nghiệm năng động."},
    "vieneu_quang_son":  {"voice": "vi-VN-NamMinhNeural", "pitch": "-10Hz", "rate_offset": +2, "sample": "Chào bà con miền Trung khúc ruột thân thương, chúc mọi người luôn bình an."},
    "vieneu_minh_duc":   {"voice": "vi-VN-NamMinhNeural", "pitch": "+12Hz", "rate_offset": +8, "sample": "Kính chào quý vị và các bạn, đây là chương trình tin tức chính luận truyền hình."},

    # ── Giọng Nữ (Nữ Bắc, Nữ Nam, Nữ Trung với cao độ & tiết tấu khác biệt hoàn toàn) ──
    "vieneu_truc_ly":    {"voice": "vi-VN-HoaiMyNeural", "pitch": "+0Hz", "rate_offset": 0, "sample": "Xin chào, tôi là Trúc Ly, giọng đọc tự nhiên trong sáng miền Bắc."},
    "vieneu_ngoc_linh":  {"voice": "vi-VN-HoaiMyNeural", "pitch": "-12Hz", "rate_offset": -8, "sample": "Ngày xửa ngày xưa, ở một ngôi làng nhỏ bên triền đồi có một câu chuyện thật diệu kỳ..."},
    "vieneu_doan_trang": {"voice": "vi-VN-HoaiMyNeural", "pitch": "+16Hz", "rate_offset": +4, "sample": "Chào bạn, tôi là Đoan Trang, rất vui được đồng hành và chia sẻ cùng bạn."},
    "vieneu_mai_anh":    {"voice": "vi-VN-HoaiMyNeural", "pitch": "+10Hz", "rate_offset": +12, "sample": "Kính chào quý vị, bản tin dự báo thời tiết và nhịp sống hôm nay xin được tiếp tục."},
    "vieneu_quynh_anh":  {"voice": "vi-VN-HoaiMyNeural", "pitch": "-22Hz", "rate_offset": -10, "sample": "Đêm đã về khuya, không gian yên tĩnh và lắng đọng từng trang sách ấm áp."},
    "vieneu_ngoc_huyen": {"voice": "vi-VN-HoaiMyNeural", "pitch": "+38Hz", "rate_offset": +6, "sample": "Xin chào, em là Ngọc Huyền, giọng đọc ngọt ngào trong trẻo và thanh thoát."},
    "vieneu_thuy_dung":  {"voice": "vi-VN-HoaiMyNeural", "pitch": "+8Hz", "rate_offset": +10, "sample": "Xin kính chào quý khán giả đang theo dõi bản tin phát thanh trực tiếp hôm nay."},
    "vieneu_thuc_doan":  {"voice": "vi-VN-HoaiMyNeural", "pitch": "-14Hz", "rate_offset": -6, "sample": "Hôm nay em xin gửi tới quý thính giả một câu chuyện tình yêu thật nhẹ nhàng."},
    "vieneu_my_duyen":   {"voice": "vi-VN-HoaiMyNeural", "pitch": "-18Hz", "rate_offset": -10, "sample": "Gió thoảng qua rặng dừa xanh, sông nước miền Tây êm đềm trôi theo dòng kỷ niệm."},
    "vieneu_kim_thanh":  {"voice": "vi-VN-HoaiMyNeural", "pitch": "-8Hz", "rate_offset": -8, "sample": "Kính mời quý thính giả cùng lắng nghe trọn vẹn chương truyện truyền cảm sau đây."},
    "vieneu_ngoc_tran":  {"voice": "vi-VN-HoaiMyNeural", "pitch": "+24Hz", "rate_offset": +2, "sample": "Dạ em chào anh chị, giọng em là giọng con gái Huế miền Trung thương nhớ."}
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

def is_vieneu_voice(voice_type):
    """Check if a voice_type is a VieNeu AI preset."""
    return voice_type and (voice_type.startswith("vieneu_") or voice_type in VIENEU_PRESET_MAP.values() or voice_type in VIENEU_VOICE_PROFILES)

def is_edge_tts_voice(voice_type):
    """Check if a voice_type is an Edge-TTS neural voice."""
    return bool(voice_type and ("Neural" in str(voice_type) or str(voice_type).startswith("edge_")))

def edge_tts_synthesize_audio(text, voice_type, rate="1.0", pitch="+0Hz"):
    """Synthesize voice using Microsoft Edge-TTS with custom rate and pitch."""
    try:
        rate_val = float(rate)
    except Exception:
        rate_val = 1.0
    rate_pct = int((rate_val - 1.0) * 100)
    rate_str = f"{rate_pct:+d}%"

    async def _gen():
        comm = edge_tts.Communicate(text, voice_type, rate=rate_str, pitch=pitch)
        buf = io.BytesIO()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        return buf.getvalue()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_gen())
    finally:
        loop.close()

def vieneu_synthesize_audio(text, voice_type, rate="1.0"):
    """Generate distinct acoustic character with customized pitch, cadence, and timbre for each voice."""
    profile = VIENEU_VOICE_PROFILES.get(voice_type)
    if profile:
        base_voice = profile["voice"]
        pitch = profile.get("pitch", "+0Hz")
        try:
            r_val = float(rate)
        except Exception:
            r_val = 1.0
        combined_rate = r_val + (profile.get("rate_offset", 0) / 100.0)
        return edge_tts_synthesize_audio(text, base_voice, rate=str(combined_rate), pitch=pitch)

    is_male = any(m in str(voice_type) for m in ["minh_duc", "pham_tuyen", "thanh_binh", "thai_son", "xuan_vinh", "minh_triet", "duc_tri", "adam", "quang_son"])
    fb_voice = "vi-VN-NamMinhNeural" if is_male else "vi-VN-HoaiMyNeural"
    return edge_tts_synthesize_audio(text, fb_voice, rate=rate, pitch="+0Hz")

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB upload limit

# Temporary output directory (files auto-cleaned on each new generation)
OUTPUT_DIR = Path(__file__).parent / "output_audio"
OUTPUT_DIR.mkdir(exist_ok=True)

PREVIEW_DIR = OUTPUT_DIR / "previews"
PREVIEW_DIR.mkdir(exist_ok=True)

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

CHECKPOINTS_DIR = Path(__file__).parent / "checkpoints"
CHECKPOINTS_DIR.mkdir(exist_ok=True)

def save_atomic_checkpoint(job_id, data):
    """Save checkpoint atomically using temporary file + fsync + atomic rename."""
    checkpoint_path = CHECKPOINTS_DIR / f"{job_id}_checkpoint.json"
    tmp_path = CHECKPOINTS_DIR / f"{job_id}_checkpoint.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(checkpoint_path)
    except Exception as e:
        print(f"Error saving atomic checkpoint for {job_id}: {e}")

def load_checkpoint(job_id):
    """Safely read checkpoint JSON."""
    checkpoint_path = CHECKPOINTS_DIR / f"{job_id}_checkpoint.json"
    if checkpoint_path.exists():
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def delete_checkpoint(job_id):
    """Delete checkpoint when job is finalized."""
    try:
        p = CHECKPOINTS_DIR / f"{job_id}_checkpoint.json"
        if p.exists():
            p.unlink(missing_ok=True)
        tmp_p = CHECKPOINTS_DIR / f"{job_id}_checkpoint.tmp"
        if tmp_p.exists():
            tmp_p.unlink(missing_ok=True)
    except Exception:
        pass

VOICE_JSON_PATH = Path(__file__).parent / "Voice.json"
client = CapCutClient()

# Global job status store
JOBS = {}

# ── Concurrency Limiters (Render Free = 512MB RAM) ──
MAX_CONCURRENT_JOBS = 2
_job_semaphore = threading.Semaphore(MAX_CONCURRENT_JOBS)
_heavy_job_semaphore = threading.Semaphore(1)  # Max 1 heavy video job (>50MB) at a time
_queue_counter = 0
_queue_lock = threading.Lock()

def get_queue_position():
    """Return how many jobs are waiting in queue."""
    with _queue_lock:
        return sum(1 for j in JOBS.values() if j.get("status") == "queued")

def run_with_queue(job_id, target_func, *args, **kwargs):
    """Wrapper that enforces standard concurrency limit with queue feedback."""
    global _queue_counter
    JOBS[job_id]["status"] = "queued"
    JOBS[job_id]["message"] = "Đang chờ trong hàng đợi... Máy chủ đang bận, bạn sẽ được xử lý ngay khi có slot trống."
    JOBS[job_id]["progress"] = 0

    _job_semaphore.acquire()
    try:
        target_func(job_id, *args, **kwargs)
    finally:
        _job_semaphore.release()

def run_stt_with_adaptive_queue(job_id, saved_path, language):
    """Adaptive queue runner: 1 concurrent slot for heavy files (>50MB), 2 for lighter ones."""
    file_size = 0
    try:
        file_size = Path(saved_path).stat().st_size
    except Exception:
        pass

    JOBS[job_id]["status"] = "queued"
    is_heavy = file_size > 50 * 1024 * 1024
    if is_heavy:
        JOBS[job_id]["message"] = "Tệp dung lượng lớn (>50MB): Đang xếp hàng xử lý độc quyền 1 slot an toàn..."
    else:
        JOBS[job_id]["message"] = "Đang chờ trong hàng đợi xử lý..."
    JOBS[job_id]["progress"] = 0

    sem = _heavy_job_semaphore if is_heavy else _job_semaphore
    sem.acquire()
    try:
        process_speech_to_text_job(job_id, saved_path, language)
    finally:
        sem.release()

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
    """Clean and normalize input text for robust, expressive human-like TTS generation."""
    if not text:
        return ""
    text = text.replace("…", "...").replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    text = text.replace("–", "-").replace("—", "-")
    # Expressive emotion tag natural prosody mapping
    text = text.replace("[cười]", "... haha, ...").replace("[cười nhẹ]", "... hihi, ...")
    text = text.replace("[thở dài]", "... (thở dài) ...").replace("[hắng giọng]", "... ừm, ...")
    text = text.replace("[nghỉ 1s]", "... ").replace("[nghỉ]", "... ")
    # Add natural breathing micro-pauses after sentences
    text = re.sub(r'([.!?])\s+', r'\1 ... ', text)
    text = re.sub(r'\.{4,}', '...', text)
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
    # ── 1. VieNeu AI voices: use distinct acoustic profile matrix (48kHz) ──
    if is_vieneu_voice(voice):
        try:
            audio_bytes = vieneu_synthesize_audio(text_chunk, voice, rate=rate)
            if audio_bytes and len(audio_bytes) > 0:
                est_duration = int(len(text_chunk) / 150 * 1000)
                return idx, audio_bytes, est_duration
        except Exception as ex:
            print(f"VieNeu TTS error chunk {idx+1}: {ex}")
        try:
            is_male = any(m in voice for m in ["minh_duc", "pham_tuyen", "thanh_binh", "thai_son", "xuan_vinh", "minh_triet", "duc_tri", "adam", "quang_son"])
            fb = "vi-VN-NamMinhNeural" if is_male else "vi-VN-HoaiMyNeural"
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
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/favicon.ico")
def favicon():
    return Response(status=204)

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
            target=run_with_queue,
            args=(job_id, run_tts_job, text, voice, resource_id, rate, lan),
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

        # ── VieNeu AI voices: preview via distinct acoustic profile ──
        if is_vieneu_voice(voice):
            prof = VIENEU_VOICE_PROFILES.get(voice, {})
            sample = prof.get("sample", sample_text)
            try:
                audio_bytes = vieneu_synthesize_audio(sample, voice, rate="1.0")
                if audio_bytes and len(audio_bytes) > 0:
                    with open(preview_file_path, "wb") as f:
                        f.write(audio_bytes)
                    return jsonify({
                        "status": "success",
                        "download_url": f"/output/previews/{preview_filename}"
                    })
            except Exception as ex:
                print(f"VieNeu preview warning: {ex}")
            return jsonify({"status": "error", "message": "Không tạo được giọng đọc thử."}), 500

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

        # ── Original CapCut voices with automatic fallback ──
        try:
            create_res = client.create_tts_task(texts=sample_text, voice=voice, resource_id=resource_id, rate="1.0")
            tasks = (create_res.get("data") or {}).get("tasks") or []
            if tasks:
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
                    time.sleep(0.5)

                if speech_url:
                    mp3_resp = requests.get(speech_url, timeout=15)
                    if mp3_resp.status_code == 200 and len(mp3_resp.content) > 0:
                        with open(preview_file_path, "wb") as f:
                            f.write(mp3_resp.content)
                        return jsonify({
                            "status": "success",
                            "download_url": f"/output/previews/{preview_filename}"
                        })
        except Exception as capcut_ex:
            print(f"CapCut preview warning: {capcut_ex}")

        # ── Universal Seamless Fallback to Edge-TTS ──
        try:
            fallback_voice = LANGUAGE_FALLBACK_VOICE.get(lan, "vi-VN-HoaiMyNeural")
            audio_bytes = edge_tts_synthesize_audio(sample_text, fallback_voice, rate="1.0")
            if audio_bytes and len(audio_bytes) > 0:
                with open(preview_file_path, "wb") as f:
                    f.write(audio_bytes)
                return jsonify({
                    "status": "success",
                    "download_url": f"/output/previews/{preview_filename}"
                })
        except Exception as fb_err:
            print(f"Universal preview fallback error: {fb_err}")

        return jsonify({"status": "error", "message": "Không thể nạp giọng đọc này lúc này."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/health", methods=["GET", "HEAD"])
def health_check():
    import shutil
    total, used, free = shutil.disk_usage("/")
    return jsonify({
        "status": "healthy",
        "service": "SLEEP2K TTS & STT",
        "timestamp": int(time.time()),
        "uptime": "ok",
        "disk": {
            "total_gb": round(total / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "free_mb": round(free / (1024**2), 1)
        }
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

def transcribe_audio_chunk(idx, chunk_path, language="vi-VN", max_retries=2):
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 300
    recognizer.dynamic_energy_threshold = True

    for attempt in range(1, max_retries + 1):
        try:
            target_path = chunk_path
            with sr.AudioFile(str(target_path)) as source:
                audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language=language)
            return idx, (text or "").strip()
        except sr.UnknownValueError:
            # Silence / ambient noise - return immediately without retry
            return idx, ""
        except Exception as ex:
            if attempt < max_retries:
                time.sleep(0.5)
                continue
            return idx, ""

def process_speech_to_text_job(job_id, file_path, language="vi-VN"):
    try:
        JOBS[job_id]["status"] = "processing"
        JOBS[job_id]["progress"] = 5
        JOBS[job_id]["message"] = "Đang phân tích tệp âm thanh và trích xuất mốc thời gian..."

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

        JOBS[job_id]["progress"] = 10
        JOBS[job_id]["message"] = f"Thời lượng: {int(duration)}s. Đang bóc tách luồng âm thanh..."

        # 2. Split audio into 15-second WAV segments with exact CSV timestamping
        temp_dir = Path(tempfile.mkdtemp(prefix="stt_"))
        csv_list_path = temp_dir / "segments.csv"
        segment_pattern = str(temp_dir / "chunk_%04d.wav")

        proc = subprocess.run([
            "ffmpeg", "-y",
            "-i", str(file_path),
            "-f", "segment",
            "-segment_time", "15",
            "-segment_list", str(csv_list_path),
            "-segment_list_type", "csv",
            "-c:a", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            segment_pattern
        ], capture_output=True, timeout=300)

        # Immediately delete original heavy upload file (e.g. 450MB/2-3GB MP4) to free storage
        try:
            if file_path and Path(file_path).exists():
                Path(file_path).unlink(missing_ok=True)
        except Exception:
            pass

        # 3. Parse segments metadata with exact timestamps
        segments_meta = []
        if csv_list_path.exists():
            try:
                with open(csv_list_path, "r", encoding="utf-8") as f:
                    for idx, line in enumerate(f):
                        parts = line.strip().split(",")
                        if len(parts) >= 3:
                            fn, st, et = parts[0], float(parts[1]), float(parts[2])
                            chunk_p = temp_dir / fn
                            segments_meta.append({
                                "index": idx,
                                "file_path": str(chunk_p),
                                "start_time": st,
                                "end_time": et
                            })
            except Exception:
                pass

        if not segments_meta:
            chunk_files = sorted(list(temp_dir.glob("chunk_*.wav")))
            for idx, cp in enumerate(chunk_files):
                st = idx * 15.0
                et = min((idx + 1) * 15.0, duration) if duration > 0 else (idx + 1) * 15.0
                segments_meta.append({
                    "index": idx,
                    "file_path": str(cp),
                    "start_time": st,
                    "end_time": et
                })

        if not segments_meta:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["message"] = "Không thể đọc dữ liệu âm thanh từ tệp tải lên."
            return

        total_chunks = len(segments_meta)

        # 4. Check for existing checkpoint (Resume support)
        chk = load_checkpoint(job_id)
        if not chk or chk.get("total_segments") != total_chunks:
            chk = {
                "job_id": job_id,
                "language": language,
                "total_duration": duration,
                "total_segments": total_chunks,
                "status": "processing",
                "segments": {}
            }
            save_atomic_checkpoint(job_id, chk)

        # 5. Process segments sequentially with atomic checkpoint & per-segment early cleanup
        completed_count = sum(1 for s in chk.get("segments", {}).values() if s.get("status") == "completed")

        for meta in segments_meta:
            idx = meta["index"]
            idx_str = str(idx)
            chunk_p = Path(meta["file_path"])

            # Check if this segment is already completed in checkpoint
            if idx_str in chk.get("segments", {}) and chk["segments"][idx_str].get("status") == "completed":
                if chunk_p.exists():
                    try:
                        chunk_p.unlink(missing_ok=True)
                    except Exception:
                        pass
                continue

            # Transcribe segment with watchdog normalization
            transcript = ""
            if chunk_p.exists():
                try:
                    _, transcript = transcribe_audio_chunk(idx, chunk_p, language, max_retries=3)
                except Exception as ex:
                    print(f"Segment {idx+1} transcription error: {ex}")
                    transcript = ""
                finally:
                    # Immediately delete chunk WAV from disk to keep disk usage near zero
                    try:
                        chunk_p.unlink(missing_ok=True)
                    except Exception:
                        pass

            # Update Checkpoint atomically
            chk["segments"][idx_str] = {
                "index": idx,
                "start_time": meta["start_time"],
                "end_time": meta["end_time"],
                "status": "completed",
                "transcript": transcript,
                "completed_at": time.time()
            }
            save_atomic_checkpoint(job_id, chk)

            completed_count += 1
            prog = int(10 + (completed_count / total_chunks) * 85)
            JOBS[job_id]["progress"] = prog
            JOBS[job_id]["message"] = f"Đang nhận diện giọng nói AI: {completed_count}/{total_chunks} đoạn ({prog}%)..."

        # 6. Build final complete TXT and SRT subtitles from checkpoint data
        valid_texts = []
        srt_blocks = []
        srt_idx = 1

        for i in range(total_chunks):
            seg_data = chk.get("segments", {}).get(str(i), {})
            txt = (seg_data.get("transcript") or "").strip()
            if txt:
                valid_texts.append(txt)
                st = seg_data.get("start_time", i * 15.0)
                et = seg_data.get("end_time", (i + 1) * 15.0)
                srt_block = f"{srt_idx}\n{format_timestamp_srt(st)} --> {format_timestamp_srt(et)}\n{txt}\n"
                srt_blocks.append(srt_block)
                srt_idx += 1

        full_text = "\n\n".join(valid_texts) if valid_texts else "Không nhận diện được giọng nói trong tệp này."
        srt_content = "\n".join(srt_blocks) if srt_blocks else ""

        # 7. Final cleanup
        try:
            for p in temp_dir.glob("*"):
                p.unlink(missing_ok=True)
            temp_dir.rmdir()
            delete_checkpoint(job_id)
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
        }
    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["message"] = f"Lỗi xử lý âm thanh: {str(e)}"

@app.route("/api/upload_progress/<upload_id>", methods=["GET"])
def api_upload_progress(upload_id):
    """Return list of chunk indices that have already been uploaded for this upload_id to enable true resume."""
    try:
        part_files = list(UPLOAD_DIR.glob(f"{upload_id}_part_*.tmp"))
        uploaded_indices = []
        for p in part_files:
            try:
                idx_str = p.stem.split("_part_")[-1]
                uploaded_indices.append(int(idx_str))
            except Exception:
                pass
        uploaded_indices.sort()
        return jsonify({
            "status": "success",
            "upload_id": upload_id,
            "uploaded_chunks": uploaded_indices,
            "count": len(uploaded_indices)
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ── Multi-Part Pipeline Engine for Large Videos (1.5GB - 5.0GB) ──

def process_multipart_part_job(job_key, session_id, part_index, part_file_path, language="vi-VN"):
    """
    Process a single valid media Part (<= 500MB):
    1. Probe duration with ffprobe.
    2. Split into 15s WAV segments with exact CSV timestamps.
    3. Delete part_file_path immediately.
    4. Transcribe 15s segments with watchdog recovery.
    5. Delete WAV segments immediately.
    6. Save Part results to session checkpoint.
    7. If all parts completed, compile final merged TXT and continuous offset SRT.
    """
    session = load_checkpoint(f"session_{session_id}")
    if not session:
        session = {
            "session_id": session_id,
            "total_parts": 1,
            "language": language,
            "status": "in_progress",
            "parts": {}
        }

    job_key = f"{session_id}_p{part_index}"
    try:
        JOBS[job_key] = {
            "status": "processing",
            "progress": 10,
            "message": f"Phần {part_index+1}: Đang bóc tách âm thanh bằng FFmpeg...",
            "result": None
        }

        # 1. Probe duration
        duration = 0.0
        try:
            res = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(part_file_path)],
                capture_output=True, text=True, timeout=20
            )
            duration = float(res.stdout.strip())
        except Exception:
            duration = 30.0

        # 2. Split audio into 15s WAV segments with CSV timestamps
        temp_dir = Path(tempfile.mkdtemp(prefix=f"stt_p{part_index}_"))
        csv_list_path = temp_dir / "segments.csv"
        segment_pattern = str(temp_dir / "chunk_%04d.wav")

        subprocess.run([
            "ffmpeg", "-y", "-i", str(part_file_path),
            "-f", "segment", "-segment_time", "15",
            "-segment_list", str(csv_list_path), "-segment_list_type", "csv",
            "-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1",
            segment_pattern
        ], capture_output=True, timeout=300)

        # 3. Immediately delete the uploaded Part file (<= 500MB) from disk
        try:
            if Path(part_file_path).exists():
                Path(part_file_path).unlink(missing_ok=True)
        except Exception:
            pass

        # 4. Parse segments metadata
        segments_meta = []
        if csv_list_path.exists():
            try:
                with open(csv_list_path, "r", encoding="utf-8") as f:
                    for idx, line in enumerate(f):
                        parts = line.strip().split(",")
                        if len(parts) >= 3:
                            segments_meta.append({
                                "index": idx,
                                "file_path": str(temp_dir / parts[0]),
                                "start_time": float(parts[1]),
                                "end_time": float(parts[2])
                            })
            except Exception:
                pass

        if not segments_meta:
            for idx, cp in enumerate(sorted(list(temp_dir.glob("chunk_*.wav")))):
                segments_meta.append({
                    "index": idx,
                    "file_path": str(cp),
                    "start_time": idx * 15.0,
                    "end_time": min((idx + 1) * 15.0, duration)
                })

        total_chunks = len(segments_meta)
        part_segments = []

        # 5. Transcribe each 15s segment & delete WAV immediately
        for idx, meta in enumerate(segments_meta):
            chunk_p = Path(meta["file_path"])
            text = ""
            if chunk_p.exists():
                try:
                    _, text = transcribe_audio_chunk(idx, chunk_p, language, max_retries=3)
                except Exception:
                    text = ""
                finally:
                    try:
                        chunk_p.unlink(missing_ok=True)
                    except Exception:
                        pass

            if text and text.strip():
                part_segments.append({
                    "start_time": meta["start_time"],
                    "end_time": meta["end_time"],
                    "text": text.strip()
                })

            prog = int(10 + ((idx + 1) / max(total_chunks, 1)) * 85)
            JOBS[job_key]["progress"] = prog
            JOBS[job_key]["message"] = f"Phần {part_index+1}: Đã nhận diện {idx+1}/{total_chunks} đoạn ({prog}%)..."

        # Cleanup temp directory
        try:
            for p in temp_dir.glob("*"):
                p.unlink(missing_ok=True)
            temp_dir.rmdir()
        except Exception:
            pass

        # 6. Save Part to Session Checkpoint
        session = load_checkpoint(f"session_{session_id}") or session
        if "parts" not in session:
            session["parts"] = {}
        session["parts"][str(part_index)] = {
            "part_index": part_index,
            "duration": duration,
            "segments": part_segments,
            "status": "completed",
            "completed_at": time.time()
        }
        save_atomic_checkpoint(f"session_{session_id}", session)

        JOBS[job_key]["status"] = "completed"
        JOBS[job_key]["progress"] = 100
        JOBS[job_key]["message"] = f"Hoàn tất xử lý Phần {part_index+1} 100%!"

        # 7. Check if all parts completed -> Merge full video TXT and cumulative continuous SRT
        total_parts = session.get("total_parts", 1)
        if len(session["parts"]) == total_parts:
            all_texts = []
            srt_blocks = []
            srt_idx = 1
            cumulative_offset = 0.0

            for p_idx in range(total_parts):
                p_data = session["parts"].get(str(p_idx), {})
                p_dur = p_data.get("duration", 0.0)
                for seg in p_data.get("segments", []):
                    all_texts.append(seg["text"])
                    st = seg["start_time"] + cumulative_offset
                    et = seg["end_time"] + cumulative_offset
                    srt_block = f"{srt_idx}\n{format_timestamp_srt(st)} --> {format_timestamp_srt(et)}\n{seg['text']}\n"
                    srt_blocks.append(srt_block)
                    srt_idx += 1
                cumulative_offset += p_dur

            final_text = "\n\n".join(all_texts) if all_texts else "Không nhận diện được giọng nói trong video."
            final_srt = "\n".join(srt_blocks) if srt_blocks else ""

            session["status"] = "completed"
            session["final_result"] = {
                "text": final_text,
                "srt": final_srt,
                "total_duration": round(cumulative_offset, 1),
                "total_parts": total_parts,
                "word_count": len(final_text.split()),
                "char_count": len(final_text)
            }
            save_atomic_checkpoint(f"session_{session_id}", session)

            JOBS[session_id] = {
                "status": "completed",
                "progress": 100,
                "message": f"Đã hoàn thành toàn bộ {total_parts} phần video thành công 100%!",
                "result": session["final_result"]
            }
    except Exception as e:
        JOBS[job_key]["status"] = "error"
        JOBS[job_key]["message"] = f"Lỗi xử lý phần {part_index+1}: {str(e)}"

@app.route("/api/multipart/init_session", methods=["POST"])
def api_multipart_init_session():
    try:
        data = request.get_json(force=True) or {}
        session_id = data.get("session_id") or uuid.uuid4().hex[:12]
        filename = data.get("filename", "video.mp4")
        total_parts = int(data.get("total_parts", 1))
        language = data.get("language", "vi-VN")

        session_data = {
            "session_id": session_id,
            "filename": filename,
            "total_parts": total_parts,
            "language": language,
            "created_at": time.time(),
            "status": "in_progress",
            "parts": {}
        }
        save_atomic_checkpoint(f"session_{session_id}", session_data)
        JOBS[session_id] = {
            "status": "in_progress",
            "progress": 0,
            "message": f"Đang khởi tạo phiên xử lý Multi-Part ({total_parts} phần)...",
            "result": None
        }
        return jsonify({"status": "success", "session_id": session_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/multipart/session_status/<session_id>", methods=["GET"])
def api_multipart_session_status(session_id):
    session = load_checkpoint(f"session_{session_id}")
    if not session:
        return jsonify({"status": "error", "message": "Không tìm thấy phiên xử lý."}), 404
    
    total_parts = session.get("total_parts", 1)
    completed_parts = list(session.get("parts", {}).keys())
    
    global_job = JOBS.get(session_id, {})
    if session.get("status") == "completed":
        return jsonify({
            "status": "completed",
            "progress": 100,
            "message": f"Hoàn tất toàn bộ {total_parts} phần video!",
            "result": session.get("final_result"),
            "completed_parts": completed_parts,
            "total_parts": total_parts
        })
    
    overall_progress = int((len(completed_parts) / max(total_parts, 1)) * 100)
    return jsonify({
        "status": "in_progress",
        "progress": overall_progress,
        "message": f"Đang xử lý Multi-Part: {len(completed_parts)}/{total_parts} phần hoàn tất...",
        "completed_parts": completed_parts,
        "total_parts": total_parts
    })

@app.route("/api/multipart/upload_part_chunk", methods=["POST"])
def api_multipart_upload_part_chunk():
    try:
        session_id = request.form.get("session_id")
        part_index = int(request.form.get("part_index", 0))
        chunk_index = int(request.form.get("chunk_index", 0))
        total_chunks = int(request.form.get("total_chunks", 1))
        filename = request.form.get("filename", "part.mp4")
        language = request.form.get("language", "vi-VN")

        if "file" not in request.files or not session_id:
            return jsonify({"status": "error", "message": "Dữ liệu mảnh không hợp lệ."}), 400

        chunk_file = request.files["file"]
        part_chunk_path = UPLOAD_DIR / f"{session_id}_p{part_index:03d}_chk{chunk_index:05d}.tmp"
        chunk_file.save(str(part_chunk_path))

        # When last chunk of this part arrives, assemble this single part
        if chunk_index == total_chunks - 1:
            ext = Path(filename).suffix.lower() or ".mp4"
            assembled_part_path = UPLOAD_DIR / f"upload_{session_id}_p{part_index}{ext}"

            with open(assembled_part_path, "wb") as outfile:
                for idx in range(total_chunks):
                    pc_file = UPLOAD_DIR / f"{session_id}_p{part_index:03d}_chk{idx:05d}.tmp"
                    if pc_file.exists():
                        with open(pc_file, "rb") as infile:
                            outfile.write(infile.read())
                        try:
                            pc_file.unlink(missing_ok=True)
                        except Exception:
                            pass

            job_key = f"{session_id}_p{part_index}"
            JOBS[job_key] = {
                "status": "queued",
                "progress": 5,
                "message": f"Phần {part_index+1}: Đã nhận đủ các mảnh, đang xếp hàng bóc tách âm thanh...",
                "result": None
            }

            t = threading.Thread(target=run_with_queue, args=(job_key, process_multipart_part_job, session_id, part_index, assembled_part_path, language))
            t.daemon = True
            t.start()

            return jsonify({"status": "part_assembled", "part_index": part_index, "job_key": job_key})

        return jsonify({"status": "chunk_received", "part_index": part_index, "chunk_index": chunk_index})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/upload_chunk", methods=["POST"])
def api_upload_chunk():
    """Receive sequential 5MB file chunks, assemble, and trigger adaptive queue STT."""
    try:
        upload_id = request.form.get("upload_id")
        chunk_index = int(request.form.get("chunk_index", 0))
        total_chunks = int(request.form.get("total_chunks", 1))
        filename = request.form.get("filename", "upload.mp4")
        language = request.form.get("language", "vi-VN")

        if "file" not in request.files or not upload_id:
            return jsonify({"status": "error", "message": "Dữ liệu mảnh tệp không hợp lệ."}), 400

        chunk_file = request.files["file"]
        chunk_path = UPLOAD_DIR / f"{upload_id}_part_{chunk_index:05d}.tmp"
        chunk_file.save(str(chunk_path))

        # When last chunk arrives, assemble all parts in strict sequence
        if chunk_index == total_chunks - 1:
            ext = Path(filename).suffix.lower() or ".mp4"
            assembled_path = UPLOAD_DIR / f"upload_{upload_id}{ext}"

            # High-speed buffered stream assembly to prevent any timeout
            with open(assembled_path, "wb") as outfile:
                for idx in range(total_chunks):
                    part_file = UPLOAD_DIR / f"{upload_id}_part_{idx:05d}.tmp"
                    if part_file.exists():
                        with open(part_file, "rb") as infile:
                            while True:
                                chunk = infile.read(2 * 1024 * 1024) # 2MB buffer
                                if not chunk:
                                    break
                                outfile.write(chunk)
                        try:
                            part_file.unlink(missing_ok=True)
                        except Exception:
                            pass

            JOBS[upload_id] = {
                "status": "queued",
                "progress": 0,
                "message": "Đã ghép nối các mảnh tệp hoàn tất! Đang xếp hàng xử lý âm thanh AI...",
                "result": None
            }

            t = threading.Thread(target=run_stt_with_adaptive_queue, args=(upload_id, assembled_path, language))
            t.daemon = True
            t.start()

            return jsonify({"status": "completed", "job_id": upload_id})

        return jsonify({"status": "chunk_received", "chunk_index": chunk_index})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

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

    t = threading.Thread(target=run_stt_with_adaptive_queue, args=(job_id, saved_path, language))
    t.daemon = True
    t.start()

    return jsonify({"status": "success", "job_id": job_id})

@app.route("/api/transcribe_status/<job_id>", methods=["GET"])
def api_transcribe_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        # Fallback to persistent checkpoint if in-memory state was reset
        chk = load_checkpoint(job_id)
        if chk:
            total = chk.get("total_segments", 1)
            completed = sum(1 for s in chk.get("segments", {}).values() if s.get("status") == "completed")
            prog = int(10 + (completed / total) * 85)
            return jsonify({
                "status": "processing",
                "progress": prog,
                "message": f"Đang nhận diện giọng nói AI (tự động phục hồi): {completed}/{total} đoạn ({prog}%)...",
                "result": None
            })
        return jsonify({"status": "error", "message": "Không tìm thấy tiến trình chuyển đổi."}), 404
    return jsonify(job)


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # Clean up any leftover temporary files on startup
    cleanup_all_temp_files()
    port = int(os.environ.get("PORT", 7860 if os.environ.get("SPACE_ID") else 5000))
    print(f"SLEEP2K Production Server (Waitress Multi-Threaded) running on port {port}")
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=8, channel_timeout=180)
    except ImportError:
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
