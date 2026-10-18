import os
import sys
import time
import json
import argparse
from pathlib import Path
from importlib.resources import files

# Force UTF-8 encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ["PYTHONIOENCODING"] = "utf-8"

import re
import numpy as np
import torch
import soundfile as sf
import torchaudio

def _custom_torchaudio_load(filepath, *args, **kwargs):
    data, sr = sf.read(filepath)
    if data.ndim == 1:
        tensor = torch.from_numpy(data).unsqueeze(0).float()
    else:
        tensor = torch.from_numpy(data.T).float()
    return tensor, sr
torchaudio.load = _custom_torchaudio_load

from omegaconf import OmegaConf
from hydra.utils import get_class

from f5_tts.infer.utils_infer import (
    load_model,
    load_vocoder,
    infer_process,
    preprocess_ref_audio_text,
)

def split_into_prosody_phrases(text: str):
    """
    Phân tích và tách các cụm ý (prosody phrases) chuẩn xác:
    - BẢO VỆ TUYỆT ĐỐI các chuỗi kỹ thuật:
      + URL (http://..., https://...)
      + Thời gian (12:30, 08:00)
      + Tỉ lệ, tỷ số (3:2, 16:9, 10:20)
      + Số thập phân & phân cách hàng nghìn (2.0, 3.14, 1,500,000, 3,5)
      + Tên file & định dạng (file.mp3, model.safetensors)
      + Mã định danh / SKU (SKU:123, ID:99)
    - CHỈ TẠO KHOẢNG NGHỈ khi punctuation thực sự là ranh giới ngữ pháp/cú pháp:
      + Dấu phẩy (', '): 220ms
      + Dấu chấm phẩy ('; '): 320ms
      + Dấu hai chấm (': ') khi theo sau là khoảng trắng/xuống dòng: 320ms
      + Dấu chấm ('. '), ba chấm ('...'), xuống dòng ('\n'): 450-500ms
      + Dấu hỏi ('? '), than ('! '): 420ms
    """
    if not text:
        return []

    t = text.replace("…", "...").replace("\r\n", "\n").replace("\r", "\n")

    url_pattern = r'https?://[^\s,;!?()]+'
    urls = []
    def _save_url(m):
        urls.append(m.group(0))
        return f"__SAFE_URL_{len(urls)-1}__"
    t_masked = re.sub(url_pattern, _save_url, t)

    time_pattern = r'\b\d{1,2}:\d{2}\b'
    times = []
    def _save_time(m):
        times.append(m.group(0))
        return f"__SAFE_TIME_{len(times)-1}__"
    t_masked = re.sub(time_pattern, _save_time, t_masked)

    ratio_pattern = r'\b\d+:\d+\b'
    ratios = []
    def _save_ratio(m):
        ratios.append(m.group(0))
        return f"__SAFE_RATIO_{len(ratios)-1}__"
    t_masked = re.sub(ratio_pattern, _save_ratio, t_masked)

    sku_pattern = r'\b[a-zA-Z0-9_\-]+:[a-zA-Z0-9_\-]+\b'
    skus = []
    def _save_sku(m):
        skus.append(m.group(0))
        return f"__SAFE_SKU_{len(skus)-1}__"
    t_masked = re.sub(sku_pattern, _save_sku, t_masked)

    num_pattern = r'\b\d+[.,]\d+\b'
    nums = []
    def _save_num(m):
        nums.append(m.group(0))
        return f"__SAFE_NUM_{len(nums)-1}__"
    t_masked = re.sub(num_pattern, _save_num, t_masked)

    file_pattern = r'\b[a-zA-Z0-9_]+\.[a-zA-Z0-9]{2,4}\b'
    files_list = []
    def _save_file(m):
        files_list.append(m.group(0))
        return f"__SAFE_FILE_{len(files_list)-1}__"
    t_masked = re.sub(file_pattern, _save_file, t_masked)

    boundary_regex = r'(\.{3,}|\n+|[;!?]+|:(?:\s|$)|(?<=\S)\.(?:\s|$)|(?<=\S),(?:\s|$))'
    tokens = re.split(boundary_regex, t_masked)

    phrases = []
    i = 0
    while i < len(tokens):
        chunk = tokens[i].strip()
        punc = tokens[i+1].strip() if i + 1 < len(tokens) else ""
        i += 2

        if not chunk:
            continue

        if "\n" in punc:
            pause_ms = 300
        elif "?" in punc or "!" in punc:
            pause_ms = 250
        elif "..." in punc:
            pause_ms = 380
        elif "." in punc:
            pause_ms = 300
        elif ";" in punc or ":" in punc:
            pause_ms = 200
        elif "," in punc:
            pause_ms = 140
        else:
            pause_ms = 180

        for idx, u in enumerate(urls):
            chunk = chunk.replace(f"__SAFE_URL_{idx}__", u)
        for idx, tm in enumerate(times):
            chunk = chunk.replace(f"__SAFE_TIME_{idx}__", tm)
        for idx, r in enumerate(ratios):
            chunk = chunk.replace(f"__SAFE_RATIO_{idx}__", r)
        for idx, sk in enumerate(skus):
            chunk = chunk.replace(f"__SAFE_SKU_{idx}__", sk)
        for idx, n in enumerate(nums):
            chunk = chunk.replace(f"__SAFE_NUM_{idx}__", n)
        for idx, fl in enumerate(files_list):
            chunk = chunk.replace(f"__SAFE_FILE_{idx}__", fl)

        clean_punc = ""
        for ch in punc:
            if ch in ",;.!?":
                clean_punc += ch
        phrase_str = chunk + (clean_punc if clean_punc else ".")
        phrases.append((phrase_str, pause_ms))

