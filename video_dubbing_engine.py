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

    for f in files:
        target_f = local_dir / f
        expected_f_size = min_size if f == "model.bin" else 100
        if not target_f.exists() or target_f.stat().st_size < expected_f_size:
            logger.info(f"Downloading {f} for whisper-{model_size} from ModelScope...")
            try:
                cmd_ms = ["curl.exe", "-L", "-C", "-", "--retry", "5", "--retry-delay", "1", "-o", str(target_f), ms_base_url + f]
                subprocess.run(cmd_ms, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
            except Exception as ex:
                logger.warning(f"ModelScope download of {f} failed: {ex}")

            if not target_f.exists() or target_f.stat().st_size < expected_f_size:
                logger.info(f"Fallback downloading {f} from hf-mirror...")
                try:
                    cmd_hf = ["curl.exe", "-L", "-C", "-", "--retry", "5", "--retry-delay", "1", "-o", str(target_f), hf_base_url + f]
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
        _WHISPER_MODELS[key] = WhisperModel(model_source, device=device, compute_type=compute_type)
        logger.info(f"Faster-Whisper model '{model_size}' loaded successfully.")
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
    """Synthesize voice using Edge-TTS with automatic CapCut fallback."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        comm = edge_tts.Communicate(text=text, voice=voice)
        loop.run_until_complete(comm.save(str(output_mp3_path)))
        loop.close()
        if Path(output_mp3_path).exists() and Path(output_mp3_path).stat().st_size > 100:
            return output_mp3_path
    except Exception as ex:
        logger.warning(f"Edge-TTS failed ({ex}), trying CapCut fallback...")
        try:
            from capcut_tts_api import CapCutClient
            client = CapCutClient()
            cap_v = "BV421_vivn_streaming" if "HoaiMy" in voice else "BV422_vivn_streaming"
            c_res = client.create_tts_task(texts=text, voice=cap_v)
            if c_res and "task_id" in c_res:
                t_id = c_res["task_id"]
                t_tok = c_res.get("token", "")
                q = client.query_tts_task(t_id, t_tok)
                if q and "audio" in q:
                    import base64
                    audio_b = base64.b64decode(q["audio"])
                    with open(output_mp3_path, "wb") as f:
                        f.write(audio_b)
                    return output_mp3_path
        except Exception as cap_ex:
            logger.error(f"CapCut fallback also failed: {cap_ex}")
    return output_mp3_path

def convert_and_fit_audio(src_mp3_path, out_wav_path, target_duration, max_speed_cap=1.35):
    """
    Convert synthesized audio to 24000Hz 16-bit mono WAV.
    If speech is longer than target_duration, apply FFmpeg atempo filter to speed up and fit the exact slot.
    """
    actual_dur = probe_duration(src_mp3_path)
    if actual_dur <= 0:
        actual_dur = target_duration

    filters = []
    # If generated audio is longer than target duration, speed it up
    if actual_dur > target_duration + 0.08 and target_duration > 0.3:
        speed_ratio = round(actual_dur / target_duration, 3)
        # Cap speed ratio so voice does not sound unnaturally rushed
        applied_speed = min(speed_ratio, max_speed_cap)
        # atempo filter supports 0.5 to 2.0
        if 0.5 <= applied_speed <= 2.0:
            filters.append(f"atempo={applied_speed}")

    cmd = ["ffmpeg", "-y", "-i", str(src_mp3_path)]
    if filters:
        cmd.extend(["-filter:a", ",".join(filters)])
    cmd.extend([
        "-acodec", "pcm_s16le",
        "-ar", "24000",
        "-ac", "1",
        str(out_wav_path)
    ])
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return out_wav_path

def build_master_audio_track(segments_with_audio, total_duration, master_wav_path):
    """
    Assemble all dubbed audio pieces into a single continuous 24kHz master audio track,
    placing each segment at its precise start timestamp (khớp từng giây).
    """
    sample_rate = 24000
    total_samples = int(math.ceil(total_duration * sample_rate)) + sample_rate
    master_buffer = [0] * total_samples

    for seg in segments_with_audio:
        wav_path = seg.get("wav_path")
        start_sec = seg.get("start", 0.0)
        if not wav_path or not Path(wav_path).exists():
            continue

        try:
            with wave.open(str(wav_path), "rb") as wf:
                n_channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                framerate = wf.getframerate()
                n_frames = wf.getnframes()
                raw_bytes = wf.readframes(n_frames)

            # Ensure 16-bit mono 24kHz
            if sampwidth == 2 and n_channels == 1:
                fmt = f"<{n_frames}h"
                samples = struct.unpack(fmt, raw_bytes)
                start_sample = int(start_sec * sample_rate)

                for idx, s in enumerate(samples):
                    target_idx = start_sample + idx
                    if target_idx < total_samples:
                        # Mix and clip 16-bit signed integer [-32768, 32767]
                        val = master_buffer[target_idx] + s
                        master_buffer[target_idx] = max(-32768, min(32767, val))
        except Exception as ex:
            logger.warning(f"Error mixing segment {seg.get('id')}: {ex}")

    # Write final master WAV file
    with wave.open(str(master_wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # Pack back into 16-bit PCM bytes
        chunk_size = 48000
        for i in range(0, total_samples, chunk_size):
            chunk = master_buffer[i:i + chunk_size]
            wf.writeframes(struct.pack(f"<{len(chunk)}h", *chunk))

    return master_wav_path

def generate_srt_file(segments, srt_path):
    """Write standard SRT subtitle file with translated Vietnamese text and exact timestamps."""
    with open(srt_path, "w", encoding="utf-8") as f:
        for idx, seg in enumerate(segments, 1):
            st_str = format_timestamp_srt(seg["start"])
            et_str = format_timestamp_srt(seg["end"])
            text = seg.get("translated_text", seg.get("text", "")).strip()
            f.write(f"{idx}\n{st_str} --> {et_str}\n{text}\n\n")
    return srt_path

class VideoDubbingPipeline:
    def __init__(self, work_dir=None):
        self.work_dir = Path(work_dir) if work_dir else Path(os.getcwd()) / "temp_dubbing"
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def process(self, video_input_path, output_video_path,
                source_lang="zh",
                target_lang="vi",
                voice="vi-VN-HoaiMyNeural",
                mode="ducking",
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
            report(15, f"Bắt đầu nhận diện giọng nói tiếng Trung với Faster-Whisper ({whisper_model_size})...")
            model = get_whisper_model(model_size=whisper_model_size, device="cpu", compute_type="int8")

            # Load audio array directly via soundfile (bypasses av version differences)
            audio_data, _ = sf.read(str(raw_audio_path), dtype="float32")
            if len(audio_data.shape) > 1:
                audio_data = audio_data.mean(axis=1)

            # Vad filter removes silences so sentences are segmented cleanly
            whisper_segments, info = model.transcribe(
                audio_data,
                language=source_lang if source_lang != "auto" else None,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=450),
                beam_size=5
            )

            detected_segments = []
            for idx, seg in enumerate(whisper_segments):
                text_clean = (seg.text or "").strip()
                if not text_clean:
                    continue
                detected_segments.append({
                    "id": idx + 1,
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "duration": round(seg.end - seg.start, 2),
                    "text": text_clean,
                    "translated_text": ""
                })
                # Cập nhật tiến độ theo thời gian thực (15% -> 34%) tránh người dùng tưởng bị treo
                if total_duration > 0 and len(detected_segments) % 4 == 0:
                    prog_asr = min(34, int(15 + (seg.end / total_duration) * 19))
                    cur_m = int(seg.end // 60)
                    cur_s = int(seg.end % 60)
                    tot_m = int(total_duration // 60)
                    tot_s = int(total_duration % 60)
                    report(prog_asr, f"Đang nhận diện giọng nói tiếng Trung: {len(detected_segments)} câu ({cur_m:02d}:{cur_s:02d} / {tot_m:02d}:{tot_s:02d})...")

            if not detected_segments:
                report(40, "Không phát hiện thấy câu thoại rõ ràng trong video. Xuất video gốc...")
                shutil.copy2(str(video_input), str(output_video_path))
                return {
                    "status": "completed",
                    "output_video": str(output_video_path),
                    "segments_count": 0,
                    "segments": []
                }

            report(35, f"Đã bóc tách {len(detected_segments)} câu thoại tiếng Trung theo từng giây. Bắt đầu dịch sang tiếng Việt...")

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
                target_dur = seg["duration"]
                raw_mp3 = segments_dir / f"seg_{s_id}_raw.mp3"
                adj_wav = segments_dir / f"seg_{s_id}_fitted.wav"

                try:
                    synthesize_segment_tts(txt, voice, raw_mp3)
                    convert_and_fit_audio(raw_mp3, adj_wav, target_dur, max_speed_cap=1.35)
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
            out_p = Path(output_video_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)

            if mode == "replace":
                # Replace audio completely with new Vietnamese track
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
                # Audio ducking: lower original Chinese audio, layer Vietnamese voice on top
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
                logger.warning(f"Complex muxing failed ({mux_err}), falling back to direct stream replacement...")
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

            report(100, "Hoàn tất 100%! Đã tạo video lồng tiếng tiếng Việt thành công.")
            return {
                "status": "completed",
                "output_video": str(output_video_path),
                "srt_file": str(srt_output_path),
                "total_duration": total_duration,
                "segments_count": len(detected_segments),
                "segments": detected_segments
            }

        finally:
            # Clean up intermediate wav files if needed, keeping temp directory intact for debugging
            pass
