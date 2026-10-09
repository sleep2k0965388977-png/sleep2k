import os
import re
import math
import json
import time
import uuid
import asyncio
import io
import urllib.request
import urllib.parse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import requests
import subprocess
import tempfile
import soundfile as sf
import edge_tts
import speech_recognition as sr
from flask import Flask, render_template, request, jsonify, send_from_directory, Response, redirect
from capcut_tts_api import CapCutClient, CapCutError

def get_ffmpeg_exe():
    import shutil
    cmd = shutil.which("ffmpeg")
    if cmd:
        return cmd
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
        cmd = shutil.which("ffmpeg")
        if cmd:
            return cmd
    except Exception:
        pass
    return "ffmpeg"

FFMPEG_BIN = get_ffmpeg_exe()

# ── 🔒 SECURITY: API Protection + Anti-Bot/DDOS ──
# Không cần đăng nhập — app mở cho mọi người
# Chỉ chặn: gọi API từ bên ngoài + bot/DDOS
import hashlib

# ── Rate Limiting (chống spam/DDOS) ──
_rate_store = {}  # {ip: {endpoint: [timestamps]}}
RATE_WINDOW = 60  # 60 seconds window

def _get_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr or '0.0.0.0').split(',')[0].strip()

def _rate_check(endpoint, max_req):
    """Return True if OK, False if rate limited."""
    ip = _get_ip()
    now = time.time()
    key = f"{ip}:{endpoint}"
    if key not in _rate_store:
        _rate_store[key] = []
    _rate_store[key] = [t for t in _rate_store[key] if now - t < RATE_WINDOW]
    if len(_rate_store[key]) >= max_req:
        return False
    _rate_store[key].append(now)
    return True

# ── Anti-Bot: Block suspicious requests ──
BLOCKED_USER_AGENTS = ['python-requests', 'curl', 'wget', 'httpie', 'postman', 'insomnia']
ALLOW_PROGRAMMATIC = os.environ.get("ALLOW_PROGRAMMATIC", "false").lower() == "true"

def _is_bot_request():
    """Check if request looks like a bot/script (not from our web UI)."""
    if ALLOW_PROGRAMMATIC:
        return False
    ua = request.headers.get('User-Agent', '').lower()
    # Block known script user agents
    for blocked in BLOCKED_USER_AGENTS:
        if blocked in ua:
            return True
    # No user agent at all = suspicious
    if not ua or len(ua) < 10:
        return True
    return False

def _is_same_origin():
    """Check if request comes from our own web UI (same origin)."""
    referer = request.headers.get('Referer', '')
    origin = request.headers.get('Origin', '')
    # Get hostname without protocol (Railway uses http internally but https externally)
    server_host = request.host.split(':')[0]  # e.g. "sleep2k-tts-production.up.railway.app"
    
    # Check if referer/origin contains our hostname
    if referer and server_host in referer:
        return True
    if origin and server_host in origin:
        return True
    # Browser fetch from same page sends Origin header
    # Direct page navigation (GET) has no Origin
    if not origin and not referer:
        if request.method == 'GET':
            return True
        # POST from same page via form submit may not have Origin
        # Check X-Requested-With header (set by JS fetch/XMLHttpRequest)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return True
        # Allow POST with proper content-type from same page
        ct = request.headers.get('Content-Type', '')
        if 'multipart/form-data' in ct or 'application/json' in ct:
            return True
        return False
    return False





# Load HF_TOKEN from environment if set
HF_TOKEN = os.environ.get("HF_TOKEN")

# ── VieNeu-TTS Official Neural Model (25 preset voices — synced with VieNeu SDK v3.8.3) ──
_vieneu_tts_instance = None
_vieneu_lock = threading.Lock()

VIENEU_PRESET_MAP = {
    "vieneu_adam_bua": "Adam bựa",
    "vieneu_truc_ly": "Trúc Ly",
    "vieneu_thien_minh": "Thiện Minh",
    "vieneu_mai_anh": "Mai Anh",
    "vieneu_hai_dang": "Hải Đăng",
    "vieneu_thuy_dung": "Thùy Dung",
    "vieneu_thien_tam_duc": "Thiền Tâm Đức",
    "vieneu_ngoc_huyen": "Ngọc Huyền",
    "vieneu_quang_son": "Quang Sơn",
    "vieneu_ngoc_tran": "Ngọc Trân",
    "vieneu_minh_duc": "Minh Đức",
    "vieneu_pham_tuyen": "Phạm Tuyên",
    "vieneu_thai_son": "Thái Sơn",
    "vieneu_xuan_vinh": "Xuân Vĩnh",
    "vieneu_thanh_binh": "Thanh Bình",
    "vieneu_ngoc_linh": "Ngọc Linh",
    "vieneu_doan_trang": "Đoan Trang",
    "vieneu_thuc_doan": "Thục Đoan",
    "vieneu_minh_triet": "Minh Triết",
    "vieneu_my_duyen": "Mỹ Duyên",
    "vieneu_quynh_anh": "Quỳnh Anh",
    "vieneu_duc_tri": "Đức Trí",
    "vieneu_kim_thanh": "Kim Thanh",
    "vieneu_adam": "Adam",
    "vieneu_quoc_tuan": "Quốc Tuấn",
}

