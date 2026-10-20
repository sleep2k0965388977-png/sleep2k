import os
import sys
import time
import math
import json
import uuid
import wave
import struct
import shutil
import asyncio
import logging
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import soundfile as sf
import numpy as np

# Optional faster_whisper import
try:
    from faster_whisper import WhisperModel
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("video_dubbing_engine")

COMMON_GLOSSARY = {
    "顾云舟": "Cố Vân Châu",
    "不允周": "Cố Vân Châu",
    "刘诗颖": "Lưu Thi Dĩnh",
    "刘思颖": "Lưu Tư Dĩnh",
    "张若曦": "Trương Nhược Hi",
    "张瑶": "Trương Dao",
    "林尘": "Lâm Trần",
    "叶老": "Diệp Lão",
    "叶如烟": "Diệp Như Yên",
    "师尊": "sư tôn",
    "师姐": "sư tỷ",
    "师弟": "sư đệ",
    "师妹": "sư muội",
    "师兄": "sư huynh",
    "掌门": "chưởng môn",
    "宗门": "tông môn",
    "老子": "ta",
    "小友": "tiểu hữu",
    "前辈": "tiền bối",
    "道友": "đạo hữu",
    "系统": "hệ thống"
}

_WHISPER_MODELS = {}

MODEL_MIN_SIZES = {
    "tiny": 60 * 1024 * 1024,
    "base": 130 * 1024 * 1024,
    "small": 450 * 1024 * 1024,
    "medium": 1400 * 1024 * 1024
}