def sanitize_ref_audio(ref_wav_path, max_internal_silence_ms=180):
    """
    Loại bỏ các khoảng lặng chết kéo dài (>180ms) bên trong audio mẫu đối chiếu
    để mô hình F5-TTS học đúng nhịp thở tự nhiên, linh hoạt của con người,
    tránh kéo dài lê thê các nguyên âm và giúp tốc độ đọc tiệm cận voice chuẩn.
    """
    try:
        data, sr = sf.read(ref_wav_path)
        if data.ndim > 1:
            data = np.mean(data, axis=1)

        frame_ms = 20
        frame_len = int(sr * (frame_ms / 1000.0))
        rms = [np.sqrt(np.mean(data[i : i + frame_len] ** 2)) for i in range(0, len(data) - frame_len, frame_len)]
        db = 20 * np.log10(np.array(rms) + 1e-7)
        is_silent = db < -32.0

        keep_mask = np.ones(len(data), dtype=bool)
        start = None
        max_silence_frames = int(max_internal_silence_ms / frame_ms)
        has_trimmed = False

        for i, s in enumerate(is_silent):
            if s and start is None:
                start = i
            elif not s and start is not None:
                silence_len = i - start
                if silence_len > max_silence_frames:
                    cut_start_sample = (start + max_silence_frames) * frame_len
                    cut_end_sample = i * frame_len
                    keep_mask[cut_start_sample:cut_end_sample] = False
                    has_trimmed = True
                start = None

        if has_trimmed:
            cleaned_data = data[keep_mask]
            clean_path = Path(ref_wav_path).parent / f"sanitized_{Path(ref_wav_path).name}"
            sf.write(str(clean_path), cleaned_data, sr)
            print(f"[F5-Runner] Sanitized ref audio: {len(data)/sr:.2f}s -> {len(cleaned_data)/sr:.2f}s (cut {len(data)/sr - len(cleaned_data)/sr:.2f}s excess dead pause)")
            return str(clean_path)
    except Exception as ex:
        print(f"[F5-Runner] Sanitize ref audio warning: {ex}")

    return ref_wav_path

