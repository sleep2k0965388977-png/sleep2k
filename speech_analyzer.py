import sys, os, math
from pathlib import Path
import numpy as np
import soundfile as sf
import subprocess

def convert_to_wav(audio_path, target_sr=24000):
    """Convert any audio to uncompressed mono WAV without lossy compression."""
    audio_path = Path(audio_path)
    tmp_wav = audio_path.parent / f"analyzed_{audio_path.stem}.wav"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(audio_path),
        "-ac", "1",
        "-ar", str(target_sr),
        str(tmp_wav)
    ]
    subprocess.run(cmd, capture_output=True)
    return tmp_wav

def estimate_f0_contour(signal, sr, frame_len=1024, hop_len=256, fmin=60, fmax=450):
    """Autocorrelation-based fundamental frequency (F0) pitch contour estimator."""
    f0_list = []
    for i in range(0, len(signal) - frame_len, hop_len):
        frame = signal[i:i+frame_len]
        # Energy gate
        energy = np.mean(frame**2)
        if energy < 1e-4:
            continue
        # Autocorrelation
        corr = np.correlate(frame, frame, mode='full')
        corr = corr[len(corr)//2:]
        min_lag = int(sr / fmax)
        max_lag = int(sr / fmin)
        if max_lag < len(corr) and min_lag < max_lag:
            peak_lag = min_lag + np.argmax(corr[min_lag:max_lag])
            if corr[peak_lag] > 0.3 * corr[0]:
                f0 = sr / peak_lag
                f0_list.append(f0)
    return f0_list

def analyze_speech_file(audio_path):
    """
    Rigorously measures the 18 acoustic & prosody dimensions:
    - Sample rate, Spectral Centroid, Spectral Bandwidth
    - 7 Standard Frequency Energy Bands (100-200, 200-350, 350-1k, 1k-2.5k, 2.5k-5k, 5k-8k, 8k-12k)
    - Crest factor, Peak, RMS, Dynamic range
    - F0 mean, F0 range, F0 std dev (inflection/prosody variation)
    - Speech rate & pause ratio
    """
    audio_path = Path(audio_path)
    wav_path = convert_to_wav(audio_path)
    try:
        data, sr = sf.read(str(wav_path))
        if data.ndim > 1:
            data = data[:, 0]
        
        # 1. Amplitude & Dynamics Metrics
        peak = float(np.max(np.abs(data)))
        rms = float(np.sqrt(np.mean(data**2)))
        peak_db = 20 * math.log10(max(1e-6, peak))
        rms_db = 20 * math.log10(max(1e-6, rms))
        crest_factor_db = peak_db - rms_db
        
        # Short-term frame dynamic energy distribution
        frame_len = int(sr * 0.05) # 50ms frames
        frames = [data[i:i+frame_len] for i in range(0, len(data) - frame_len, frame_len)]
        frame_rms = [np.sqrt(np.mean(f**2)) for f in frames if np.mean(f**2) > 0]
        frame_rms_db = [20 * math.log10(max(1e-6, r)) for r in frame_rms]
        
        if frame_rms_db:
            dr_p95 = np.percentile(frame_rms_db, 95)
            dr_p10 = np.percentile(frame_rms_db, 10)
            dynamic_range_p95_p10 = float(dr_p95 - dr_p10)
            noise_floor_est_db = float(np.percentile(frame_rms_db, 5))
        else:
            dynamic_range_p95_p10 = 0.0
            noise_floor_est_db = -90.0

        # 2. Spectral FFT Breakdown
        fft_vals = np.abs(np.fft.rfft(data))
        freqs = np.fft.rfftfreq(len(data), 1.0 / sr)
        total_energy = float(np.sum(fft_vals**2)) + 1e-9

        # Spectral Centroid & Spectral Spread/Bandwidth
        if np.sum(fft_vals) > 0:
            spectral_centroid = float(np.sum(freqs * fft_vals) / np.sum(fft_vals))
            spectral_bandwidth = float(np.sqrt(np.sum(((freqs - spectral_centroid)**2) * fft_vals) / np.sum(fft_vals)))
        else:
            spectral_centroid = 0.0
            spectral_bandwidth = 0.0

        def band_pct(f_low, f_high):
            mask = (freqs >= f_low) & (freqs < f_high)
            return round(float((np.sum(fft_vals[mask]**2) / total_energy) * 100), 2)

        # 3. F0 Prosody / Intonation Dynamics
        f0_values = estimate_f0_contour(data, sr)
        if f0_values:
            f0_mean = float(np.mean(f0_values))
            f0_min = float(np.percentile(f0_values, 5))
            f0_max = float(np.percentile(f0_values, 95))
            f0_range = float(f0_max - f0_min)
            f0_std = float(np.std(f0_values))
        else:
            f0_mean = 0.0
            f0_range = 0.0
            f0_std = 0.0

        # 4. Silence & Pause Ratio
        silence_threshold = 10 ** ((noise_floor_est_db + 10) / 20)
        silence_frames = sum(1 for r in frame_rms if r < silence_threshold)
        pause_ratio_pct = round(float((silence_frames / max(1, len(frame_rms))) * 100), 1)

        return {
            "file": audio_path.name,
            "sample_rate": sr,
            "duration_s": round(len(data) / sr, 2),
            "peak_db": round(peak_db, 1),
            "rms_db": round(rms_db, 1),
            "crest_factor_db": round(crest_factor_db, 1),
            "dynamic_range_p95_p10_db": round(dynamic_range_p95_p10, 1),
            "noise_floor_est_db": round(noise_floor_est_db, 1),
            "spectral_centroid_hz": int(spectral_centroid),
            "spectral_bandwidth_hz": int(spectral_bandwidth),
            "frequency_bands": {
                "100-200 Hz": band_pct(100, 200),
                "200-350 Hz": band_pct(200, 350),
                "350-1000 Hz": band_pct(350, 1000),
                "1000-2500 Hz": band_pct(1000, 2500),
                "2500-5000 Hz": band_pct(2500, 5000),
                "5000-8000 Hz": band_pct(5000, 8000),
                "8000-12000 Hz": band_pct(8000, 12000)
            },
            "prosody_f0": {
                "f0_mean_hz": round(f0_mean, 1),
                "f0_range_hz": round(f0_range, 1),
                "f0_std_dev_hz": round(f0_std, 1),
                "pause_ratio_pct": pause_ratio_pct
            }
        }
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except Exception:
            pass

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        if target.exists():
            res = analyze_speech_file(target)
            import pprint
            pprint.pprint(res)