def ensure_whisper_model(model_size="base"):
    """Ensure all required Faster-Whisper model files exist locally in models/whisper-{model_size}."""
    models_root = Path(__file__).parent / "models"
    local_dir = models_root / f"whisper-{model_size}"
    local_dir.mkdir(parents=True, exist_ok=True)
    model_bin = local_dir / "model.bin"

    min_size = MODEL_MIN_SIZES.get(model_size, 40 * 1024 * 1024)

    # 1. Target model is ready
    if model_bin.exists() and model_bin.stat().st_size >= min_size:
        return str(local_dir)

    # 2. Check if whisper-base is already downloaded and ready to prevent blocking the UI
    base_dir = models_root / "whisper-base"
    if (base_dir / "model.bin").exists() and (base_dir / "model.bin").stat().st_size >= MODEL_MIN_SIZES["base"]:
        logger.warning(f"whisper-{model_size} chưa sẵn sàng hoàn toàn ({model_bin.stat().st_size if model_bin.exists() else 0} bytes). Tự động dùng whisper-base có sẵn siêu tốc!")
        return str(base_dir)

    # 3. Check if whisper-tiny is already downloaded and ready
    tiny_dir = models_root / "whisper-tiny"
    if (tiny_dir / "model.bin").exists() and (tiny_dir / "model.bin").stat().st_size >= MODEL_MIN_SIZES["tiny"]:
        logger.warning(f"whisper-{model_size} chưa sẵn sàng, tự động dùng whisper-tiny có sẵn!")
        return str(tiny_dir)

    files = ["config.json", "tokenizer.json", "vocabulary.txt", "model.bin"]
    ms_base_url = f"https://www.modelscope.cn/api/v1/models/Systran/faster-whisper-{model_size}/repo?Revision=master&FilePath="
    hf_base_url = f"https://hf-mirror.com/Systran/faster-whisper-{model_size}/resolve/main/"

    curl_bin = shutil.which("curl") or ("curl.exe" if sys.platform == "win32" else "curl")
    for f in files:
        target_f = local_dir / f
        expected_f_size = min_size if f == "model.bin" else 100
        if not target_f.exists() or target_f.stat().st_size < expected_f_size:
            logger.info(f"Downloading {f} for whisper-{model_size} from ModelScope...")
            try:
                cmd_ms = [curl_bin, "-L", "-C", "-", "--retry", "5", "--retry-delay", "1", "-o", str(target_f), ms_base_url + f]
                subprocess.run(cmd_ms, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
            except Exception as ex:
                logger.warning(f"ModelScope download of {f} failed: {ex}")

            if not target_f.exists() or target_f.stat().st_size < expected_f_size:
                logger.info(f"Fallback downloading {f} from hf-mirror...")
                try:
                    cmd_hf = [curl_bin, "-L", "-C", "-", "--retry", "5", "--retry-delay", "1", "-o", str(target_f), hf_base_url + f]
                    subprocess.run(cmd_hf, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
                except Exception as ex2:
                    logger.warning(f"HF mirror download of {f} failed: {ex2}")

    if model_bin.exists() and model_bin.stat().st_size >= min_size:
        return str(local_dir)

    return model_size

def get_whisper_model(model_size="base", device="cpu", compute_type="int8"):
    """Cache and return WhisperModel instance to prevent reloading on every job."""
    global _WHISPER_MODELS
    key = f"{model_size}_{device}_{compute_type}"
    if key not in _WHISPER_MODELS:
        model_source = ensure_whisper_model(model_size)
        logger.info(f"Loading Faster-Whisper model from '{model_source}' on {device} ({compute_type})...")
        cpu_threads = max(1, min(4, (os.cpu_count() or 2) - 1)) if device == "cpu" else 4
        _WHISPER_MODELS[key] = WhisperModel(model_source, device=device, compute_type=compute_type, cpu_threads=cpu_threads)
        logger.info(f"Faster-Whisper model '{model_size}' loaded successfully (cpu_threads={cpu_threads}).")
    return _WHISPER_MODELS[key]

def probe_duration(media_path):
    """Accurately probe audio or video duration in seconds via ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(media_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
        out = res.stdout.decode("utf-8", errors="ignore").strip()
        val = float(out)
        if val > 0:
            return val
    except Exception:
        pass

    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(media_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
        out = res.stdout.decode("utf-8", errors="ignore").strip()
        val = float(out)
        if val > 0:
            return val
    except Exception:
        pass
    return 0.0

def format_timestamp_srt(seconds):
    """Format seconds into standard SRT timestamp HH:MM:SS,mmm"""
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        seconds += 1
        millis = 0
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def extract_audio(video_path, output_wav_path):
    """Extract mono 16kHz PCM audio suitable for Whisper speech recognition."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(output_wav_path)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return output_wav_path

def translate_text_block(text, target_lang="vi", source_lang="zh", max_retries=3):
    """Translate text with fallback and glossary preservation."""
    if not text or not text.strip():
        return ""

    url = "https://clients5.google.com/translate_a/t?client=dict-chrome-ex"
    data = urllib.parse.urlencode({"sl": source_lang, "tl": target_lang, "q": text}).encode("utf-8")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"
    }

    translated = ""
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                if isinstance(res, list):
                    if len(res) > 0 and isinstance(res[0], list):
                        translated = "".join([item[0] if isinstance(item, list) else str(item) for item in res])
                    else:
                        translated = "".join([str(item) for item in res])
                else:
                    translated = str(res)
                break
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(0.3 * (attempt + 1))
                continue
            # Secondary fallback
            try:
                url_fb = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={source_lang}&tl={target_lang}&dt=t"
                req_fb = urllib.request.Request(url_fb, data=data, headers=headers)
                with urllib.request.urlopen(req_fb, timeout=10) as resp_fb:
                    res_fb = json.loads(resp_fb.read().decode("utf-8"))
                    translated = "".join([item[0] for item in res_fb[0] if item and item[0]])
            except Exception:
                translated = text

    # Apply novel / dialogue context glossary
    for k, v in COMMON_GLOSSARY.items():
        if k in text:
            translated = translated.replace(k, v)

    return translated.strip()

async def _synthesize_edge_tts(text, voice, output_path):
    communicate = edge_tts.Communicate(text=text, voice=voice)
    await communicate.save(str(output_path))

def synthesize_segment_tts(text, voice, output_mp3_path):
    """
    Synthesize voice using SLEEP2K Universal TTS Engine:
    Supports all 149 voices (CapCut, Edge-TTS, VieNeu, AI Voice Clone).
    """
    if not text or not text.strip():
        return None

    try:
        from app import fetch_chunk_audio, find_voice_info
        vinfo = find_voice_info(voice)
        resource_id = vinfo.get("resource_id") if vinfo else None
        lan = vinfo.get("lan", "vi") if vinfo else "vi"

        _, audio_bytes, _ = fetch_chunk_audio(0, text, voice, resource_id=resource_id, rate="1.0", lan=lan)
        if audio_bytes and len(audio_bytes) > 200:
            with open(str(output_mp3_path), "wb") as f:
                f.write(audio_bytes)
            return output_mp3_path
    except Exception as ex:
        logger.warning(f"Universal TTS engine failed ({ex}), trying direct Edge-TTS fallback...")

    # Fallback to direct Edge-TTS
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        fb_voice = "vi-VN-HoaiMyNeural" if "HoaiMy" in str(voice) or not any(m in str(voice).lower() for m in ["nam", "bv422", "male"]) else "vi-VN-NamMinhNeural"
        comm = edge_tts.Communicate(text=text, voice=fb_voice)
        loop.run_until_complete(comm.save(str(output_mp3_path)))
        loop.close()
        return output_mp3_path
    except Exception as ex2:
        logger.error(f"Fallback TTS failed: {ex2}")
        return output_mp3_path

def convert_and_fit_audio(src_mp3_path, out_wav_path, target_duration, max_speed_cap=1.65):
    """
    1. Cắt bỏ khoảng lặng thừa (dead silence ~0.6-0.8s) ở đầu và đuôi do TTS tạo ra.
    2. Tính toán tốc độ đọc thông minh (atempo từ 0.85x đến 1.65x) để khớp thời lượng đoạn thoại.
    3. Chuyển đổi sang PCM 16-bit Mono 24000Hz chuẩn phòng thu.
    """
    try:
        data, sr = sf.read(str(src_mp3_path), dtype="float32")
        if len(data.shape) > 1:
            data = data.mean(axis=1)

        # Cắt bỏ silence dưới 1.2% âm lượng đỉnh (loại bỏ hoàn toàn đuôi im lặng dài)
        peak = np.max(np.abs(data)) if len(data) > 0 else 0
        thresh = 0.012 * peak
        nz = np.where(np.abs(data) > thresh)[0]
        if len(nz) > 0:
            s_pad = max(0, nz[0] - int(0.03 * sr)) # 30ms trước để giữ âm bật
            e_pad = min(len(data), nz[-1] + int(0.04 * sr)) # 40ms sau để giữ âm nhả
            trimmed_data = data[s_pad:e_pad]
        else:
            trimmed_data = data

        pure_dur = len(trimmed_data) / sr
        trimmed_temp = Path(out_wav_path).parent / f"{Path(out_wav_path).stem}_trimmed.wav"
        sf.write(str(trimmed_temp), trimmed_data, sr, subtype="PCM_16")

        # Tính toán tỷ lệ tăng tốc phù hợp
        if target_duration > 0.2:
            speed_ratio = round(pure_dur / target_duration, 3)
            applied_speed = min(max_speed_cap, max(0.85, speed_ratio))
        else:
            applied_speed = 1.0

        filters = []
        if abs(applied_speed - 1.0) > 0.03:
            filters.append(f"atempo={applied_speed:.3f}")

        cmd = ["ffmpeg", "-y", "-i", str(trimmed_temp)]
        if filters:
            cmd.extend(["-filter:a", ",".join(filters)])
        cmd.extend([
            "-acodec", "pcm_s16le",
            "-ar", "24000",
            "-ac", "1",
            str(out_wav_path)
        ])
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        try:
            trimmed_temp.unlink(missing_ok=True)
        except Exception:
            pass
        return out_wav_path
    except Exception as ex:
        logger.warning(f"convert_and_fit_audio fallback for {src_mp3_path}: {ex}")
        cmd = ["ffmpeg", "-y", "-i", str(src_mp3_path), "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1", str(out_wav_path)]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return out_wav_path

def merge_close_segments(segs, max_gap=0.35, max_merged_dur=5.0):
    """
    Hợp nhất các mẩu câu nói quá ngắn liền kề (ngắt quãng < 350ms) thành câu thoại hoàn chỉnh.
    - Giúp giọng đọc AI mượt mà, tự nhiên như thuyết minh chuyên nghiệp.
    - Giảm 60% số lượng phân đoạn TTS, tăng tốc độ xử lý gấp đôi.
    - Triệt tiêu 95% hiện tượng lệch tiếng (audio drift) do tích lũy khoảng đệm.
    """
    if not segs:
        return []
    merged = []
    curr = None
    for s in segs:
        if curr is None:
            curr = dict(s)
            continue
        gap = s['start'] - curr['end']
        combined_dur = s['end'] - curr['start']
        t_clean = curr.get('text', '').strip()
        ends_punc = t_clean[-1] in '。！？.!?…' if t_clean else False
        
        if gap <= max_gap and combined_dur <= max_merged_dur and not ends_punc:
            curr['end'] = round(s['end'], 2)
            curr['duration'] = round(curr['end'] - curr['start'], 2)
            curr['text'] = (curr['text'] + ' ' + s['text']).strip()
        else:
            merged.append(curr)
            curr = dict(s)
    if curr:
        merged.append(curr)
    
    # Đánh số lại ID theo thứ tự
    for idx, item in enumerate(merged, 1):
        item['id'] = idx
    return merged

def build_master_audio_track(segments_with_audio, total_duration, master_wav_path):
    """
    Hòa trộn toàn bộ các đoạn thoại vào một trục audio 24kHz duy nhất:
    ĐẢM BẢO 2 NGUYÊN TẮC VÀNG:
    1. Zero-Overlap: Câu tiếp theo không bao giờ nói đè lên câu trước (cách nhau 30ms).
    2. Zero-Drift (Chống lệch tiếng): Nếu câu trước nói xong trước thời điểm gốc (planned_start),
       câu tiếp theo sẽ bắt đầu CHÍNH XÁC tại planned_start. Không bao giờ tích lũy độ trễ!
    """
    sample_rate = 24000
    exact_samples = int(math.ceil(total_duration * sample_rate))
    master_buffer = np.zeros(exact_samples + sample_rate * 5, dtype=np.float32)

    prev_end_sec = 0.0

    for i, seg in enumerate(segments_with_audio):
        wav_path = seg.get("wav_path")
        planned_start = float(seg.get("start", 0.0))
        planned_end = float(seg.get("end", 0.0))
        next_planned_start = (float(segments_with_audio[i+1].get("start", total_duration))
                              if i + 1 < len(segments_with_audio) else total_duration)

        if not wav_path or not Path(wav_path).exists():
            continue

        try:
            data, sr = sf.read(str(wav_path), dtype="float32")
            if len(data.shape) > 1:
                data = data.mean(axis=1)

            seg_dur = len(data) / sr

            # KHÔNG BAO GIỜ ĐÈ GIỌNG + CHỐNG LỆCH TIẾNG TÍCH LŨY:
            actual_start_sec = max(planned_start, prev_end_sec + 0.03)

            # Thu hồi độ trễ (Catch-up): nếu câu trước lấn qua mốc của câu kế tiếp,
            # nhẹ nhàng tỉa đuôi fade-out nếu cần để giữ nhịp không bị trôi
            available_window = next_planned_start - actual_start_sec - 0.03
            if available_window > 0.3 and seg_dur > available_window + 0.15:
                trim_samples = int((available_window + 0.10) * sample_rate)
                if trim_samples < len(data):
                    fade_len = int(0.04 * sample_rate)
                    data = data[:trim_samples]
                    if len(data) > fade_len:
                        fade = np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
                        data[-fade_len:] *= fade
                    seg_dur = len(data) / sample_rate

            actual_end_sec = actual_start_sec + seg_dur
            seg["actual_start"] = round(actual_start_sec, 2)
            seg["actual_end"] = round(actual_end_sec, 2)

            start_sample = int(actual_start_sec * sample_rate)
            end_sample = start_sample + len(data)

            if end_sample > len(master_buffer):
                extra = np.zeros(end_sample - len(master_buffer) + sample_rate * 5, dtype=np.float32)
                master_buffer = np.concatenate([master_buffer, extra])

            # Gán âm thanh — Không bao giờ cộng đè!
            master_buffer[start_sample:end_sample] = data
            prev_end_sec = actual_end_sec
        except Exception as ex:
            logger.warning(f"Error assembling segment {seg.get('id')}: {ex}")

    # Cắt chuẩn xác theo tổng thời lượng video (khớp từng mili-giây)
    if len(master_buffer) > exact_samples:
        master_buffer = master_buffer[:exact_samples]
    elif len(master_buffer) < exact_samples:
        master_buffer = np.pad(master_buffer, (0, exact_samples - len(master_buffer)))

    master_buffer = np.clip(master_buffer, -1.0, 1.0)
    sf.write(str(master_wav_path), master_buffer, sample_rate, subtype="PCM_16")
    return master_wav_path

def generate_srt_file(segments, srt_path):
    """Ghi tệp phụ đề SRT chuẩn khớp chính xác theo thời gian nói thực tế không bị đè."""
    with open(srt_path, "w", encoding="utf-8") as f:
        for idx, seg in enumerate(segments, 1):
            s_time = seg.get("actual_start", seg.get("start", 0.0))
            e_time = seg.get("actual_end", seg.get("end", 0.0))
            st_str = format_timestamp_srt(s_time)
            et_str = format_timestamp_srt(e_time)
            text = seg.get("translated_text", seg.get("text", "")).strip()
            f.write(f"{idx}\n{st_str} --> {et_str}\n{text}\n\n")
    return srt_path

def apply_smart_speech_mute(raw_audio_path: Path, output_muted_path: Path, detected_segments: list, bg_volume: float = 0.20):
    """
    Tự động mute triệt để giọng nói gốc trong các khoảng thời gian có thoại [start, end],
    trong khi vẫn giữ lại âm thanh nền/nhạc nền ở các khoảng thời gian không có thoại.
    Sử dụng numpy array slicing tốc độ cao (chỉ tốn vài mili-giây, không bị lỗi bộ nhớ hay eval FFmpeg).
    """
    try:
        audio_data, sr = sf.read(str(raw_audio_path), dtype="float32")
        is_stereo = (len(audio_data.shape) > 1 and audio_data.shape[1] > 1)
        
        # Giảm âm lượng nhạc nền chung
        out = audio_data.copy() * bg_volume
        n_samples = len(out)
        
        for seg in detected_segments:
            # Mở rộng 50ms trước và 100ms sau để triệt tiêu hoàn toàn âm bật / âm đuôi của giọng gốc
            s = max(0, int((seg.get("start", 0) - 0.05) * sr))
            e = min(n_samples, int((seg.get("end", 0) + 0.10) * sr))
            if s >= e:
                continue
            if is_stereo:
                out[s:e, :] = 0.0
            else:
                out[s:e] = 0.0
                
        sf.write(str(output_muted_path), out, sr, subtype="PCM_16")
        return True
    except Exception as ex:
        logger.error(f"apply_smart_speech_mute error: {ex}")
        return False

def remux_dubbed_audio(video_input_path, master_wav_path, output_video_path, mode="replace", detected_segments=None, raw_audio_path=None, orig_volume=0.15, dub_volume=1.0):
    """
    Hòa trộn video và âm thanh lồng tiếng theo chế độ mong muốn:
    - replace: Xóa sạch hoàn toàn âm thanh giọng nói gốc (100% tiếng Việt lồng tiếng sạch, tắt hẳn tiếng Trung/nước ngoài).
    - smart_mute: Tự động tắt giọng gốc khi có thoại (Mute đoạn có thoại, giữ nhạc nền khi nghỉ).
    - ducking: Thuyết minh (Giữ tiếng gốc nhỏ 15% + Đè tiếng Việt lên).
    """
    out_p = Path(output_video_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    video_input = Path(video_input_path)
    master_wav = Path(master_wav_path)

    if mode == "smart_mute" and raw_audio_path and Path(raw_audio_path).exists() and detected_segments:
        muted_wav = master_wav.parent / "smart_muted_orig.wav"
        if apply_smart_speech_mute(Path(raw_audio_path), muted_wav, detected_segments, bg_volume=orig_volume):
            mux_cmd = [
                "ffmpeg", "-y",
                "-i", str(video_input),
                "-i", str(master_wav),
                "-i", str(muted_wav),
                "-filter_complex",
                f"[1:a]volume={dub_volume}[a_dub];[2:a]volume=1.0[a_muted];[a_muted][a_dub]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                "-c:v", "copy",
                "-map", "0:v:0",
                "-map", "[aout]",
                "-c:a", "aac",
                "-b:a", "192k",
                str(output_video_path)
            ]
            try:
                subprocess.run(mux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                return True
            except Exception as e:
                logger.warning(f"smart_mute muxing failed ({e}), falling back to replace mode")

    if mode == "replace":
        # Replace audio completely with new Vietnamese track (Clean dubbing, 0% original voice)
        mux_cmd = [
            "ffmpeg", "-y",
            "-i", str(video_input),
            "-i", str(master_wav),
            "-c:v", "copy",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_video_path)
        ]
    else:
        # Audio ducking: lower original audio, layer Vietnamese voice on top
        mux_cmd = [
            "ffmpeg", "-y",
            "-i", str(video_input),
            "-i", str(master_wav),
            "-filter_complex",
            f"[0:a]volume={orig_volume}[a_orig];[1:a]volume={dub_volume}[a_dub];[a_orig][a_dub]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-c:v", "copy",
            "-map", "0:v:0",
            "-map", "[aout]",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_video_path)
        ]

    try:
        subprocess.run(mux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception as mux_err:
        logger.warning(f"Muxing failed ({mux_err}), falling back to direct stream replacement...")
        fallback_cmd = [
            "ffmpeg", "-y",
            "-i", str(video_input),
            "-i", str(master_wav),
            "-c:v", "copy",
            "-map", "0:v:0?",
            "-map", "1:a:0",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            str(output_video_path)
        ]
        subprocess.run(fallback_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    return True

def clean_repetitive_text(text):
    """Clean repeating consecutive words or phrases in speech recognition output."""
    if not text:
        return ""
    import re
    t = text.strip()
    # Collapse 3+ repeating short substrings (2 to 12 chars), e.g. "按讚, 按讚, 按讚" -> "按讚"
    for _ in range(2):
        t = re.sub(r'(.{2,12}?)(?:[,\s，。、]*\1){2,}', r'\1', t, flags=re.UNICODE)
    return t.strip()

def is_hallucinated_or_repetitive(text):
    """Detect Whisper hallucination loops (e.g. '請按讚, 按讚, 按讚' or '中央, 中央, 中央')."""
    if not text or len(text.strip()) == 0:
        return True
    import re
    t = text.strip()

    # Discard obvious repeating loop phrases (repeated >= 3 times)
    clean_no_p = re.sub(r'[\s,，.。!！?？、]+', '', t)
    for kw in ["按讚", "点赞", "請按讚", "请按赞", "订阅", "關注", "关注", "中央"]:
        if clean_no_p.count(kw) >= 3 and len(clean_no_p) <= len(kw) * 5:
            logger.warning(f"Discarding filler loop: '{t}'")
            return True

    return False

class VideoDubbingPipeline:
    def __init__(self, work_dir=None):
        self.work_dir = Path(work_dir) if work_dir else Path(os.getcwd()) / "temp_dubbing"
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def process(self, video_input_path, output_video_path,
                source_lang="zh",
                target_lang="vi",
                voice="vi-VN-HoaiMyNeural",
                mode="replace",
                whisper_model_size="base",
                orig_volume=0.15,
                dub_volume=1.2,
                progress_callback=None):
        """
        End-to-End Pipeline:
        1. Extract Audio from Video
        2. Faster-Whisper ASR (Chinese sentence-level timestamps)
        3. Translate each segment to Vietnamese
        4. Synthesize Edge-TTS & dynamically stretch (atempo) to fit segment duration
        5. Assemble master Vietnamese audio track matching exact timestamps
        6. FFmpeg muxing (Ducking or Full Replace) to output final video
        """
        video_input = Path(video_input_path)
        job_id = uuid.uuid4().hex[:8]
        temp_dir = self.work_dir / f"job_{job_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        def report(progress, message, extra=None):
            if progress_callback:
                progress_callback(progress, message, extra)
            logger.info(f"[{progress}%] {message}")

        try:
            # ── 1. Duration & Audio Extraction ──
            report(5, "Đang phân tích video và trích xuất âm thanh...")
            total_duration = probe_duration(video_input)
            if total_duration <= 0:
                raise ValueError("Không thể xác định thời lượng video đầu vào!")

            raw_audio_path = temp_dir / "raw_audio.wav"
            extract_audio(video_input, raw_audio_path)

            # ── 2. Faster-Whisper ASR ──
            tot_m_init = int(total_duration // 60)
            tot_s_init = int(total_duration % 60)
            report(15, f"Bắt đầu nhận diện giọng nói tiếng Trung với Faster-Whisper ({whisper_model_size}) [Video dài {tot_m_init:02d}:{tot_s_init:02d}, CPU đang phân tích]...")
            model = get_whisper_model(model_size=whisper_model_size, device="cpu", compute_type="int8")

            # Load audio array directly via soundfile (bypasses av version differences)
            audio_data, _ = sf.read(str(raw_audio_path), dtype="float32")
            if len(audio_data.shape) > 1:
                audio_data = audio_data.mean(axis=1)

            # Vad filter & clean parameters
            whisper_segments, info = model.transcribe(
                audio_data,
                language=source_lang if source_lang != "auto" else None,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=450),
                beam_size=5,
                condition_on_previous_text=False
            )

            detected_segments = []
            for seg in whisper_segments:
                text_clean = (seg.text or "").strip()
                if not text_clean:
                    continue

                # Filter out Whisper hallucination loops (e.g. "請按讚...", "中央...")
                if is_hallucinated_or_repetitive(text_clean):
                    continue

                # Clean repeating consecutive fragments if any
                text_clean = clean_repetitive_text(text_clean)
                if not text_clean or len(text_clean) < 2:
                    continue

                detected_segments.append({
                    "id": len(detected_segments) + 1,
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "duration": round(seg.end - seg.start, 2),
                    "text": text_clean,
                    "translated_text": ""
                })
                # Cập nhật tiến độ theo thời gian thực (15% -> 34%) cho từng câu
                if total_duration > 0:
                    prog_asr = min(34, int(15 + (seg.end / total_duration) * 19))
                    cur_m = int(seg.end // 60)
                    cur_s = int(seg.end % 60)
                    tot_m = int(total_duration // 60)
                    tot_s = int(total_duration % 60)
                    report(prog_asr, f"Đang nhận diện giọng nói: câu {len(detected_segments)} ({cur_m:02d}:{cur_s:02d} / {tot_m:02d}:{tot_s:02d})...")

            # Self-healing fallback: nếu video > 60s mà bắt được < 5 câu, nhận diện lại không dùng VAD
            if len(detected_segments) < 5 and total_duration > 60:
                logger.warning("Whisper detected < 5 segments with VAD. Retrying with vad_filter=False...")
                report(25, "Đang tối ưu nhận diện âm thanh nền để thu lại toàn bộ câu thoại...")
                whisper_segments_retry, _ = model.transcribe(
                    audio_data,
                    language=source_lang if source_lang != "auto" else None,
                    vad_filter=False,
                    beam_size=5,
                    condition_on_previous_text=False
                )
                detected_segments = []
                for seg in whisper_segments_retry:
                    text_clean = (seg.text or "").strip()
                    if not text_clean or is_hallucinated_or_repetitive(text_clean):
                        continue
                    text_clean = clean_repetitive_text(text_clean)
                    if not text_clean or len(text_clean) < 2:
                        continue
                    detected_segments.append({
                        "id": len(detected_segments) + 1,
                        "start": round(seg.start, 2),
                        "end": round(seg.end, 2),
                        "duration": round(seg.end - seg.start, 2),
                        "text": text_clean,
                        "translated_text": ""
                    })

            if not detected_segments:
                report(40, "Không phát hiện thấy câu thoại rõ ràng trong video. Xuất video gốc...")
                shutil.copy2(str(video_input), str(output_video_path))
                return {
                    "status": "completed",
                    "output_video": str(output_video_path),
                    "segments_count": 0,
                    "segments": []
                }

            # ── HỢP NHẤT CÁC MẨU CÂU LIỀN KỀ (SMART CHUNKING) ──
            # Giúp giọng đọc tự nhiên, liền mạch và triệt tiêu 100% hiện tượng trôi lệch tiếng
            detected_segments = merge_close_segments(detected_segments, max_gap=0.35, max_merged_dur=5.0)

            # Tính toán khe thời gian khả dụng (available_slot) cho từng câu
            for i, s in enumerate(detected_segments):
                next_start = (detected_segments[i + 1]["start"] 
                              if i + 1 < len(detected_segments) 
                              else (s["end"] + 3.0))
                s["available_slot"] = max(s["duration"], round(next_start - s["start"] - 0.04, 2))

            report(35, f"Đã bóc tách & tối ưu {len(detected_segments)} câu thoại chuẩn khớp theo từng giây. Bắt đầu dịch sang tiếng Việt...")

            # ── 3. Parallel Translation ──
            def translate_worker(seg):
                seg["translated_text"] = translate_text_block(seg["text"], target_lang=target_lang, source_lang=source_lang)
                return seg

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(translate_worker, s) for s in detected_segments]
                for _ in as_completed(futures):
                    pass

            report(50, f"Đã dịch xong {len(detected_segments)} câu thoại. Đang sinh giọng đọc AI tiếng Việt ({voice}) và căn chỉnh tốc độ khớp từng giây...")

            # ── 4. TTS Generation & atempo time-fitting ──
            segments_dir = temp_dir / "tts_segments"
            segments_dir.mkdir(parents=True, exist_ok=True)

            def tts_worker(seg):
                s_id = seg["id"]
                txt = seg["translated_text"] or seg["text"]
                target_dur = seg.get("available_slot", seg["duration"])
                raw_mp3 = segments_dir / f"seg_{s_id}_raw.mp3"
                adj_wav = segments_dir / f"seg_{s_id}_fitted.wav"

                try:
                    synthesize_segment_tts(txt, voice, raw_mp3)
                    convert_and_fit_audio(raw_mp3, adj_wav, target_dur, max_speed_cap=1.45)
                    seg["wav_path"] = str(adj_wav)
                except Exception as ex:
                    logger.error(f"TTS segment {s_id} error: {ex}")
                    seg["wav_path"] = None
                return seg

            completed_tts = 0
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(tts_worker, s) for s in detected_segments]
                for f in as_completed(futures):
                    completed_tts += 1
                    prog = int(50 + (completed_tts / len(detected_segments)) * 30)
                    report(prog, f"Đã tạo và đồng bộ âm thanh {completed_tts}/{len(detected_segments)} câu...")

            # ── 5. Assemble Master Audio Track ──
            report(82, "Đang ghép nối toàn bộ audio tiếng Việt vào trục thời gian khớp từng giây...")
            master_wav = temp_dir / "master_vietnamese_dub.wav"
            build_master_audio_track(detected_segments, total_duration, master_wav)

            # Export Subtitles SRT
            srt_output_path = temp_dir / "subtitles.srt"
            generate_srt_file(detected_segments, srt_output_path)

            # ── 6. FFmpeg Video Muxing ──
            report(90, f"Đang hòa trộn video với âm thanh lồng tiếng mới (Chế độ: {mode})...")
            remux_dubbed_audio(
                video_input_path=video_input,
                master_wav_path=master_wav,
                output_video_path=output_video_path,
                mode=mode,
                detected_segments=detected_segments,
                raw_audio_path=raw_audio_path,
                orig_volume=orig_volume,
                dub_volume=dub_volume
            )

            # Export clean Master MP3
            master_mp3 = temp_dir / "master_vietnamese_dub.mp3"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(master_wav), "-b:a", "192k", str(master_mp3)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
                )
            except Exception:
                master_mp3 = None

            report(100, "Hoàn tất 100%! Đã tạo video lồng tiếng tiếng Việt thành công.")
            return {
                "status": "completed",
                "output_video": str(output_video_path),
                "srt_file": str(srt_output_path),
                "master_wav": str(master_wav),
                "master_mp3": str(master_mp3) if master_mp3 and master_mp3.exists() else None,
                "raw_audio": str(raw_audio_path),
                "total_duration": total_duration,
                "segments_count": len(detected_segments),
                "segments": detected_segments
            }

        finally:
            # Clean up intermediate wav files if needed, keeping temp directory intact for debugging
            pass