def run_inference(
    ref_audio_path: str,
    ref_text: str,
    gen_text: str,
    output_path: str,
    ckpt_path: str = "",
    vocab_path: str = "",
    lang: str = "vi",
    nfe_step: int = 16,
    speed: float = 1.0,
):
    t0 = time.time()
    device = "cpu"

    # Configure language-specific checkpoints and vocabs
    vi_ckpt = r"test_ab_cloning\models\f5_vietnamese\model_latest.safetensors"
    vi_vocab = r"test_ab_cloning\models\f5_vietnamese\vocab.txt"
    base_ckpt = r"models\model_1250000.safetensors"
    base_vocab = str(files("f5_tts").joinpath("infer/examples/vocab.txt"))

    if lang == "vi" and os.path.exists(vi_ckpt) and os.path.exists(vi_vocab):
        final_ckpt = ckpt_path if ckpt_path and os.path.exists(ckpt_path) and "1250000" not in ckpt_path else vi_ckpt
        final_vocab = vocab_path if vocab_path and os.path.exists(vocab_path) else vi_vocab
        print(f"[F5-Runner] Using Vietnamese Neural Model: {final_ckpt} | Vocab: {final_vocab}")
    else:
        final_ckpt = ckpt_path if ckpt_path and os.path.exists(ckpt_path) else base_ckpt
        final_vocab = vocab_path if vocab_path and os.path.exists(vocab_path) else base_vocab
        print(f"[F5-Runner] Using Standard Multilingual Model ({lang}): {final_ckpt} | Vocab: {final_vocab}")

    print(f"[F5-Runner] Loading configuration and model...")
    cfg_file = str(files("f5_tts").joinpath("configs/F5TTS_v1_Base.yaml"))
    model_cfg = OmegaConf.load(cfg_file)
    model_cls = get_class(f"f5_tts.model.{model_cfg.model.backbone}")

    # Load model & vocoder
    ema_model = load_model(
        model_cls,
        model_cfg.model.arch,
        final_ckpt,
        mel_spec_type="vocos",
        vocab_file=final_vocab,
        device=device,
    )
    vocoder = load_vocoder(vocoder_name="vocos", is_local=False, device=device)

    # Sanitize and preprocess reference audio and text
    clean_ref_audio_path = sanitize_ref_audio(ref_audio_path)
    print(f"[F5-Runner] Preprocessing reference audio: {clean_ref_audio_path}")
    ref_audio_proc, ref_text_proc = preprocess_ref_audio_text(clean_ref_audio_path, ref_text)

    # Phân tích văn bản thành các cụm ý prosody phrases
    phrases = split_into_prosody_phrases(gen_text)
    if not phrases:
        phrases = [(gen_text, 350)]

    print(f"[F5-Runner] Generating speech for {len(phrases)} prosody phrases with natural cadence (nfe_step={nfe_step}, speed={speed})...", flush=True)
    collected_waves = []
    total_phrases = len(phrases)
    sr = 24000

    for idx, (p_text, p_pause_ms) in enumerate(phrases):
        prog_pct = 25 + int((idx / total_phrases) * 65)
        print(f"PROGRESS:{prog_pct}:Đang đọc câu {idx+1}/{total_phrases} (ngắt nghỉ tự nhiên)...", flush=True)
        print(f"[F5-Runner] Phrase {idx+1}/{total_phrases}: '{p_text}' (pause: {p_pause_ms}ms)", flush=True)

        wave_chunk, sr, _ = infer_process(
            ref_audio_proc,
            ref_text_proc,
            p_text,
            ema_model,
            vocoder,
            mel_spec_type="vocos",
            nfe_step=nfe_step,
            speed=speed,
            device=device,
        )

        if wave_chunk is not None and len(wave_chunk) > 0:
            collected_waves.append(wave_chunk)
            if idx < total_phrases - 1 and p_pause_ms > 0:
                silence_samples = int(sr * (p_pause_ms / 1000.0))
                silence = np.zeros(silence_samples, dtype=wave_chunk.dtype)
                collected_waves.append(silence)

    if not collected_waves:
        raise RuntimeError("No audio was generated from phrases.")

    final_wave = np.concatenate(collected_waves)


    output_p = Path(output_path)
    output_p.parent.mkdir(parents=True, exist_ok=True)

    # Save to temp wav then convert to target format (mp3 or wav)
    temp_wav = output_p.with_suffix(".temp.wav")
    
    # Post-processing Studio Mastering: Boost treble presence (>3.5kHz) & 44.1kHz upsample
    try:
        import numpy as np
        import librosa
        import scipy.signal as dsp_signal
        sos = dsp_signal.butter(2, 3500 / (sr / 2), btype='high', output='sos')
        h_band = dsp_signal.sosfilt(sos, final_wave)
        enhanced_wave = final_wave + 0.40 * h_band
        enhanced_wave = enhanced_wave / (np.max(np.abs(enhanced_wave)) + 1e-6) * 0.95
        enhanced_44k = librosa.resample(enhanced_wave, orig_sr=sr, target_sr=44100)
        sf.write(str(temp_wav), enhanced_44k, 44100)
    except Exception as master_err:
        print(f"[F5-Runner] Mastering fallback to raw: {master_err}")
        sf.write(str(temp_wav), final_wave, sr)

    if output_p.suffix.lower() == ".mp3":
        import subprocess
        cmd = [
            "ffmpeg", "-y",
            "-i", str(temp_wav),
            "-b:a", "192k",
            str(output_p)
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        if temp_wav.exists():
            temp_wav.unlink(missing_ok=True)
    else:
        if temp_wav != output_p:
            temp_wav.replace(output_p)

    dur = len(final_wave) / sr
    elapsed = time.time() - t0
    print(f"[F5-Runner] Completed in {elapsed:.2f}s! Audio duration: {dur:.2f}s")

    result = {
        "status": "success",
        "output_file": str(output_p),
        "duration": round(dur, 2),
        "elapsed": round(elapsed, 2)
    }
    print("RESULT_JSON:" + json.dumps(result))
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="F5-TTS Voice Cloning Runner")
    parser.add_argument("--ref_audio", required=True, help="Path to reference audio")
    parser.add_argument("--ref_text", required=True, help="Reference text transcript")
    parser.add_argument("--gen_text", required=True, help="Text to synthesize")
    parser.add_argument("--output", required=True, help="Output MP3/WAV file path")
    parser.add_argument("--ckpt", default="", help="Model checkpoint path")
    parser.add_argument("--vocab", default="", help="Custom vocab.txt file path")
    parser.add_argument("--lang", default="vi", choices=["vi", "en", "zh"], help="Language code (vi, en, zh)")
    parser.add_argument("--nfe_step", type=int, default=16, help="NFE steps (default: 16)")
    parser.add_argument("--speed", type=float, default=1.0, help="Speech speed")

    args = parser.parse_args()
    run_inference(
        ref_audio_path=args.ref_audio,
        ref_text=args.ref_text,
        gen_text=args.gen_text,
        output_path=args.output,
        ckpt_path=args.ckpt,
        vocab_path=args.vocab,
        lang=args.lang,
        nfe_step=args.nfe_step,
        speed=args.speed,
    )