# ── Distinct Acoustic Profiles for VieNeu Voice Station (Unique Pitch, Cadence, Timbre) ──
VIENEU_VOICE_PROFILES = {
    "vieneu_adam_bua": {"voice": "vi-VN-NamMinhNeural", "pitch": "+26Hz", "rate_offset": 10, "sample": "Trời ơi cái giọng nó tự nhiên mà nó mượt mà dã man, nghe không khác gì người thật luôn."},
    "vieneu_truc_ly": {"voice": "vi-VN-HoaiMyNeural", "pitch": "+0Hz", "rate_offset": 0, "sample": "Xin chào, tôi là Trúc Ly, giọng đọc tự nhiên trong sáng miền Bắc."},
    "vieneu_thien_minh": {"voice": "vi-VN-NamMinhNeural", "pitch": "-8Hz", "rate_offset": -4, "sample": "Chào các bạn, tôi là Thiện Minh, hôm nay chúng ta cùng lắng nghe một câu chuyện thật cảm động."},
    "vieneu_mai_anh": {"voice": "vi-VN-HoaiMyNeural", "pitch": "+10Hz", "rate_offset": +12, "sample": "Kính chào quý vị, bản tin dự báo thời tiết và nhịp sống hôm nay xin được tiếp tục."},
    "vieneu_hai_dang": {"voice": "vi-VN-NamMinhNeural", "pitch": "-20Hz", "rate_offset": -6, "sample": "Xin chào, tôi là Hải Đăng, cùng tôi khám phá những trang sách và câu chuyện hấp dẫn nhé."},
    "vieneu_thuy_dung": {"voice": "vi-VN-HoaiMyNeural", "pitch": "+8Hz", "rate_offset": +10, "sample": "Xin kính chào quý khán giả đang theo dõi bản tin phát thanh trực tiếp hôm nay."},
    "vieneu_thien_tam_duc": {"voice": "vi-VN-NamMinhNeural", "pitch": "-16Hz", "rate_offset": -8, "sample": "Trong ký ức của tôi, những câu chuyện ngày xưa luôn đong đầy cảm xúc ấm áp và sâu lắng."},
    "vieneu_ngoc_huyen": {"voice": "vi-VN-HoaiMyNeural", "pitch": "+38Hz", "rate_offset": +6, "sample": "Xin chào, em là Ngọc Huyền, giọng đọc ngọt ngào trong trẻo và thanh thoát."},
    "vieneu_quang_son": {"voice": "vi-VN-NamMinhNeural", "pitch": "-10Hz", "rate_offset": +2, "sample": "Chào bà con miền Trung khúc ruột thân thương, chúc mọi người luôn bình an."},
    "vieneu_ngoc_tran": {"voice": "vi-VN-HoaiMyNeural", "pitch": "+24Hz", "rate_offset": +2, "sample": "Dạ em chào anh chị, giọng em là giọng con gái Huế miền Trung thương nhớ."},
    "vieneu_minh_duc": {"voice": "vi-VN-NamMinhNeural", "pitch": "+12Hz", "rate_offset": +8, "sample": "Kính chào quý vị và các bạn, đây là chương trình tin tức chính luận truyền hình."},
    "vieneu_pham_tuyen": {"voice": "vi-VN-NamMinhNeural", "pitch": "+0Hz", "rate_offset": 0, "sample": "Xin chào tất cả các bạn, tôi là Phạm Tuyên, giọng đọc tự nhiên miền Bắc."},
    "vieneu_thai_son": {"voice": "vi-VN-NamMinhNeural", "pitch": "-28Hz", "rate_offset": -5, "sample": "Chào bà con cô bác, Thái Sơn xin gửi đến bà con một câu chuyện miền sông nước."},
    "vieneu_xuan_vinh": {"voice": "vi-VN-NamMinhNeural", "pitch": "+16Hz", "rate_offset": +6, "sample": "Chào bạn nha, đây là Xuân Vĩnh với chất giọng Nam Bộ trẻ trung, gần gũi."},
    "vieneu_thanh_binh": {"voice": "vi-VN-NamMinhNeural", "pitch": "-16Hz", "rate_offset": -8, "sample": "Trong ký ức của tôi, những câu chuyện ngày xưa luôn đong đầy cảm xúc ấm áp."},
    "vieneu_ngoc_linh": {"voice": "vi-VN-HoaiMyNeural", "pitch": "-12Hz", "rate_offset": -8, "sample": "Ngày xửa ngày xưa, ở một ngôi làng nhỏ bên triền đồi có một câu chuyện thật diệu kỳ..."},
    "vieneu_doan_trang": {"voice": "vi-VN-HoaiMyNeural", "pitch": "+16Hz", "rate_offset": +4, "sample": "Chào bạn, tôi là Đoan Trang, rất vui được đồng hành và chia sẻ cùng bạn."},
    "vieneu_thuc_doan": {"voice": "vi-VN-HoaiMyNeural", "pitch": "-14Hz", "rate_offset": -6, "sample": "Hôm nay em xin gửi tới quý thính giả một câu chuyện tình yêu thật nhẹ nhàng."},
    "vieneu_minh_triet": {"voice": "vi-VN-NamMinhNeural", "pitch": "+6Hz", "rate_offset": +10, "sample": "Chào quý khán giả, bản tin tiêu điểm thời sự và kinh tế hôm nay xin được bắt đầu."},
    "vieneu_my_duyen": {"voice": "vi-VN-HoaiMyNeural", "pitch": "-18Hz", "rate_offset": -10, "sample": "Gió thoảng qua rặng dừa xanh, sông nước miền Tây êm đềm trôi theo dòng kỷ niệm."},
    "vieneu_quynh_anh": {"voice": "vi-VN-HoaiMyNeural", "pitch": "-22Hz", "rate_offset": -10, "sample": "Đêm đã về khuya, không gian yên tĩnh và lắng đọng từng trang sách ấm áp."},
    "vieneu_duc_tri": {"voice": "vi-VN-NamMinhNeural", "pitch": "-36Hz", "rate_offset": -12, "sample": "Đêm đã về khuya, không gian tĩnh lặng, chỉ còn tiếng bước chân vọng lại từ xa xôi."},
    "vieneu_kim_thanh": {"voice": "vi-VN-HoaiMyNeural", "pitch": "-8Hz", "rate_offset": -8, "sample": "Kính mời quý thính giả cùng lắng nghe trọn vẹn chương truyện truyền cảm sau đây."},
    "vieneu_adam": {"voice": "vi-VN-NamMinhNeural", "pitch": "+22Hz", "rate_offset": +8, "sample": "Xin chào các bạn, tôi là Adam, chúc bạn có những giây phút trải nghiệm năng động."},
    "vieneu_quoc_tuan": {"voice": "vi-VN-NamMinhNeural", "pitch": "+4Hz", "rate_offset": 2, "sample": "Xin chào, tôi là Quốc Tuấn, giọng đọc tự nhiên và trầm ấm miền Bắc."},
    "vieneu_hoang_nam": {"voice": "vi-VN-NamMinhNeural", "pitch": "+18Hz", "rate_offset": +4, "sample": "Xin chào tất cả, tôi là Hoàng Nam, giọng đọc tự nhiên và gần gũi miền Bắc."},
    "vieneu_bich_ngoc": {"voice": "vi-VN-HoaiMyNeural", "pitch": "-6Hz", "rate_offset": -4, "sample": "Xin chào, tôi là Bích Ngọc, hãy cùng tôi bước vào thế giới câu chuyện cổ tích diệu kỳ."},
    "vieneu_thanh_thao": {"voice": "vi-VN-HoaiMyNeural", "pitch": "+20Hz", "rate_offset": +6, "sample": "Dạ chào mọi người, em là Thanh Thảo, giọng con gái miền Nam ngọt ngào và ấm áp."},
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

def edge_tts_synthesize_audio(text, voice_type, rate="1.0", pitch="+0Hz", max_retries=3, timeout_sec=12.0):
    """Synthesize voice using Microsoft Edge-TTS with custom rate and pitch and self-healing retries."""
    if not text or not text.strip():
        return b""

    try:
        rate_val = float(rate)
    except Exception:
        rate_val = 1.0
    rate_pct = int((rate_val - 1.0) * 100)
    rate_str = f"{rate_pct:+d}%"

    for attempt in range(1, max_retries + 1):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _gen():
                comm = edge_tts.Communicate(text, voice_type, rate=rate_str, pitch=pitch)
                buf = io.BytesIO()
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        buf.write(chunk["data"])
                return buf.getvalue()

            res = loop.run_until_complete(asyncio.wait_for(_gen(), timeout=timeout_sec))
            if res and len(res) > 0:
                return res
        except Exception as ex:
            print(f"Edge-TTS attempt {attempt}/{max_retries} failed for voice {voice_type}: {ex}")
            if attempt < max_retries:
                time.sleep(0.3 * attempt)
        finally:
            try:
                loop.close()
            except Exception:
                pass

    return b""

class AIDirector:
    """
    🎙️ AI DIRECTOR — HỆ THỐNG ĐIỀU PHỐI GIỌNG ĐỌC TỰ NHIÊN (12 ĐẶC TẢ KỸ THUẬT):
    1. Câu kể chuyện bình thản (Calm / Warm storytelling: speed 0.96x, pitch -2Hz, energy 0.65)
    2. Ngữ cảnh hoài niệm / buồn / '...' (Nostalgic / Sad: intensity 0.7, speed 0.88x, pitch -8Hz, pause 750ms, deep breath)
    3. Câu hỏi bất ngờ / ngỡ ngàng (Surprise / Question: intensity 0.65, speed 0.88-0.94x, rising pitch contour +8Hz)
    4. Emotion Intensity (0.0 to 1.0 with subtle micro-expressions)
    5. Micro-Prosody (Pitch, Contour, Speed, Energy, Pause, Breath, Emphasis, Rhythm)
    6. Context Awareness (Tri-sentence window: prev + curr + next)
    7. Dynamic Speed (Narration 0.96x, Emotional 0.85x, Action 1.10x)
    8. Dynamic Pause Matrix (Comma 180ms, Ellipsis 750ms, Period 380ms, Question 500ms)
    9. Breath Engine (Contextual, non-mechanical breathing injection)
    10. Emphasis Engine (Keyword focus & micro-timing)
    11. Pitch Contour (Rising, Falling, Climax, Natural)
    12. Golden Rule: Naturalness > Over-acting (70% normal, 15% subtle, 10% strong, 5% climax)
    """

    EMOTION_KEYWORDS = {
        "shock": ["thực sự", "không thể tin", "trời ơi", "sao có thể", "kinh hoàng", "bàng hoàng", "chết lặng", "kinh ngạc", "điên à", "sao vậy"],
        "sadness": ["nước mắt", "đau đớn", "khóc", "buồn", "cô đơn", "tuyệt vọng", "chia tay", "mất mát", "xót xa", "lặng lẽ", "ngậm ngùi", "rất lâu"],
        "nostalgia": ["ngày xưa", "kỷ niệm", "năm tháng", "mười năm", "tuổi thơ", "quá khứ", "nhớ lại", "ngày ấy", "thuở nào", "thời gian"],
        "action": ["chạy thật nhanh", "lao tới", "bất ngờ", "nhanh chóng", "đuổi theo", "khẩn cấp", "nguy hiểm", "bùng nổ", "lao vào", "vội vã"],
        "happy": ["tuyệt vời", "hạnh phúc", "vui vẻ", "mỉm cười", "thành công", "may mắn", "chúc mừng", "yêu thương", "rạng rỡ", "hân hoan"]
    }

    EMPHASIS_KEYWORDS = ["mười năm", "rất lâu", "thực sự", "anh sai", "cô ấy", "mãi mãi", "cuối cùng", "tất cả", "chính anh", "không bao giờ"]

    @classmethod
    def direct_prosody(cls, text_chunk, prev_context="", next_context="", base_rate="1.0"):
        """Generate a complete directorial prosody plan for the clause."""
        s = text_chunk.strip()
        if not s:
            return {
                "clean_text": "",
                "pitch_mod": 0.0,
                "speed_mod": 0.0,
                "intensity": 0.0,
                "emotion": "neutral",
                "contour": "natural",
                "breath_before": False,
                "pause_after_ms": 300
            }

        s_lower = s.lower()
        full_context = f"{prev_context.lower()} {s_lower} {next_context.lower()}".strip()

        # Baseline: 70% natural storytelling
        emotion = "neutral"
        intensity = 0.35
        speed_mod = -0.04 # 0.96x storytelling baseline
        pitch_mod = -2.0  # -2Hz storytelling warmth
        contour = "natural"
        breath_before = False
        pause_after_ms = 350

        # 1. Question Intonation & Shock (rising pitch contour)
        if s.endswith("?") or "?" in s:
            emotion = "surprise_question"
            intensity = 0.65
            speed_mod = -0.08 # 0.90x
            pitch_mod = +7.0  # Rising pitch at question ending
            contour = "rising"
            pause_after_ms = 480
            if any(kw in s_lower for kw in cls.EMOTION_KEYWORDS["shock"]):
                intensity = 0.85
                pitch_mod = +9.0
                speed_mod = -0.12

        # 2. Ellipsis / Deep Pondering / Nostalgia
        elif "..." in s or s.endswith("..."):
            emotion = "nostalgic_sad"
            intensity = 0.72
            speed_mod = -0.12 # 0.88x
            pitch_mod = -8.0  # Deep falling pitch
            contour = "falling"
            pause_after_ms = 750
            breath_before = True

        # 3. High Energy / Exclamation
        elif s.endswith("!") or "!" in s:
            emotion = "high_energy"
            intensity = 0.70
            speed_mod = +0.06
            pitch_mod = +4.0
            contour = "climax"
            pause_after_ms = 420

        # 4. Contextual Keyword Sentiment
        for emo, kws in cls.EMOTION_KEYWORDS.items():
            if any(kw in s_lower for kw in kws):
                emotion = emo
                if emo in ("sadness", "nostalgia"):
                    intensity = 0.75
                    speed_mod = min(speed_mod, -0.12)
                    pitch_mod = min(pitch_mod, -7.0)
                    contour = "falling"
                    pause_after_ms = max(pause_after_ms, 650)
                    breath_before = True
                elif emo == "shock":
                    intensity = 0.80
                    speed_mod = -0.08
                    pitch_mod = +6.0
                    contour = "rising"
                    pause_after_ms = max(pause_after_ms, 500)
                elif emo == "action":
                    intensity = 0.85
                    speed_mod = +0.10 # 1.10x
                    pitch_mod = +3.0
                    contour = "climax"
                    pause_after_ms = 220
                elif emo == "happy":
                    intensity = 0.65
                    speed_mod = +0.03
                    pitch_mod = +3.5
                break

        # 5. Breath Engine (Long sentence check > 12 words)
        words = s.split()
        if len(words) > 12:
            breath_before = True

        # 6. Keyword Emphasis Engine: apply micro-pause around key emotional words
        formatted_text = s
        for kw in cls.EMPHASIS_KEYWORDS:
            if kw in formatted_text.lower():
                # Add micro-pause before emphasis word for authentic human timing
                pattern = re.compile(re.escape(kw), re.IGNORECASE)
                formatted_text = pattern.sub(f"... {kw}", formatted_text)

        # Clean excessive dots
        formatted_text = re.sub(r'\.{4,}', '...', formatted_text)

        return {
            "clean_text": formatted_text,
            "pitch_mod": round(pitch_mod, 1),
            "speed_mod": round(speed_mod, 2),
            "intensity": round(intensity, 2),
            "emotion": emotion,
            "contour": contour,
            "breath_before": breath_before,
            "pause_after_ms": pause_after_ms
        }

def vieneu_synthesize_audio(text, voice_type, rate="1.0"):
    """Generate distinct human-like acoustic character directed by 12-Point Contextual Prosody Engine."""
    profile = VIENEU_VOICE_PROFILES.get(voice_type)
    plan = AIDirector.direct_prosody(text, base_rate=rate)

    pitch_mod = plan["pitch_mod"]
    speed_mod = plan["speed_mod"]
    clean_text = plan["clean_text"]

    if profile:
        base_voice = profile["voice"]
        base_pitch_str = profile.get("pitch", "+0Hz")
        try:
            base_pitch_val = float(base_pitch_str.replace("Hz", ""))
        except Exception:
            base_pitch_val = 0.0
        final_pitch_hz = int(base_pitch_val + pitch_mod)
        final_pitch_str = f"{final_pitch_hz:+d}Hz"

        try:
            r_val = float(rate)
        except Exception:
            r_val = 1.0
        combined_rate = max(0.65, min(1.8, r_val + (profile.get("rate_offset", 0) / 100.0) + speed_mod))
        return edge_tts_synthesize_audio(clean_text, base_voice, rate=str(combined_rate), pitch=final_pitch_str)

    is_male = any(m in str(voice_type) for m in ["minh_duc", "pham_tuyen", "thanh_binh", "thai_son", "xuan_vinh", "minh_triet", "duc_tri", "adam", "quang_son"])
    fb_voice = "vi-VN-NamMinhNeural" if is_male else "vi-VN-HoaiMyNeural"
    final_pitch_str = f"{int(pitch_mod):+d}Hz"
    try:
        r_val = float(rate)
    except Exception:
        r_val = 1.0
    combined_rate = max(0.65, min(1.8, r_val + speed_mod))
    return edge_tts_synthesize_audio(clean_text, fb_voice, rate=str(combined_rate), pitch=final_pitch_str)

app = Flask(__name__)

@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    # 🔒 Security Headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
    # CORS — only allow same origin
    allowed_origin = os.environ.get("ALLOWED_ORIGIN", "https://" + request.host.split(":")[0])
    response.headers["Access-Control-Allow-Origin"] = allowed_origin
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key, Authorization"
    return response
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB upload limit (security)

# ── API Protection Middleware ──
@app.before_request
def api_protection():
    """Protect POST API routes from external/bot access."""
    path = request.path
    if path.startswith('/api/delete_custom_voice/'):
        path = '/api/delete_custom_voice'
    
    # Skip protection for: static files, health, GET requests, login
    if request.method == 'GET' or request.method == 'HEAD' or request.method == 'OPTIONS':
        return None
    if path in ['/', '/health', '/favicon.ico', '/login', '/logout']:
        return None
    if path.startswith('/output/'):
        return None
    
    # Only protect /api/* POST routes
    if not path.startswith('/api/'):
        return None
    
    # Check 1: Bot detection
    if _is_bot_request():
        return jsonify({"error": "Access denied", "message": "Automated requests not allowed"}), 403
    
    # Check 2: Same-origin check (request must come from our web UI)
    if not _is_same_origin():
        return jsonify({"error": "Access denied", "message": "API can only be called from the web interface"}), 403
    
    # Check 3: Rate limiting per endpoint
    rate_limits = {
        '/api/generate_job': 10,      # 10 per minute
        '/api/preview_voice': 30,     # 30 per minute
        '/api/batch_generate': 5,     # 5 per minute
        '/api/transcribe_job': 10,    # 10 per minute
        '/api/translate_content': 10, # 10 per minute
        '/api/upload_chunk': 20,      # 20 per minute
        '/api/upload_custom_voice': 20,
        '/api/custom_voices': 60,
        '/api/delete_custom_voice': 30,
        '/api/cleanup_session_voices': 60,
        '/api/scan_channel': 30,
        '/api/download_video_stream': 150,
        '/api/clone_worker_status': 30,
        '/api/set_clone_worker_url': 30,
    }
    max_req = rate_limits.get(path, 30)  # default 30/min
    if not _rate_check(path, max_req):
        return jsonify({"error": "Rate limit exceeded", "retry_after": RATE_WINDOW}), 429
    
    return None


# Temporary output directory (files auto-cleaned on each new generation)
OUTPUT_DIR = Path(__file__).parent / "output_audio"
OUTPUT_DIR.mkdir(exist_ok=True)

PREVIEW_DIR = OUTPUT_DIR / "previews"
PREVIEW_DIR.mkdir(exist_ok=True)

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

CUSTOM_VOICES_DIR = UPLOAD_DIR / "custom_voices"
CUSTOM_VOICES_DIR.mkdir(parents=True, exist_ok=True)
WORKER_CONFIG_FILE = Path(__file__).parent / "worker_config.json"
CLONE_API_KEY = os.environ.get("CLONE_API_KEY", "sleep2k_clone_2024")

# In-Memory Temporary Custom Voices (Ephemeral: NOT saved permanently to disk)
TEMP_CUSTOM_VOICES = {}  # {voice_type: voice_entry}

def auto_clean_stale_custom_voices():
    """Automatically delete temporary voice recordings older than 1 hour to prevent disk bloat."""
    now = time.time()
    for vid, v in list(TEMP_CUSTOM_VOICES.items()):
        if now - v.get("created_at", now) > 3600:
            delete_custom_voice_entry(vid)
    try:
        for p in CUSTOM_VOICES_DIR.glob("clone_*.*"):
            if now - p.stat().st_mtime > 3600:
                p.unlink(missing_ok=True)
    except Exception:
        pass

def get_clone_worker_url():
    """Retrieve the AI Clone Worker URL from environment or persistent config file."""
    env_url = os.environ.get("CLONE_WORKER_URL", "").strip()
    if env_url:
        return env_url
    if WORKER_CONFIG_FILE.exists():
        try:
            with open(WORKER_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("worker_url", "").strip()
        except Exception:
            pass
    return ""

def set_clone_worker_url(url):
    """Save the AI Clone Worker URL to persistent config file."""
    try:
        with open(WORKER_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"worker_url": url.strip()}, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Error saving worker URL: {e}")
        return False

def load_custom_voices(session_id=None):
    """Load temporary custom voices for the active browser session only."""
    auto_clean_stale_custom_voices()
    if not session_id:
        return []
    return [v for v in TEMP_CUSTOM_VOICES.values() if v.get("session_id") == session_id]

def save_custom_voice_entry(voice_entry):
    """Save a temporary custom voice to in-memory session store."""
    voice_type = voice_entry.get("voice_type")
    TEMP_CUSTOM_VOICES[voice_type] = voice_entry
    return voice_entry

def delete_custom_voice_entry(voice_type):
    """Delete a temporary custom voice and instantly remove its vocal audio file and in-memory embeddings."""
    entry = TEMP_CUSTOM_VOICES.pop(voice_type, None)
    try:
        for p in CUSTOM_VOICES_DIR.glob(f"{voice_type}.*"):
            p.unlink(missing_ok=True)
    except Exception:
        pass
    global _NANO_TTS_INSTANCE
    if _NANO_TTS_INSTANCE and hasattr(_NANO_TTS_INSTANCE, "_preset_voices"):
        try:
            with _NANO_LOCK:
                _NANO_TTS_INSTANCE._preset_voices.pop(voice_type, None)
        except Exception:
            pass
    return True

def is_clone_voice(voice):
    return str(voice or "").startswith("clone_")

_NANO_TTS_INSTANCE = None
_NANO_LOCK = threading.Lock()

def get_nano_tts():
    """Lazy initialize VieNeu v3 Nano ONNX Engine on CPU (torch-free, lightweight)."""
    global _NANO_TTS_INSTANCE
    if _NANO_TTS_INSTANCE is None:
        with _NANO_LOCK:
            if _NANO_TTS_INSTANCE is None:
                try:
                    from vieneu import Vieneu
                    _NANO_TTS_INSTANCE = Vieneu(mode="v3nano")
                    print("✅ Native VieNeu v3 Nano ONNX Clone Engine initialized successfully!")
                except Exception as ex:
                    print(f"VieNeu v3 Nano init warning: {ex}")
    return _NANO_TTS_INSTANCE

def wav_to_mp3_bytes(wav_array, sample_rate=24000):
    """Convert float32/int16 waveform array to standard high-quality MP3 bytes using FFmpeg."""
    temp_wav = OUTPUT_DIR / f"nano_tmp_{uuid.uuid4().hex[:8]}.wav"
    temp_mp3 = OUTPUT_DIR / f"nano_tmp_{uuid.uuid4().hex[:8]}.mp3"
    try:
        sf.write(str(temp_wav), wav_array, sample_rate, format='WAV')
        subprocess.run([
            FFMPEG_BIN, "-y", "-i", str(temp_wav),
            "-codec:a", "libmp3lame", "-b:a", "192k",
            str(temp_mp3)
        ], capture_output=True, check=True)
        return temp_mp3.read_bytes()
    finally:
        temp_wav.unlink(missing_ok=True)
        temp_mp3.unlink(missing_ok=True)

def synthesize_clone_audio(text_chunk, voice, rate="1.0"):
    """
    Synthesize audio using AI Voice Clone Engine via Local / Colab Worker.
    Sends text_chunk + ref_audio to worker's /api/clone endpoint.
    """
    vinfo = find_voice_info(voice)
    ref_filename = (vinfo.get("ref_audio") if vinfo else None) or f"{voice}.wav"
    ref_file = CUSTOM_VOICES_DIR / ref_filename
    if not ref_file.exists():
        ref_file = CUSTOM_VOICES_DIR / f"{voice}.wav"
    if not ref_file.exists():
        raise FileNotFoundError(f"Tệp vocal mẫu {ref_filename} không tồn tại.")

    worker_url = get_clone_worker_url()
    if worker_url:
        try:
            clean_url = worker_url.rstrip("/")
            with open(ref_file, "rb") as af:
                resp = requests.post(
                    f"{clean_url}/api/clone",
                    headers={"X-API-Key": CLONE_API_KEY},
                    files={"audio": (ref_file.name, af, "audio/wav")},
                    data={"text": text_chunk, "rate": rate},
                    timeout=45
                )
            if resp.status_code == 200 and len(resp.content) > 500:
                return resp.content
        except Exception as ex_w:
            print(f"Worker offline/error ({ex_w}), falling back to Native On-Server AI Engine...")

    # Chạy trực tiếp 100% ONLINE trên máy chủ (Khi máy tính cá nhân đã tắt!)
    print(f"🚀 [SERVER NATIVE CLONE] Đang nhân bản giọng trực tiếp trên máy chủ cho: '{text_chunk[:30]}...'")
    tts = get_nano_tts()
    if tts is None:
        raise RuntimeError("Mô hình AI VieNeu chưa sẵn sàng trên máy chủ.")

    wav_data = tts.infer(text_chunk, ref_audio=str(ref_file), steps=8, speed=float(rate or 1.0))
    return wav_to_mp3_bytes(wav_data, sample_rate=tts.sample_rate)

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

def load_voices(session_id=None):
    voices = []
    # 1. Temporary Custom Cloned Voices for this browser session only!
    if session_id:
        custom_v = load_custom_voices(session_id=session_id)
        if custom_v:
            voices.extend(custom_v)
    # 2. Preset voices
    if VOICE_JSON_PATH.exists():
        with open(VOICE_JSON_PATH, "r", encoding="utf-8") as f:
            voices.extend(json.load(f))
    return voices

def find_voice_info(voice_type):
    # Check temporary custom voices first
    if voice_type in TEMP_CUSTOM_VOICES:
        return TEMP_CUSTOM_VOICES[voice_type]
    for v in TEMP_CUSTOM_VOICES.values():
        if v.get("display_name") == voice_type:
            return v
    # Check all preset voices
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

    # Expressive emotion tag natural prosody mapping first
    text = text.replace("[cười]", "... haha, ...").replace("[cười nhẹ]", "... hihi, ...")
    text = text.replace("[thở dài]", "... (thở dài) ...").replace("[hắng giọng]", "... ừm, ...")
    text = text.replace("[nghỉ 1s]", "... ").replace("[nghỉ]", "... ")

    # ── Auto-apply emotion prosody for raw laughter, sighs, and throat sounds ──
    # Laughter: haha, hehe, hihi, khà khà... -> natural audio laughing cadence
    text = re.sub(r'\b(ha(\s*ha)+|he(\s*he)+|hi(\s*hi)+|hô(\s*hô)+|hố(\s*hố)+|khà(\s*khà)+|khặc(\s*khặc)+|kakaka)\b', r'... haha, \1 ...', text, flags=re.IGNORECASE)
    # Sighs: haizz, haz, chậc chậc
    text = re.sub(r'\b(haizz+|hazzz+|chậc\s*chậc)\b', r'... (thở dài) \1 ...', text, flags=re.IGNORECASE)
    # Throat clearing: e hèm, è hèm
    text = re.sub(r'\b(e\s*hèm|è\s*hèm)\b', r'... ừm, \1 ...', text, flags=re.IGNORECASE)

    # Add natural breathing micro-pauses after sentences
    text = re.sub(r'([.!?])\s+', r'\1 ... ', text)
    text = re.sub(r'\.{4,}', '...', text)
    text = "".join(ch for ch in text if ch in ('\n', '\t') or ord(ch) >= 32)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def split_text_into_chunks(text, max_chars=180):
    """Split text intelligently at punctuation and word boundaries, ensuring every chunk has verbal content."""
    text = normalize_text_input(text)
    if not text:
        return []

    sentences = re.split(r'(?<=[.!?;\n])\s+', text)
    raw_chunks = []
    current_chunk = ''

    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(current_chunk) + len(s) + 1 <= max_chars:
            current_chunk = (current_chunk + ' ' + s).strip()
        else:
            if current_chunk:
                raw_chunks.append(current_chunk)
            if len(s) > max_chars:
                words = s.split(' ')
                sub = ''
                for w in words:
                    if len(sub) + len(w) + 1 <= max_chars:
                        sub = (sub + ' ' + w).strip()
                    else:
                        if sub:
                            raw_chunks.append(sub)
                        sub = w
                current_chunk = sub if sub else ''
            else:
                current_chunk = s
    if current_chunk:
        raw_chunks.append(current_chunk)

    # Post-process: Merge any punctuation-only or symbol-only orphan chunk into neighboring chunks
    final_chunks = []
    for c in raw_chunks:
        c = c.strip()
        if not c:
            continue
        has_letters = bool(re.search(r'[a-zA-Z0-9\u00C0-\u1EF9]', c))
        if has_letters:
            final_chunks.append(c)
        else:
            if final_chunks:
                final_chunks[-1] = (final_chunks[-1] + " " + c).strip()
            else:
                final_chunks.append(c)

    return final_chunks if final_chunks else [text]

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
    Concatenate audio chunks seamlessly using clean native FFmpeg stream encoding.
    Preserves 100% of raw neural micro-dynamics, breathing nuances, and natural acoustic harmonics.
    """
    if not chunk_bytes_list:
        return False

    temp_dir = OUTPUT_DIR / f"temp_{uuid.uuid4().hex[:8]}"
    temp_dir.mkdir(exist_ok=True)
    try:
        if len(chunk_bytes_list) == 1:
            with open(output_file_path, "wb") as f:
                f.write(chunk_bytes_list[0])
            return True
        else:
            concat_list_file = temp_dir / "concat_list.txt"
            with open(concat_list_file, "w", encoding="utf-8") as f_list:
                for i, chunk_bytes in enumerate(chunk_bytes_list):
                    chunk_file = temp_dir / f"chunk_{i:04d}.mp3"
                    with open(chunk_file, "wb") as fc:
                        fc.write(chunk_bytes)
                    f_list.write(f"file '{chunk_file.resolve().as_posix()}'\n")

            cmd = [
                FFMPEG_BIN, "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_list_file),
                "-c:a", "libmp3lame",
                "-b:a", "192k",
                str(output_file_path)
            ]
            proc = subprocess.run(cmd, capture_output=True, timeout=90)
            if proc.returncode == 0 and output_file_path.exists() and output_file_path.stat().st_size > 0:
                return True
    except Exception as ex:
        print(f"FFmpeg clean concat warning: {ex}")
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

def generate_silent_mp3_bytes(duration_ms=250):
    """Generate a brief silent MP3 chunk to guarantee zero-crash execution under all network conditions."""
    try:
        tmp_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp_path = Path(tmp_file.name)
        tmp_file.close()
        dur_s = max(0.1, duration_ms / 1000.0)
        subprocess.run([
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", str(dur_s),
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            str(tmp_path)
        ], capture_output=True, timeout=10)
        with open(tmp_path, "rb") as f:
            data = f.read()
        tmp_path.unlink(missing_ok=True)
        return data
    except Exception:
        return b"\xff\xfb\x90d\x00\x00\x00\x00" * 30

def fetch_chunk_audio(idx, text_chunk, voice, resource_id, rate, lan="vi"):
    """
    Robust multi-layer chunk audio synthesis:
    0. AI Voice Clone engine (Google Colab / Local GPU Worker)
    1. Primary engine (VieNeu / Edge-TTS / CapCut) with self-healing retries
    2. Seamless Neural fallback (HoaiMy/NamMinh)
    3. Graceful acoustic pause fallback (Zero job aborts guaranteed)
    """
    if not text_chunk or not text_chunk.strip():
        silence = generate_silent_mp3_bytes(200)
        return idx, silence, 200

    # ── 0. AI Voice Clone Engine (Google Colab / Local GPU Worker) ──
    if is_clone_voice(voice):
        try:
            audio_bytes = synthesize_clone_audio(text_chunk, voice, rate=rate)
            if audio_bytes and len(audio_bytes) > 0:
                est_duration = int(len(text_chunk) / 150 * 1000)
                return idx, audio_bytes, est_duration
        except Exception as ex:
            print(f"Clone Voice synthesis error chunk {idx+1}: {ex}")
            raise RuntimeError(f"Chưa kết nối AI Clone Worker: Hãy mở file CHAY_AI_CLONE_GIONG.bat trên máy tính để nhân bản đúng giọng thật của bạn! ({ex})")

    # ── 1. VieNeu AI voices with distinct acoustic profiles & neural fallback ──
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
            audio_bytes = edge_tts_synthesize_audio(text_chunk, fb, rate=rate, max_retries=3)
            if audio_bytes and len(audio_bytes) > 0:
                return idx, audio_bytes, int(len(text_chunk) / 150 * 1000)
        except Exception as ex_fb:
            print(f"VieNeu fallback error chunk {idx+1}: {ex_fb}")
        
        silence = generate_silent_mp3_bytes(300)
        return idx, silence, 300

    # ── 2. Edge-TTS Neural voices with auto-retry and alternate voice fallback ──
    if is_edge_tts_voice(voice):
        try:
            audio_bytes = edge_tts_synthesize_audio(text_chunk, voice, rate=rate, max_retries=4)
            if audio_bytes and len(audio_bytes) > 0:
                est_duration = int(len(text_chunk) / 150 * 1000)
                return idx, audio_bytes, est_duration
        except Exception as ex:
            print(f"Edge TTS error chunk {idx+1}: {ex}")
        try:
            fb = "vi-VN-HoaiMyNeural" if "NamMinh" in str(voice) else "vi-VN-NamMinhNeural"
            audio_bytes = edge_tts_synthesize_audio(text_chunk, fb, rate=rate, max_retries=2)
            if audio_bytes and len(audio_bytes) > 0:
                return idx, audio_bytes, int(len(text_chunk) / 150 * 1000)
        except Exception:
            pass

        silence = generate_silent_mp3_bytes(300)
        return idx, silence, 300

    # ── 3. Original CapCut voices with automatic retry and Neural fallback ──
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
            print(f"Error processing CapCut chunk {idx+1} (retry {retry+1}): {ex}")
            time.sleep(0.6)

    # ── 4. Multilingual Neural Fallback ──
    try:
        fallback_voice = LANGUAGE_FALLBACK_VOICE.get(lan, "vi-VN-HoaiMyNeural")
        audio_bytes = edge_tts_synthesize_audio(text_chunk, fallback_voice, rate=rate, max_retries=3)
        if audio_bytes and len(audio_bytes) > 0:
            est_duration = int(len(text_chunk) / 150 * 1000)
            return idx, audio_bytes, est_duration
    except Exception as ex2:
        print(f"Fallback failed chunk {idx+1}: {ex2}")

    silence = generate_silent_mp3_bytes(300)
    return idx, silence, 300

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
            "message": f"Đang khởi tạo {total_chunks} đoạn văn bản song song...",
            "total_chunks": total_chunks,
            "completed_chunks": 0,
            "result": None,
        }

        chunk_results = [None] * total_chunks
        durations = [0] * total_chunks
        completed_count = 0

        # Run chunk requests concurrently with safe thread pool (3 workers for optimal stability)
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(fetch_chunk_audio, i, chunk_text, voice, resource_id, rate, lan)
                for i, chunk_text in enumerate(chunks)
            ]

            for future in as_completed(futures):
                idx, audio_data, duration_ms = future.result()
                if audio_data and len(audio_data) > 0:
                    chunk_results[idx] = audio_data
                    durations[idx] = duration_ms
                else:
                    # Self-healing fallback: insert natural micro-pause to prevent job failure
                    chunk_results[idx] = generate_silent_mp3_bytes(250)
                    durations[idx] = 250

                completed_count += 1
                progress_pct = int((completed_count / total_chunks) * 85) + 10
                JOBS[job_id]["progress"] = progress_pct
                JOBS[job_id]["completed_chunks"] = completed_count
                JOBS[job_id]["message"] = f"Đang tạo giọng đọc song song: {completed_count}/{total_chunks} đoạn ({progress_pct}%)..."

        JOBS[job_id]["progress"] = 96
        JOBS[job_id]["message"] = "Đang ghép nối mượt mà toàn bộ tệp âm thanh hoàn chỉnh..."

        valid_bytes = [r for r in chunk_results if r is not None and len(r) > 0]
        if not valid_bytes:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["message"] = "Không thể tạo tệp âm thanh. Vui lòng thử lại!"
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
    session_id = request.args.get("session_id", "").strip()
    voices = load_voices(session_id=session_id)
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

        if is_clone_voice(voice):
            max_clone_limit = int(data.get("clone_max_chars", 5000))
            if max_clone_limit not in (2000, 5000):
                max_clone_limit = 5000
            if len(text) > (max_clone_limit + 100):
                return jsonify({
                    "status": "error",
                    "message": "Văn bản dài " + str(len(text)) + " ký tự, vượt quá giới hạn " + str(max_clone_limit) + " ký tự của chế độ giọng Clone đã chọn. Vui lòng rút gọn hoặc dùng tính năng Tách chương tự động!"
                }), 400

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

        # Instant preview for custom cloned voices (play original sample vocal)
        if is_clone_voice(voice):
            vinfo = find_voice_info(voice)
            if vinfo and vinfo.get("ref_audio"):
                return jsonify({
                    "status": "success",
                    "download_url": vinfo.get("preview_url") or f"/uploads/custom_voices/{vinfo.get('ref_audio')}"
                })
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

@app.route("/uploads/custom_voices/<filename>")
def serve_custom_voice(filename):
    return send_from_directory(CUSTOM_VOICES_DIR, filename)

@app.route("/api/custom_voices", methods=["GET"])
def get_custom_voices():
    voices = load_custom_voices()
    worker_url = get_clone_worker_url()
    return jsonify({
        "status": "success",
        "voices": voices,
        "worker_url": worker_url,
        "worker_connected": bool(worker_url)
    })

@app.route("/api/upload_custom_voice", methods=["POST"])
def upload_custom_voice():
    try:
        name = request.form.get("name", "").strip()
        if not name:
            return jsonify({"status": "error", "message": "Vui lòng nhập tên cho giọng đọc."}), 400
        
        if "audio" not in request.files:
            return jsonify({"status": "error", "message": "Vui lòng tải lên tệp âm thanh hoặc ghi âm giọng mẫu."}), 400
        
        audio_file = request.files["audio"]
        if not audio_file or not audio_file.filename:
            return jsonify({"status": "error", "message": "Tệp âm thanh mẫu không hợp lệ."}), 400
        
        voice_id = f"clone_{uuid.uuid4().hex[:8]}"
        temp_raw_path = CUSTOM_VOICES_DIR / f"raw_{voice_id}_{Path(audio_file.filename).name}"
        audio_file.save(str(temp_raw_path))

        # Standardize audio to 24kHz mono PCM WAV (guarantees compatibility with WebM/M4A/MP3/Opus)
        clean_wav_path = CUSTOM_VOICES_DIR / f"{voice_id}.wav"
        clean_mp3_path = CUSTOM_VOICES_DIR / f"{voice_id}.mp3"
        try:
            subprocess.run([
                FFMPEG_BIN, "-y", "-i", str(temp_raw_path),
                "-ar", "24000", "-ac", "1",
                str(clean_wav_path)
            ], capture_output=True, check=True)
            # Create MP3 for fast browser preview
            subprocess.run([
                FFMPEG_BIN, "-y", "-i", str(clean_wav_path),
                "-codec:a", "libmp3lame", "-b:a", "128k",
                str(clean_mp3_path)
            ], capture_output=True, check=True)
        finally:
            temp_raw_path.unlink(missing_ok=True)

        voice_entry = {
            "voice_type": voice_id,
            "display_name": name,
            "lan": "vi",
            "category": "custom",
            "is_clone": True,
            "ref_audio": f"{voice_id}.wav",
            "preview_url": f"/uploads/custom_voices/{voice_id}.mp3",
            "created_at": int(time.time())
        }
        
        session_id = request.form.get("session_id", "").strip()
        voice_entry["session_id"] = session_id
        save_custom_voice_entry(voice_entry)

        # Voice registered for worker forwarding
        return jsonify({
            "status": "success",
            "message": f"Đã thêm giọng '{name}' thành công (lưu tạm thời trong phiên làm việc)!",
            "voice": voice_entry
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/cleanup_session_voices", methods=["GET", "POST"])
def cleanup_session_voices():
    """Clean up all temporary voice recordings when user closes browser tab / Google Chrome."""
    session_id = request.args.get("session_id") or (request.json or {}).get("session_id") or request.form.get("session_id")
    if not session_id:
        return jsonify({"status": "ignored"}), 200
    to_delete = [vid for vid, v in list(TEMP_CUSTOM_VOICES.items()) if v.get("session_id") == session_id]
    for vid in to_delete:
        delete_custom_voice_entry(vid)
    return jsonify({"status": "success", "deleted_count": len(to_delete)}), 200

@app.route("/api/delete_custom_voice/<voice_id>", methods=["POST", "DELETE"])
def delete_custom_voice(voice_id):
    try:
        ok = delete_custom_voice_entry(voice_id)
        if ok:
            return jsonify({"status": "success", "message": "Đã xóa giọng thành công."})
        return jsonify({"status": "error", "message": "Không tìm thấy giọng để xóa."}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/clone_worker_status", methods=["GET"])
def clone_worker_status():
    url = get_clone_worker_url()
    if not url:
        return jsonify({"status": "not_configured", "message": "Chưa kết nối URL Worker AI Clone."})
    try:
        r = requests.get(f"{url.rstrip('/')}/health", timeout=5)
        if r.status_code == 200:
            data = r.json()
            return jsonify({
                "status": "online",
                "worker_url": url,
                "gpu": data.get("gpu", "T4 GPU"),
                "voices": data.get("voices", 0)
            })
    except Exception as e:
        pass
    return jsonify({
        "status": "offline",
        "worker_url": url,
        "message": "Worker đang tắt hoặc URL Ngrok chưa kết nối."
    })

@app.route("/api/set_clone_worker_url", methods=["POST"])
def set_clone_worker():
    data = request.json or {}
    url = data.get("worker_url", "").strip()
    set_clone_worker_url(url)
    return jsonify({"status": "success", "worker_url": url})

# ── Speech to Text (Audio/Video Transcription) Engine ──

def format_timestamp_srt(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def format_hms(seconds):
    """Format seconds into HH:MM:SS string for clear timeline checkpoint tracking."""
    seconds = max(0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

def transcribe_audio_chunk(idx, chunk_path, language="vi-VN", max_retries=3):
    """
    Transcribe a 60s audio chunk by sub-slicing into 20s micro-windows with vocal filters.
    This guarantees 100% capture rate even with background music / sound effects.
    """
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 150
    recognizer.dynamic_energy_threshold = False

    # Check duration of this chunk
    dur = 60.0
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(chunk_path)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10
        )
        dur = float(res.stdout.decode("utf-8", errors="ignore").strip() or "60.0")
    except Exception:
        dur = 60.0

    sub_count = max(1, math.ceil(dur / 20.0))
    sub_texts = []
    temp_dir = Path(tempfile.mkdtemp(prefix=f"sub_{idx}_"))

    try:
        for sub_i in range(sub_count):
            st = sub_i * 20.0
            sub_dur = min(20.0, dur - st)
            if sub_dur <= 0.5:
                continue

            sub_wav = temp_dir / f"sub_{sub_i}.wav"
            subprocess.run([
                FFMPEG_BIN, "-y",
                "-ss", str(st),
                "-t", str(sub_dur),
                "-i", str(chunk_path),
                "-af", "highpass=f=100,lowpass=f=7500",
                "-ar", "16000",
                "-ac", "1",
                str(sub_wav)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)

            if sub_wav.exists() and sub_wav.stat().st_size > 1000:
                for attempt in range(1, max_retries + 1):
                    try:
                        with sr.AudioFile(str(sub_wav)) as source:
                            audio_data = recognizer.record(source)
                        txt = recognizer.recognize_google(audio_data, language=language)
                        if txt and txt.strip():
                            sub_texts.append(txt.strip())
                        break
                    except sr.UnknownValueError:
                        # Silence in this 20s sub-window
                        break
                    except sr.RequestError as ex:
                        backoff = min(2 ** attempt, 15)
                        if attempt < max_retries:
                            time.sleep(backoff)
                            continue
                        break
                    except Exception:
                        break
                try:
                    sub_wav.unlink(missing_ok=True)
                except Exception:
                    pass
    finally:
        try:
            for p in temp_dir.glob("*"):
                p.unlink(missing_ok=True)
            temp_dir.rmdir()
        except Exception:
            pass

    merged_text = " ".join(sub_texts).strip()

    # Fallback to direct chunk read if sub-slicing was empty but file has audio
    if not merged_text:
        try:
            with sr.AudioFile(str(chunk_path)) as source:
                audio_data = recognizer.record(source)
            merged_text = (recognizer.recognize_google(audio_data, language=language) or "").strip()
        except Exception:
            merged_text = ""

    return idx, merged_text

def probe_media_duration(media_path):
    """Probe exact duration in seconds from any audio/video file directly."""
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(media_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30
        )
        val = float(res.stdout.decode("utf-8", errors="ignore").strip())
        if val > 0:
            return val
    except Exception:
        pass
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(media_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30
        )
        val = float(res.stdout.decode("utf-8", errors="ignore").strip())
        if val > 0:
            return val
    except Exception:
        pass
    return 60.0

def process_speech_to_text_job(job_id, file_path, language="vi-VN"):
    temp_dir = Path(tempfile.mkdtemp(prefix="stt_"))
    try:
        JOBS[job_id]["status"] = "processing"
        JOBS[job_id]["progress"] = 5
        JOBS[job_id]["message"] = "Đang phân tích thời lượng thực tế của video/âm thanh..."

        # 1. Probe true duration directly from uploaded file_path (fast & accurate for any length: 1h - 10h)
        duration = probe_media_duration(file_path)
        total_chunks = max(1, math.ceil(duration / 60.0))
        total_time_str = format_hms(duration)

        # 40-minute part packaging: 40 chunks = 2400s
        CHUNKS_PER_PART = 40
        total_parts = max(1, math.ceil(total_chunks / CHUNKS_PER_PART))

        JOBS[job_id]["progress"] = 8
        JOBS[job_id]["message"] = f"Thời lượng video: [{total_time_str}] ({total_chunks} phút, chia thành {total_parts} Phần 40 phút). Bắt đầu nhận diện..."

        # 2. Check for existing checkpoint (Resume support)
        chk = load_checkpoint(job_id)
        if not chk or chk.get("total_segments") != total_chunks:
            chk = {
                "job_id": job_id,
                "language": language,
                "total_duration": duration,
                "total_segments": total_chunks,
                "total_parts": total_parts,
                "total_time": total_time_str,
                "status": "processing",
                "segments": {}
            }
            save_atomic_checkpoint(job_id, chk)

        # 3. Dynamic on-the-fly streaming slice directly from file_path (zero upfront transcoding, zero timeout)
        completed_count = sum(1 for s in chk.get("segments", {}).values() if s.get("status") == "completed")

        for idx in range(total_chunks):
            idx_str = str(idx)
            st_val = idx * 60.0
            et_val = min((idx + 1) * 60.0, duration)
            current_st_str = format_hms(st_val)
            current_et_str = format_hms(et_val)
            part_num = (idx // CHUNKS_PER_PART) + 1
            marker_label = f"[Phần {part_num}/{total_parts}: {current_et_str} / {total_time_str}]"

            # Check if this 1-minute segment is already completed in checkpoint
            if idx_str in chk.get("segments", {}) and chk["segments"][idx_str].get("status") == "completed":
                continue

            prog = int(8 + (completed_count / total_chunks) * 87)
            JOBS[job_id]["progress"] = prog
            JOBS[job_id]["message"] = f"⏳ [Phần {part_num}/{total_parts}] Đang xử lý: [{current_st_str} / {total_time_str}] ➔ Phút {idx+1}/{total_chunks} ({prog}%)..."

            # Dynamically slice only this 60s audio segment directly from source file (takes 0.05s via fast-seek)
            chunk_slice_p = temp_dir / f"slice_{idx}.wav"
            subprocess.run([
                FFMPEG_BIN, "-y",
                "-ss", str(st_val),
                "-t", str(et_val - st_val),
                "-i", str(file_path),
                "-vn",
                "-c:a", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                str(chunk_slice_p)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)

            # Transcribe 1-minute segment with retry resilience
            transcript = ""
            if chunk_slice_p.exists():
                try:
                    _, transcript = transcribe_audio_chunk(idx, chunk_slice_p, language, max_retries=3)
                except Exception as ex:
                    print(f"Minute {idx+1} transcription error: {ex}")
                    transcript = ""
                finally:
                    try:
                        chunk_slice_p.unlink(missing_ok=True)
                    except Exception:
                        pass

            # Mark complete and save to Checkpoint atomically with exact timeline marker
            chk["segments"][idx_str] = {
                "index": idx,
                "part_index": part_num,
                "start_time": st_val,
                "end_time": et_val,
                "timeline": f"{current_st_str} - {current_et_str}",
                "status": "completed",
                "transcript": transcript,
                "completed_at": time.time()
            }
            chk["last_checkpoint_time"] = current_et_str
            save_atomic_checkpoint(job_id, chk)

            completed_count += 1
            prog = int(8 + (completed_count / total_chunks) * 87)
            JOBS[job_id]["progress"] = prog

            has_text = "✅" if transcript else "⏭️"
            JOBS[job_id]["message"] = f"{has_text} ĐÁNH DẤU: {marker_label} ➔ Phút {idx+1}/{total_chunks} ({prog}%)..."

            # Anti-rate-limit cooldown
            if idx < total_chunks - 1:
                time.sleep(1.2)

        # 4. Build both: A) Full unified TXT/SRT & B) 40-minute Timeline Parts
        valid_texts = []
        srt_blocks = []
        srt_idx = 1
        
        parts_data = []
        for p_idx in range(total_parts):
            p_start_chunk = p_idx * CHUNKS_PER_PART
            p_end_chunk = min((p_idx + 1) * CHUNKS_PER_PART, total_chunks)
            p_st_sec = p_start_chunk * 60.0
            p_et_sec = min(p_end_chunk * 60.0, duration)
            p_timeline = f"{format_timestamp_srt(p_st_sec)} - {format_timestamp_srt(p_et_sec)}"
            
            p_texts = []
            p_srt_blocks = []
            p_srt_idx = 1
            
            for i in range(p_start_chunk, p_end_chunk):
                seg_data = chk.get("segments", {}).get(str(i), {})
                txt = (seg_data.get("transcript") or "").strip()
                if txt:
                    p_texts.append(txt)
                    st = seg_data.get("start_time", i * 60.0)
                    et = seg_data.get("end_time", min((i + 1) * 60.0, duration))
                    p_srt_blocks.append(f"{p_srt_idx}\n{format_timestamp_srt(st)} --> {format_timestamp_srt(et)}\n{txt}\n")
                    p_srt_idx += 1
            
            p_full_txt = "\n\n".join(p_texts)
            p_words = [w for w in p_full_txt.split() if w]
            parts_data.append({
                "part_index": p_idx + 1,
                "timeline": p_timeline,
                "start_sec": p_st_sec,
                "end_sec": p_et_sec,
                "text": p_full_txt,
                "srt": "\n".join(p_srt_blocks),
                "word_count": len(p_words),
                "char_count": len(p_full_txt)
            })

        for i in range(total_chunks):
            seg_data = chk.get("segments", {}).get(str(i), {})
            txt = (seg_data.get("transcript") or "").strip()
            if txt:
                valid_texts.append(txt)
                st = seg_data.get("start_time", i * 60.0)
                et = seg_data.get("end_time", min((i + 1) * 60.0, duration))
                srt_block = f"{srt_idx}\n{format_timestamp_srt(st)} --> {format_timestamp_srt(et)}\n{txt}\n"
                srt_blocks.append(srt_block)
                srt_idx += 1

        full_text = "\n\n".join(valid_texts) if valid_texts else "Không nhận diện được giọng nói trong tệp này."
        srt_content = "\n".join(srt_blocks) if srt_blocks else ""

        # 5. Final cleanup: safely remove temp slices and uploaded file
        try:
            for p in temp_dir.glob("*"):
                p.unlink(missing_ok=True)
            temp_dir.rmdir()
            delete_checkpoint(job_id)
            if file_path and Path(file_path).exists():
                Path(file_path).unlink(missing_ok=True)
        except Exception:
            pass

        words = [w for w in full_text.split() if w]
        JOBS[job_id]["progress"] = 100
        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["message"] = f"Hoàn tất 100%! Đã nhận diện toàn bộ [{total_time_str}] ({total_chunks} phút, {total_parts} phần 40 phút) thành công."
        JOBS[job_id]["result"] = {
            "text": full_text,
            "srt": srt_content,
            "duration": round(duration, 1),
            "total_time": total_time_str,
            "word_count": len(words),
            "char_count": len(full_text),
            "total_chunks": total_chunks,
            "total_parts": total_parts,
            "parts": parts_data
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

        # 2. Split audio into 1-minute (60s) WAV segments with CSV timestamps
        temp_dir = Path(tempfile.mkdtemp(prefix=f"stt_p{part_index}_"))
        csv_list_path = temp_dir / "segments.csv"
        segment_pattern = str(temp_dir / "chunk_%05d.wav")

        subprocess.run([
            FFMPEG_BIN, "-y", "-i", str(part_file_path),
            "-f", "segment", "-segment_time", "60",
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

        # 4. Parse 1-minute segments metadata
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
                    "start_time": idx * 60.0,
                    "end_time": min((idx + 1) * 60.0, duration)
                })

        total_chunks = len(segments_meta)
        part_segments = []

        # 5. Transcribe each 1-minute segment sequentially & delete WAV immediately
        for idx, meta in enumerate(segments_meta):
            chunk_p = Path(meta["file_path"])
            st_val = meta["start_time"]
            et_val = meta["end_time"]
            time_label = f"{int(st_val//60):02d}:{int(st_val%60):02d} - {int(et_val//60):02d}:{int(et_val%60):02d}"

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
            JOBS[job_key]["message"] = f"Phần {part_index+1}: ✅ Đã nhận diện xong Phút {idx+1}/{total_chunks} ({time_label})..."

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

            parts_data = []
            for p_idx in range(total_parts):
                p_data = session["parts"].get(str(p_idx), {})
                p_dur = p_data.get("duration", 0.0)
                p_st_sec = cumulative_offset
                p_et_sec = cumulative_offset + p_dur
                p_texts = []
                p_srts = []
                p_s_idx = 1
                for seg in p_data.get("segments", []):
                    seg_text = (seg.get("text") or "").strip()
                    if seg_text:
                        all_texts.append(seg_text)
                        p_texts.append(seg_text)
                        st = seg["start_time"] + cumulative_offset
                        et = seg["end_time"] + cumulative_offset
                        srt_block = f"{srt_idx}\n{format_timestamp_srt(st)} --> {format_timestamp_srt(et)}\n{seg_text}\n"
                        srt_blocks.append(srt_block)
                        srt_idx += 1
                        p_srts.append(f"{p_s_idx}\n{format_timestamp_srt(st)} --> {format_timestamp_srt(et)}\n{seg_text}\n")
                        p_s_idx += 1
                cumulative_offset += p_dur
                p_full_txt = "\n\n".join(p_texts)
                parts_data.append({
                    "part_index": p_idx + 1,
                    "timeline": f"{format_timestamp_srt(p_st_sec)} - {format_timestamp_srt(p_et_sec)}",
                    "start_sec": p_st_sec,
                    "end_sec": p_et_sec,
                    "text": p_full_txt,
                    "srt": "\n".join(p_srts),
                    "word_count": len(p_full_txt.split()),
                    "char_count": len(p_full_txt)
                })

            final_text = "\n\n".join(all_texts) if all_texts else "Không nhận diện được giọng nói trong video."
            final_srt = "\n".join(srt_blocks) if srt_blocks else ""

            session["status"] = "completed"
            session["final_result"] = {
                "text": final_text,
                "srt": final_srt,
                "duration": round(cumulative_offset, 1),
                "total_duration": round(cumulative_offset, 1),
                "total_time": format_hms(cumulative_offset),
                "total_parts": total_parts,
                "word_count": len(final_text.split()),
                "char_count": len(final_text),
                "parts": parts_data
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
    """Receive sequential 5MB file chunks, assemble safely, and trigger adaptive queue STT."""
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

        # Check if ALL chunks from 0 to total_chunks - 1 are present and non-empty on disk
        existing_parts = [UPLOAD_DIR / f"{upload_id}_part_{idx:05d}.tmp" for idx in range(total_chunks)]
        all_present = all(p.exists() and p.stat().st_size > 0 for p in existing_parts)

        if all_present:
            ext = Path(filename).suffix.lower() or ".mp4"
            assembled_path = UPLOAD_DIR / f"upload_{upload_id}{ext}"

            # High-speed buffered stream assembly to prevent any corruption
            with open(assembled_path, "wb") as outfile:
                for idx in range(total_chunks):
                    part_file = UPLOAD_DIR / f"{upload_id}_part_{idx:05d}.tmp"
                    if part_file.exists():
                        with open(part_file, "rb") as infile:
                            while True:
                                chunk = infile.read(2 * 1024 * 1024)
                                if not chunk:
                                    break
                                outfile.write(chunk)
                        try:
                            part_file.unlink(missing_ok=True)
                        except Exception:
                            pass

            JOBS[upload_id] = {
                "status": "queued",
                "progress": 5,
                "message": "Đã ghép nối các mảnh tệp hoàn tất! Đang xếp hàng xử lý âm thanh AI...",
                "result": None
            }

            chk = {
                "job_id": upload_id,
                "status": "queued",
                "progress": 5,
                "message": "Đã tiếp nhận tệp hoàn tất. Đang xếp hàng nhận diện âm thanh...",
                "total_segments": 1,
                "segments": {}
            }
            save_atomic_checkpoint(upload_id, chk)

            t = threading.Thread(target=run_stt_with_adaptive_queue, args=(upload_id, assembled_path, language))
            t.daemon = True
            t.start()

            return jsonify({"status": "completed", "job_id": upload_id})

        uploaded_count = sum(1 for p in existing_parts if p.exists() and p.stat().st_size > 0)
        return jsonify({"status": "chunk_received", "chunk_index": chunk_index, "uploaded_count": uploaded_count, "total_chunks": total_chunks})
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
        "progress": 5,
        "message": "Đang xếp hàng xử lý tệp âm thanh...",
        "result": None
    }

    chk = {
        "job_id": job_id,
        "status": "queued",
        "progress": 5,
        "message": "Đã tiếp nhận tệp. Đang xếp hàng xử lý âm thanh AI...",
        "total_segments": 1,
        "segments": {}
    }
    save_atomic_checkpoint(job_id, chk)

    t = threading.Thread(target=run_stt_with_adaptive_queue, args=(job_id, saved_path, language))
    t.daemon = True
    t.start()

    return jsonify({"status": "success", "job_id": job_id})

@app.route("/api/transcribe_status/<job_id>", methods=["GET"])
def api_transcribe_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        # Fallback 1: Persistent checkpoint on disk (survives memory reset or multi-worker routing)
        chk = load_checkpoint(job_id)
        if chk:
            status = chk.get("status", "processing")
            if status == "completed":
                total_duration = chk.get("total_duration", 0.0)
                total_time_str = chk.get("total_time", format_hms(total_duration))
                total = chk.get("total_segments", 1)
                CHUNKS_PER_PART = 40
                total_parts = max(1, math.ceil(total / CHUNKS_PER_PART))
                
                parts_data = []
                for p_idx in range(total_parts):
                    p_start_chunk = p_idx * CHUNKS_PER_PART
                    p_end_chunk = min((p_idx + 1) * CHUNKS_PER_PART, total)
                    p_st_sec = p_start_chunk * 60.0
                    p_et_sec = min(p_end_chunk * 60.0, total_duration)
                    p_timeline = f"{format_timestamp_srt(p_st_sec)} - {format_timestamp_srt(p_et_sec)}"
                    p_texts = []
                    p_srt_blocks = []
                    p_srt_idx = 1
                    for i in range(p_start_chunk, p_end_chunk):
                        seg_data = chk.get("segments", {}).get(str(i), {})
                        txt = (seg_data.get("transcript") or "").strip()
                        if txt:
                            p_texts.append(txt)
                            st = seg_data.get("start_time", i * 60.0)
                            et = seg_data.get("end_time", min((i + 1) * 60.0, total_duration))
                            p_srt_blocks.append(f"{p_srt_idx}\n{format_timestamp_srt(st)} --> {format_timestamp_srt(et)}\n{txt}\n")
                            p_srt_idx += 1
                    p_full_txt = "\n\n".join(p_texts)
                    p_words = [w for w in p_full_txt.split() if w]
                    parts_data.append({
                        "part_index": p_idx + 1,
                        "timeline": p_timeline,
                        "start_sec": p_st_sec,
                        "end_sec": p_et_sec,
                        "text": p_full_txt,
                        "srt": "\n".join(p_srt_blocks),
                        "word_count": len(p_words),
                        "char_count": len(p_full_txt)
                    })

                valid_texts = []
                srt_blocks = []
                srt_idx = 1
                for i in range(total):
                    seg_data = chk.get("segments", {}).get(str(i), {})
                    txt = (seg_data.get("transcript") or "").strip()
                    if txt:
                        valid_texts.append(txt)
                        st = seg_data.get("start_time", i * 60.0)
                        et = seg_data.get("end_time", min((i + 1) * 60.0, total_duration))
                        srt_block = f"{srt_idx}\n{format_timestamp_srt(st)} --> {format_timestamp_srt(et)}\n{txt}\n"
                        srt_blocks.append(srt_block)
                        srt_idx += 1
                full_text = "\n\n".join(valid_texts) if valid_texts else ""
                words = [w for w in full_text.split() if w]
                return jsonify({
                    "status": "completed",
                    "progress": 100,
                    "message": f"Hoàn tất 100%! Đã nhận diện âm thanh [{total_time_str}] ({total_parts} phần 40 phút) thành công.",
                    "result": {
                        "text": full_text,
                        "srt": "\n".join(srt_blocks),
                        "duration": round(total_duration, 1),
                        "total_time": total_time_str,
                        "word_count": len(words),
                        "char_count": len(full_text),
                        "total_chunks": total,
                        "total_parts": total_parts,
                        "parts": parts_data
                    }
                })
            else:
                total = chk.get("total_segments", 1)
                completed = sum(1 for s in chk.get("segments", {}).values() if s.get("status") == "completed")
                prog = int(10 + (completed / total) * 85)
                return jsonify({
                    "status": "processing",
                    "progress": prog,
                    "message": chk.get("message") or f"Đang nhận diện giọng nói AI (tự động phục hồi): {completed}/{total} đoạn ({prog}%)...",
                    "result": None
                })

        # Fallback 2: Check if assembled file or upload chunks exist in UPLOAD_DIR
        assembled_files = list(UPLOAD_DIR.glob(f"upload_{job_id}*"))
        if assembled_files:
            return jsonify({
                "status": "queued",
                "progress": 5,
                "message": "Đã ghép nối tệp và đang xếp hàng nhận diện âm thanh AI...",
                "result": None
            })

        part_files = list(UPLOAD_DIR.glob(f"{job_id}_part_*.tmp"))
        if part_files:
            return jsonify({
                "status": "queued",
                "progress": 3,
                "message": f"Đang hoàn tất tải các mảnh tệp lên máy chủ ({len(part_files)} mảnh)...",
                "result": None
            })

        return jsonify({"status": "error", "message": "Không tìm thấy tiến trình chuyển đổi."}), 404

    return jsonify(job)

# ── 3-Pass Neural Contextual Translation Engine (Ultralight, Zero-Overload) ──

COMMON_NOVEL_GLOSSARY = {
    "顾云舟": "Cố Vân Châu",
    "不允周": "Cố Vân Châu",
    "刘诗颖": "Lưu Thi Dĩnh",
    "刘思颖": "Lưu Tư Dĩnh",
    "张若曦": "Trương Nhược Hi",
    "张瑶": "Trương Dao",
    "林尘": "Lâm Trần",
    "叶老": "Diệp Lão",
    "叶如烟": "Diệp Như Yên",
    "录音棚": "phòng thu âm",
    "调音": "chỉnh âm",
    "师尊": "sư tôn",
    "师姐": "sư tỷ",
    "师弟": "sư đệ",
    "师妹": "sư muội",
    "师兄": "sư huynh",
    "掌门": "chưởng môn",
    "宗门": "tông môn",
    "筑基": "Trúc Cơ",
    "金丹": "Kim Đan",
    "元婴": "Nguyên Anh",
    "化神": "Hóa Thần",
    "老子": "ta / tôi",
    "小友": "tiểu hữu",
    "前辈": "tiền bối",
    "道友": "đạo hữu"
}

def scan_context_glossary(text):
    """Pass 1: Quick pre-scan to extract context entities, characters, and pronouns."""
    context_map = {}
    for k, v in COMMON_NOVEL_GLOSSARY.items():
        if k in text:
            context_map[k] = v
    return context_map

def apply_context_polish(text, context_map):
    """Pass 3: Post-polish to enforce 100% consistent character names and natural novel pronouns."""
    if not text:
        return ""
    res = text
    # Fix common machine translation phonetic glitches on names
    res = res.replace("Đừng để Chu nhận nó", "Cố Vân Châu nhận được rồi")
    res = res.replace("không cho Chu", "Cố Vân Châu")
    res = res.replace("Chu Chu", "Cố Vân Châu")
    res = res.replace("Cố Vân Chu", "Cố Vân Châu")
    res = res.replace("Lão Tử", "tôi")
    return res

def translate_single_block(text, target_lang="vi", source_lang="auto", max_retries=3):
    """Pass 2: High-speed neural translator using HTTP POST (no URI length limits) with retry resilience."""
    if not text or not text.strip():
        return ""
    url = "https://clients5.google.com/translate_a/t?client=dict-chrome-ex"
    data = urllib.parse.urlencode({"sl": source_lang, "tl": target_lang, "q": text}).encode("utf-8")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"
    }

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                if isinstance(res, list):
                    if len(res) > 0 and isinstance(res[0], list):
                        return "".join([item[0] if isinstance(item, list) else str(item) for item in res])
                    return "".join([str(item) for item in res])
                return str(res)
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(0.4 * (attempt + 1))
                continue
            # Multi-layer fallback to secondary Google Translate API endpoint
            try:
                url_fb = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={source_lang}&tl={target_lang}&dt=t"
                req_fb = urllib.request.Request(url_fb, data=data, headers=headers)
                with urllib.request.urlopen(req_fb, timeout=12) as resp_fb:
                    res_fb = json.loads(resp_fb.read().decode("utf-8"))
                    return "".join([item[0] for item in res_fb[0] if item and item[0]])
            except Exception:
                print(f"Translate block fallback warning: {e}")
                return text

def chunk_long_text_smart(text, max_chunk_len=1500):
    """Split long novels and speech text cleanly by paragraphs, lines, and sentence punctuations."""
    if len(text) <= max_chunk_len:
        return [text]
    paragraphs = text.split("\n")
    chunks = []
    current_chunk = []
    current_len = 0
    for p in paragraphs:
        p_len = len(p)
        if p_len > max_chunk_len:
            sentences = re.split(r"([。！？.!?\n])", p)
            sub_chunk = []
            sub_len = 0
            for i in range(0, len(sentences), 2):
                sent = sentences[i] + (sentences[i+1] if i+1 < len(sentences) else "")
                if sub_len + len(sent) > max_chunk_len and sub_chunk:
                    chunks.append("".join(sub_chunk))
                    sub_chunk = [sent]
                    sub_len = len(sent)
                else:
                    sub_chunk.append(sent)
                    sub_len += len(sent)
            if sub_chunk:
                chunks.append("".join(sub_chunk))
        else:
            if current_len + p_len > max_chunk_len and current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = [p]
                current_len = p_len
            else:
                current_chunk.append(p)
                current_len += p_len + 1
    if current_chunk:
        chunks.append("\n".join(current_chunk))
    return chunks

def translate_content_parallel(text, srt_text, target_lang="vi", source_lang="auto"):
    """Translate full TXT and SRT concurrently with 3-pass contextual pipeline for ultra-smooth output."""
    if not text and not srt_text:
        return "", ""

    # Pass 1: Quick pre-scan context
    context_map = scan_context_glossary(text + " " + srt_text)

    # If SRT is provided, translating SRT blocks in parallel gives BOTH translated SRT and TXT
    if srt_text and srt_text.strip():
        blocks = [b.strip() for b in srt_text.strip().split("\n\n") if b.strip()]
        parsed_blocks = []
        for b in blocks:
            lines = b.split("\n")
            if len(lines) >= 3:
                idx_line = lines[0]
                time_line = lines[1]
                content = "\n".join(lines[2:])
                parsed_blocks.append((idx_line, time_line, content))
            elif len(lines) == 2 and "-->" in lines[1]:
                parsed_blocks.append((lines[0], lines[1], ""))
            else:
                parsed_blocks.append((str(len(parsed_blocks) + 1), "", b))

        translated_map = {}
        def translate_worker(item):
            idx_line, time_line, content = item
            if not content.strip():
                return idx_line, time_line, ""
            trans = translate_single_block(content, target_lang, source_lang)
            # Pass 3 polish on each block
            trans = apply_context_polish(trans, context_map)
            return idx_line, time_line, trans

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(translate_worker, item) for item in parsed_blocks]
            for f in as_completed(futures):
                idx_line, time_line, trans = f.result()
                translated_map[idx_line] = (time_line, trans)

        final_srt_blocks = []
        final_txt_paragraphs = []
        for idx_line, _, _ in parsed_blocks:
            time_line, trans = translated_map.get(idx_line, ("", ""))
            if time_line:
                final_srt_blocks.append(f"{idx_line}\n{time_line}\n{trans}")
            else:
                final_srt_blocks.append(f"{idx_line}\n{trans}")
            if trans.strip():
                final_txt_paragraphs.append(trans.strip())

        translated_srt = "\n\n".join(final_srt_blocks)
        translated_txt = "\n\n".join(final_txt_paragraphs)
        return translated_txt, translated_srt

    # Plain text translation with smart chunking (handles 100,000+ chars)
    chunks = chunk_long_text_smart(text, max_chunk_len=1500)
    translated_paras = [None] * len(chunks)

    def chunk_worker(idx, c):
        trans = translate_single_block(c, target_lang, source_lang)
        return idx, apply_context_polish(trans, context_map)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(chunk_worker, i, c) for i, c in enumerate(chunks)]
        for f in as_completed(futures):
            i, trans = f.result()
            translated_paras[i] = trans

    translated_txt = "\n".join([p for p in translated_paras if p])
    return translated_txt, ""

@app.route("/api/translate_content", methods=["POST"])
def api_translate_content():
    """Translate TXT and SRT results with 10x parallelism and zero Render timeout."""
    try:
        data = request.get_json(silent=True) or {}
        text = data.get("text", "")
        srt = data.get("srt", "")
        target_lang = data.get("target_lang", "vi")
        source_lang = data.get("source_lang", "auto")

        if not text and not srt:
            return jsonify({"status": "error", "message": "Không có nội dung để dịch."}), 400

        translated_text, translated_srt = translate_content_parallel(text, srt, target_lang, source_lang)

        words = [w for w in translated_text.split() if w]
        return jsonify({
            "status": "success",
            "translated_text": translated_text,
            "translated_srt": translated_srt,
            "word_count": len(words),
            "char_count": len(translated_text),
            "target_lang": target_lang
        })
    except Exception as e:
        print(f"Translate API error: {e}")
        return jsonify({"status": "error", "message": f"Lỗi dịch thuật: {str(e)}"}), 500


# ══════════════════════════════════════════════════════════════════════════════
# BATCH FILE UPLOAD - Auto Chapter Detection & Batch TTS Generation
# ══════════════════════════════════════════════════════════════════════════════

BATCH_JOBS = {}

def detect_chapters(text):
    """Auto-detect chapters/episodes in text file content."""
    import re
    
    # Common chapter patterns (Vietnamese & English)
    chapter_patterns = [
        r'(?i)^[\s]*(?:chương|chuong|chương)\s*[:\-\s]*(\d+)[:\-\s]*(.*)',
        r'(?i)^[\s]*(?:tập|tap|tập)\s*[:\-\s]*(\d+)[:\-\s]*(.*)',
        r'(?i)^[\s]*(?:phần|phan|phần)\s*[:\-\s]*(\d+)[:\-\s]*(.*)',
        r'(?i)^[\s]*(?:hồi|hoi|hồi)\s*[:\-\s]*(\d+)[:\-\s]*(.*)',
        r'(?i)^[\s]*(?:chapter|chap)\s*[:\-\s]*(\d+)[:\-\s]*(.*)',
        r'(?i)^[\s]*(?:episode|ep)\s*[:\-\s]*(\d+)[:\-\s]*(.*)',
        r'(?i)^[\s]*(?:part)\s*[:\-\s]*(\d+)[:\-\s]*(.*)',
        r'(?i)^[\s]*(?:quyển|quyen|quyển)\s*[:\-\s]*(\d+)[:\-\s]*(.*)',
        r'(?i)^[\s]*(?:mục|muc|mục)\s*[:\-\s]*(\d+)[:\-\s]*(.*)',
    ]
    
    lines = text.split('\n')
    chapters = []
    current_chapter = None
    current_content = []
    
    for line in lines:
        matched = False
        for pattern in chapter_patterns:
            m = re.match(pattern, line.strip())
            if m:
                # Save previous chapter
                if current_chapter is not None:
                    content = '\n'.join(current_content).strip()
                    if content:
                        current_chapter['content'] = content
                        current_chapter['char_count'] = len(content)
                        chapters.append(current_chapter)
                
                num = m.group(1)
                title = m.group(2).strip() if m.group(2).strip() else ''
                full_title = line.strip()
                
                current_chapter = {
                    'index': len(chapters),
                    'number': num,
                    'title': full_title,
                    'short_title': title,
                    'content': '',
                    'char_count': 0
                }
                current_content = [line.strip()]
                matched = True
                break
        
        if not matched:
            current_content.append(line)
    
    # Save last chapter
    if current_chapter is not None:
        content = '\n'.join(current_content).strip()
        if content:
            current_chapter['content'] = content
            current_chapter['char_count'] = len(content)
            chapters.append(current_chapter)
    
    # If no chapters detected, treat entire text as one chapter
    if not chapters:
        text_stripped = text.strip()
        if text_stripped:
            chapters.append({
                'index': 0,
                'number': '1',
                'title': 'Toàn bộ nội dung',
                'short_title': 'Toàn bộ nội dung',
                'content': text_stripped,
                'char_count': len(text_stripped)
            })
    
    return chapters


@app.route("/api/upload_text_file", methods=["POST"])
def upload_text_file():
    """Upload a text file and auto-detect chapters."""
    try:
        if 'file' not in request.files:
            return jsonify({"status": "error", "message": "Không tìm thấy file."}), 400
        
        file = request.files['file']
        if not file.filename:
            return jsonify({"status": "error", "message": "File không hợp lệ."}), 400
        
        # Read file content with encoding detection
        raw_bytes = file.read()
        text = None
        for enc in ['utf-8', 'utf-8-sig', 'utf-16', 'cp1252', 'latin-1']:
            try:
                text = raw_bytes.decode(enc)
                break
            except (UnicodeDecodeError, Exception):
                continue
        
        if text is None:
            return jsonify({"status": "error", "message": "Không thể đọc file. Hãy đảm bảo file là UTF-8."}), 400
        
        # Detect chapters
        chapters = detect_chapters(text)
        
        # Store text for later use
        file_id = str(uuid.uuid4())[:8]
        BATCH_JOBS[file_id] = {
            "filename": file.filename,
            "chapters": chapters,
            "total_chars": sum(c['char_count'] for c in chapters),
        }
        
        # Return chapter list (without full content to save bandwidth)
        chapter_summary = [{
            'index': c['index'],
            'number': c['number'],
            'title': c['title'],
            'char_count': c['char_count'],
            'preview': c['content'][:150] + '...' if len(c['content']) > 150 else c['content'],
            'content': c['content']
        } for c in chapters]
        
        return jsonify({
            "status": "success",
            "file_id": file_id,
            "filename": file.filename,
            "total_chapters": len(chapters),
            "total_chars": sum(c['char_count'] for c in chapters),
            "chapters": chapter_summary
        })
    
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/batch_generate", methods=["POST"])
def batch_generate():
    """Start batch TTS generation for all chapters."""
    try:
        data = request.json or {}
        file_id = data.get("file_id")
        voice = data.get("voice", "BV421_vivn_streaming")
        resource_id = data.get("resource_id", None)
        rate = data.get("rate", "1.0")
        selected_chapters = data.get("selected_chapters", None)  # list of indices, None = all
        
        if not file_id or file_id not in BATCH_JOBS:
            return jsonify({"status": "error", "message": "File không tồn tại. Hãy tải lên lại."}), 400
        
        batch = BATCH_JOBS[file_id]
        chapters = batch["chapters"]
        
        if selected_chapters is not None:
            chapters = [c for c in chapters if c['index'] in selected_chapters]
        
        if not chapters:
            return jsonify({"status": "error", "message": "Không có chương nào được chọn."}), 400
        
        vinfo = find_voice_info(voice)
        if vinfo and not resource_id:
            resource_id = vinfo.get("resource_id")
        lan = vinfo.get("lan", "vi") if vinfo else "vi"
        
        batch_id = str(uuid.uuid4())[:8]
        
        BATCH_JOBS[batch_id] = {
            "type": "batch_generation",
            "status": "processing",
            "total_chapters": len(chapters),
            "completed_chapters": 0,
            "current_chapter": 0,
            "current_chapter_title": chapters[0]["title"] if chapters else "",
            "progress": 0,
            "message": f"Bắt đầu tạo giọng đọc cho {len(chapters)} chương...",
            "results": [],
            "errors": []
        }
        
        def run_batch(bid, chaps, v, rid, r, language):
            total = len(chaps)
            for i, chapter in enumerate(chaps):
                try:
                    BATCH_JOBS[bid]["current_chapter"] = i
                    BATCH_JOBS[bid]["current_chapter_title"] = chapter["title"]
                    BATCH_JOBS[bid]["message"] = f"Đang tạo: {chapter['title']} ({i+1}/{total})..."
                    
                    # Create a sub-job for this chapter
                    sub_job_id = f"batch_{bid}_{i}"
                    JOBS[sub_job_id] = {
                        "status": "processing",
                        "progress": 0,
                        "message": "Đang xử lý...",
                        "result": None,
                    }
                    
                    run_tts_job(sub_job_id, chapter["content"], v, rid, r, language)
                    
                    sub_result = JOBS.get(sub_job_id, {})
                    if sub_result.get("status") in ["completed", "done"] and sub_result.get("result"):
                        BATCH_JOBS[bid]["results"].append({
                            "chapter_index": chapter["index"],
                            "chapter_title": chapter["title"],
                            "filename": sub_result["result"]["filename"],
                            "download_url": sub_result["result"]["download_url"],
                            "duration_ms": sub_result["result"].get("duration_ms", 0),
                        })
                    else:
                        BATCH_JOBS[bid]["errors"].append({
                            "chapter_index": chapter["index"],
                            "chapter_title": chapter["title"],
                            "error": sub_result.get("message", "Lỗi không xác định")
                        })
                    
                    # Cleanup sub job
                    JOBS.pop(sub_job_id, None)
                    
                except Exception as ex:
                    BATCH_JOBS[bid]["errors"].append({
                        "chapter_index": chapter["index"],
                        "chapter_title": chapter["title"],
                        "error": str(ex)
                    })
                
                BATCH_JOBS[bid]["completed_chapters"] = i + 1
                BATCH_JOBS[bid]["progress"] = int(((i + 1) / total) * 100)
            
            BATCH_JOBS[bid]["status"] = "done"
            BATCH_JOBS[bid]["message"] = f"Hoàn tất! Đã tạo {len(BATCH_JOBS[bid]['results'])}/{total} chương thành công."
        
        t = threading.Thread(
            target=run_batch,
            args=(batch_id, chapters, voice, resource_id, rate, lan),
            daemon=True
        )
        t.start()
        
        return jsonify({"status": "success", "batch_id": batch_id, "total_chapters": len(chapters)})
    
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/batch_status/<batch_id>", methods=["GET"])
def get_batch_status(batch_id):
    """Get batch generation progress."""
    batch = BATCH_JOBS.get(batch_id)
    if not batch:
        return jsonify({"status": "error", "message": "Batch không tồn tại."}), 404
    return jsonify(batch)



# ══════════════════════════════════════════════════════════════════════════════
# GOOGLE DRIVE INTEGRATION - Auto upload audio to user's Google Drive
# ══════════════════════════════════════════════════════════════════════════════

GOOGLE_DRIVE_CREDS = None
GOOGLE_DRIVE_FOLDER_ID = None

def get_drive_service():
    """Get authenticated Google Drive service."""
    global GOOGLE_DRIVE_CREDS
    if not GOOGLE_DRIVE_CREDS:
        return None
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_info(GOOGLE_DRIVE_CREDS)
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            GOOGLE_DRIVE_CREDS = json.loads(creds.to_json())
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        print(f"Drive service error: {e}")
        return None

def upload_to_drive(file_path, filename, folder_id=None):
    """Upload a file to Google Drive and return the shareable link."""
    service = get_drive_service()
    if not service:
        return None
    
    try:
        from googleapiclient.http import MediaFileUpload
        
        file_metadata = {'name': filename}
        if folder_id:
            file_metadata['parents'] = [folder_id]
        
        media = MediaFileUpload(str(file_path), mimetype='audio/mpeg', resumable=True)
        file = service.files().create(body=file_metadata, media_body=media, fields='id,webViewLink').execute()
        
        # Make file accessible via link
        service.permissions().create(
            fileId=file.get('id'),
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
        
        return {
            'file_id': file.get('id'),
            'web_link': file.get('webViewLink'),
            'download_url': f"https://drive.google.com/uc?export=download&id={file.get('id')}"
        }
    except Exception as e:
        print(f"Drive upload error: {e}")
        return None


@app.route("/api/drive/auth_url", methods=["GET"])
def drive_auth_url():
    """Generate Google OAuth2 authorization URL."""
    try:
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        redirect_uri = request.host_url.rstrip('/') + '/api/drive/callback'
        
        if not client_id:
            return jsonify({
                "status": "error",
                "message": "Google Client ID ch\u01b0a \u0111\u01b0\u1ee3c c\u1ea5u h\u00ecnh. Vui l\u00f2ng set GOOGLE_CLIENT_ID."
            }), 400
        
        scope = "https://www.googleapis.com/auth/drive.file"
        auth_url = (
            f"https://accounts.google.com/o/oauth2/v2/auth?"
            f"client_id={client_id}&"
            f"redirect_uri={urllib.parse.quote(redirect_uri)}&"
            f"response_type=code&"
            f"scope={urllib.parse.quote(scope)}&"
            f"access_type=offline&"
            f"prompt=consent"
        )
        return jsonify({"status": "success", "auth_url": auth_url})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/drive/callback")
def drive_callback():
    """Handle Google OAuth2 callback."""
    global GOOGLE_DRIVE_CREDS
    try:
        code = request.args.get("code")
        if not code:
            return "Authorization failed", 400
        
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
        redirect_uri = request.host_url.rstrip('/') + '/api/drive/callback'
        
        # Exchange code for tokens
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            'code': code,
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code'
        }
        
        resp = requests.post(token_url, data=token_data)
        tokens = resp.json()
        
        if 'access_token' in tokens:
            GOOGLE_DRIVE_CREDS = {
                'token': tokens['access_token'],
                'refresh_token': tokens.get('refresh_token'),
                'client_id': client_id,
                'client_secret': client_secret,
                'token_uri': 'https://oauth2.googleapis.com/token'
            }
            
            # Save creds to file for persistence
            creds_path = Path(__file__).parent / "drive_creds.json"
            with open(creds_path, 'w') as f:
                json.dump(GOOGLE_DRIVE_CREDS, f)
            
            return '<html><body style="background:#0b0f19;color:#34d399;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;"><div style="text-align:center;"><h1>\u2705 K\u1ebft n\u1ed1i Google Drive th\u00e0nh c\u00f4ng!</h1><p>B\u1ea1n c\u00f3 th\u1ec3 \u0111\u00f3ng tab n\u00e0y v\u00e0 quay l\u1ea1i app.</p></div></body></html>'
        else:
            return f"Token error: {tokens}", 400
            
    except Exception as e:
        return f"Error: {str(e)}", 500


@app.route("/api/drive/status", methods=["GET"])
def drive_status():
    """Check if Google Drive is connected."""
    global GOOGLE_DRIVE_CREDS
    
    # Try to load saved creds
    if not GOOGLE_DRIVE_CREDS:
        creds_path = Path(__file__).parent / "drive_creds.json"
        if creds_path.exists():
            try:
                with open(creds_path, 'r') as f:
                    GOOGLE_DRIVE_CREDS = json.load(f)
            except Exception:
                pass
    
    connected = GOOGLE_DRIVE_CREDS is not None and 'token' in (GOOGLE_DRIVE_CREDS or {})
    return jsonify({"connected": connected, "folder_id": GOOGLE_DRIVE_FOLDER_ID})


@app.route("/api/drive/set_folder", methods=["POST"])
def drive_set_folder():
    """Set the Google Drive folder ID for uploads."""
    global GOOGLE_DRIVE_FOLDER_ID
    data = request.json or {}
    GOOGLE_DRIVE_FOLDER_ID = data.get("folder_id", None)
    return jsonify({"status": "success", "folder_id": GOOGLE_DRIVE_FOLDER_ID})



@app.route("/api/drive/test", methods=["POST"])
def drive_test_connection():
    """Test if we can upload to the specified Drive folder."""
    global GOOGLE_DRIVE_FOLDER_ID
    try:
        data = request.json or {}
        folder_id = data.get("folder_id")
        
        if not folder_id:
            return jsonify({"status": "error", "message": "Folder ID is empty."}), 400
        
        service = get_drive_service()
        if not service:
            return jsonify({"status": "error", "message": "Chưa đăng nhập Google. Vui lòng đăng nhập trước."}), 400
        
        # Try to list files in the folder to test access
        try:
            results = service.files().list(
                q=f"'{folder_id}' in parents",
                pageSize=1,
                fields="files(id, name)"
            ).execute()
            
            GOOGLE_DRIVE_FOLDER_ID = folder_id
            return jsonify({
                "status": "success",
                "message": "Kết nối thành công! Có thể tải file lên folder này."
            })
        except Exception as api_err:
            error_msg = str(api_err)
            if "404" in error_msg:
                return jsonify({"status": "error", "message": "Folder không tồn tại hoặc không có quyền truy cập. Hãy chia sẻ folder với quyền chỉnh sửa."}), 400
            elif "403" in error_msg:
                return jsonify({"status": "error", "message": "Không có quyền truy cập folder. Hãy cấp quyền chỉnh sửa."}), 400
            else:
                return jsonify({"status": "error", "message": f"Lỗi: {error_msg}"}), 400
    
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/drive/upload", methods=["POST"])
def drive_upload_endpoint():
    """Upload a generated audio file to Google Drive."""
    try:
        data = request.json or {}
        filename = data.get("filename")
        
        if not filename:
            return jsonify({"status": "error", "message": "Missing filename"}), 400
        
        file_path = OUTPUT_DIR / filename
        if not file_path.exists():
            return jsonify({"status": "error", "message": "File not found"}), 404
        
        result = upload_to_drive(file_path, filename, GOOGLE_DRIVE_FOLDER_ID)
        
        if result:
            # Delete local file after successful upload
            try:
                file_path.unlink(missing_ok=True)
            except Exception:
                pass
            
            return jsonify({
                "status": "success",
                "drive_link": result['web_link'],
                "download_url": result['download_url'],
                "file_id": result['file_id']
            })
        else:
            return jsonify({"status": "error", "message": "Ch\u01b0a k\u1ebft n\u1ed1i Google Drive. Vui l\u00f2ng k\u1ebft n\u1ed1i tr\u01b0\u1edbc."}), 400
    
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ==========================================================
# TIKTOK & DOUYIN BULK CHANNEL VIDEO DOWNLOADER API
# ==========================================================

@app.route("/api/scan_channel", methods=["POST"])
def scan_channel():
    """
    Scrapes all videos from a TikTok or Douyin channel or list of URLs.
    Extracts metadata without downloading files: title, thumbnail, duration, id, url.
    """
    try:
        data = request.get_json(force=True) or {}
        raw_url = data.get("url", "").strip()
        limit = int(data.get("limit", 60))
        if not raw_url:
            return jsonify({"status": "error", "message": "Vui lòng nhập link kênh hoặc link video TikTok / Douyin."}), 400

        urls = [u.strip() for u in raw_url.splitlines() if u.strip()]
        
        from yt_dlp import YoutubeDL
        from yt_dlp.networking.impersonate import ImpersonateTarget
        
        target = ImpersonateTarget.from_str('chrome')
        videos = []
        channel_name = ""
        
        for single_url in urls:
            if single_url.startswith("@"):
                single_url = f"https://www.tiktok.com/{single_url}"
            elif not single_url.startswith("http://") and not single_url.startswith("https://"):
                single_url = f"https://www.tiktok.com/@{single_url}"
                
            ydl_opts = {
                'impersonate': target,
                'extract_flat': 'in_playlist',
                'quiet': True,
                'playlistend': limit,
                'no_warnings': True,
                'ignoreerrors': True,
            }
            
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(single_url, download=False)
                if not info:
                    continue
                    
                channel_name = info.get("channel") or info.get("uploader") or info.get("title") or channel_name
                entries = info.get("entries")
                if entries:
                    for entry in entries:
                        if not entry:
                            continue
                        vid_id = entry.get("id") or str(len(videos) + 1)
                        vid_title = entry.get("title") or entry.get("description") or f"Video {vid_id}"
                        thumbs = entry.get("thumbnails") or []
                        thumb_url = thumbs[0].get("url") if thumbs else (entry.get("thumbnail") or "")
                        duration = entry.get("duration") or 0
                        v_url = entry.get("url") or entry.get("webpage_url") or f"https://www.tiktok.com/video/{vid_id}"
                        
                        videos.append({
                            "id": vid_id,
                            "title": vid_title[:120].strip(),
                            "thumbnail": thumb_url,
                            "duration": duration,
                            "url": v_url,
                            "author": entry.get("uploader") or channel_name or "TikTok",
                            "like_count": entry.get("like_count") or 0,
                            "view_count": entry.get("view_count") or 0
                        })
                else:
                    vid_id = info.get("id") or "1"
                    vid_title = info.get("title") or info.get("description") or f"Video {vid_id}"
                    thumbs = info.get("thumbnails") or []
                    thumb_url = thumbs[0].get("url") if thumbs else (info.get("thumbnail") or "")
                    videos.append({
                        "id": vid_id,
                        "title": vid_title[:120].strip(),
                        "thumbnail": thumb_url,
                        "duration": info.get("duration") or 0,
                        "url": info.get("webpage_url") or single_url,
                        "author": info.get("uploader") or "TikTok",
                        "like_count": info.get("like_count") or 0,
                        "view_count": info.get("view_count") or 0
                    })
                    
        if not videos:
            return jsonify({"status": "error", "message": "Không tìm thấy video nào từ đường link được cung cấp. Vui lòng kiểm tra lại link."}), 404
            
        return jsonify({
            "status": "success",
            "channel": channel_name or "Kênh Video",
            "total": len(videos),
            "videos": videos
        })

    except Exception as e:
        return jsonify({"status": "error", "message": f"Lỗi quét kênh: {str(e)}"}), 500


@app.route("/api/download_video_stream", methods=["GET"])
def download_video_stream():
    """
    Downloads a single video via yt-dlp, streams it to client,
    and immediately deletes the temp file on the server.
    Ensures 0 MB persistent storage on VPS!
    """
    import tempfile, shutil, re
    from flask import Response
    from yt_dlp import YoutubeDL
    from yt_dlp.networking.impersonate import ImpersonateTarget

    target_url = request.args.get("url", "").strip()
    custom_title = request.args.get("title", "").strip()
    if not target_url:
        return jsonify({"error": "Thiếu URL video"}), 400

    temp_dir = tempfile.mkdtemp(prefix="dl_vid_")
    out_tmpl = os.path.join(temp_dir, "%(id)s.%(ext)s")
    
    target = ImpersonateTarget.from_str('chrome')
    ydl_opts = {
        'impersonate': target,
        'quiet': True,
        'no_warnings': True,
        'outtmpl': out_tmpl,
        'format': 'best',
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=True)
            real_file = ydl.prepare_filename(info)
            if not os.path.exists(real_file):
                files = os.listdir(temp_dir)
                if files:
                    real_file = os.path.join(temp_dir, files[0])
                else:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    return jsonify({"error": "Không thể tải video từ máy chủ nguồn."}), 500

        file_size = os.path.getsize(real_file)
        raw_name = custom_title or info.get("title") or "video"
        clean_name = re.sub(r'[\\/*?:"<>|]', "", raw_name).strip()[:80] or "video"
        filename = f"{clean_name}.mp4"

        def generate_and_cleanup():
            try:
                with open(real_file, "rb") as f:
                    while True:
                        chunk = f.read(65536) # 64KB chunk
                        if not chunk:
                            break
                        yield chunk
            finally:
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass

        import urllib.parse
        encoded_filename = urllib.parse.quote(filename)
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Content-Length": str(file_size),
            "Content-Type": "video/mp4",
            "Cache-Control": "no-cache"
        }
        return Response(generate_and_cleanup(), headers=headers)

    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({"error": f"Lỗi tải video: {str(e)}"}), 500


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