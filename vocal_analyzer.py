"""
SLEEP2K VOCAL ANALYZER ENGINE - ACOUSTIC & PROSODIC INTELLIGENCE
Phân tích chuyên sâu 8 chiều đặc trưng của file vocal âm thanh đầu vào:
1. Intonation (Ngữ điệu & Đường cao độ F0)
2. Breathing (Hơi thở & Tiếng lấy hơi sinh học)
3. Pauses (Ngắt nghỉ & Khoảng lặng)
4. Pronunciation & Vietnamese Tones (6 thanh điệu tiếng Việt)
5. Stress & Emphasis (Trọng âm & Nhấn nhá năng lượng)
6. Rhythm & Pacing (Tốc độ & Nhịp điệu phát âm)
7. Vocal Texture (Chất giọng: Trong trẻo, Khàn, Hơi gió, HNR, Jitter, Shimmer, Formants)
8. Expressiveness & Emotion (Biểu cảm & Năng lượng cảm xúc)
"""

import os
import time
import math
from pathlib import Path
from typing import Dict, Any, List

import numpy as np
import soundfile as sf
import librosa
import parselmouth
from parselmouth.praat import call

try:
    import opensmile
    _HAS_OPENSMILE = True
except Exception:
    _HAS_OPENSMILE = False


class VocalAnalyzer:
    """Bộ máy phân tích âm học và ngữ điệu vocal đa tầng."""

    def __init__(self):
        self.smile = None
        if _HAS_OPENSMILE:
            try:
                self.smile = opensmile.Smile(
                    feature_set=opensmile.FeatureSet.eGeMAPSv02,
                    feature_level=opensmile.FeatureLevel.Functionals,
                )
            except Exception as e:
                print(f"Warning initializing openSMILE: {e}")

    def analyze(self, audio_path: str, language: str = "vi") -> Dict[str, Any]:
        """
        Phân tích toàn diện file audio đầu vào và trả về báo cáo phân tầng:
        - global_summary: Báo cáo tổng thể toàn bộ file
        - intonation_curve: Dữ liệu vẽ biểu đồ cao độ theo thời gian
        - temporal_timeline: Dòng thời gian các sự kiện (lời nói, hơi thở, ngắt nghỉ)
        - vocal_texture: Chi tiết chất giọng (HNR, Jitter, Shimmer, Formants)
        - vietnamese_tone_profile: Đặc trưng thanh điệu tiếng Việt
        """
        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        # 1. Đọc audio bằng SoundFile và Librosa
        y, sr = librosa.load(str(audio_file), sr=16000, mono=True)
        duration = float(librosa.get_duration(y=y, sr=sr))
        if duration < 0.3:
            raise ValueError("File âm thanh quá ngắn (cần tối thiểu 0.5 giây)")

        # 2. Phân tích Praat Parselmouth (F0, Formants, HNR, Jitter, Shimmer)
        snd = parselmouth.Sound(str(audio_file))
        praat_stats = self._extract_praat_features(snd)

        # 3. Phân tích Dòng thời gian: Lời nói, Ngắt nghỉ, Tiếng lấy hơi
        timeline_events = self._detect_temporal_events(y, sr)

        # 4. Phân tích Đường cao độ F0 (Intonation Curve)
        intonation_data = self._extract_intonation_curve(snd, max_points=40)

        # 5. Phân tích Nhịp điệu, Tốc độ & Trọng âm (Pacing & Stress)
        rhythm_stats = self._analyze_rhythm_and_stress(y, sr, duration, timeline_events)

        # 6. Trích xuất openSMILE eGeMAPSv02 (Biểu cảm & Năng lượng)
        opensmile_stats = self._extract_opensmile_features(audio_file)

        # 7. Phân tích cấu trúc thanh điệu Tiếng Việt (Tonal Contour)
        tone_stats = self._analyze_vietnamese_tones(intonation_data)

        # 8. Phân loại tổng hợp chất giọng và biểu cảm
        vocal_type, emotion_type = self._classify_voice_style(praat_stats, opensmile_stats, rhythm_stats)

        gender = "Nam (Trầm)" if praat_stats["mean_f0"] < 165 else "Nữ (Thanh / Sáng)"

        return {
            "status": "success",
            "file_info": {
                "filename": audio_file.name,
                "duration_seconds": round(duration, 2),
                "sample_rate": sr,
                "predicted_gender": gender,
            },
            "profile_8_dimensions": {
                "1_intonation": {
                    "mean_pitch_hz": round(praat_stats["mean_f0"], 1),
                    "min_pitch_hz": round(praat_stats["min_f0"], 1),
                    "max_pitch_hz": round(praat_stats["max_f0"], 1),
                    "pitch_stdev_hz": round(praat_stats["stdev_f0"], 1),
                    "pitch_stability": praat_stats["pitch_stability"],
                    "pitch_range_semitones": round(praat_stats["pitch_range_semitones"], 1),
                },
                "2_breathing": {
                    "breath_events_count": len([e for e in timeline_events if e["type"] == "breath"]),
                    "total_breath_time_s": round(sum(e["duration"] for e in timeline_events if e["type"] == "breath"), 2),
                    "breathiness_ratio_pct": round(rhythm_stats["breath_ratio"] * 100, 1),
                    "has_natural_breathing": len([e for e in timeline_events if e["type"] == "breath"]) > 0,
                },
                "3_pauses": {
                    "pause_count": len([e for e in timeline_events if e["type"] == "pause"]),
                    "total_pause_time_s": round(sum(e["duration"] for e in timeline_events if e["type"] == "pause"), 2),
                    "pause_ratio_pct": round(rhythm_stats["pause_ratio"] * 100, 1),
                    "average_pause_duration_s": round(rhythm_stats["avg_pause_s"], 2),
                },
                "4_pronunciation": {
                    "language": language,
                    "clarity_score_pct": min(100, max(40, round(praat_stats["hnr"] * 4.2))),
                    "vietnamese_tones": tone_stats,
                },
                "5_stress_and_emphasis": {
                    "dynamic_range_db": round(rhythm_stats["dynamic_range_db"], 1),
                    "stress_frequency": rhythm_stats["stress_level"],
                    "emphasis_style": rhythm_stats["emphasis_style"],
                },
                "6_rhythm_and_pacing": {
                    "speaking_rate_syllables_sec": round(rhythm_stats["syllables_per_sec"], 1),
                    "tempo_category": rhythm_stats["tempo_category"],
                    "speech_time_ratio_pct": round(rhythm_stats["speech_ratio"] * 100, 1),
                },
                "7_vocal_texture": {
                    "texture_category": vocal_type,
                    "hnr_db": round(praat_stats["hnr"], 1),
                    "jitter_local_pct": round(praat_stats["jitter"] * 100, 2),
                    "shimmer_local_pct": round(praat_stats["shimmer"] * 100, 2),
                    "formants_hz": {
                        "f1_openness": round(praat_stats["f1"], 0),
                        "f2_frontness": round(praat_stats["f2"], 0),
                        "f3_brightness": round(praat_stats["f3"], 0),
                    },
                },
                "8_expressiveness": {
                    "emotional_tone": emotion_type,
                    "valence_energy": opensmile_stats.get("energy_profile", "Cân bằng"),
                    "vocal_richness_score": min(100, max(30, round(praat_stats["pitch_range_semitones"] * 6.5 + praat_stats["hnr"] * 1.5))),
                },
            },
            "timeline_events": timeline_events,
            "intonation_curve": intonation_data,
        }

    # =========================================================================
    # INTERNAL HELPERS
    # =========================================================================

    def _extract_praat_features(self, snd: parselmouth.Sound) -> Dict[str, Any]:
        """Trích xuất F0, Formant, Jitter, Shimmer, HNR từ Praat Parselmouth."""
        pitch = snd.to_pitch_ac(time_step=0.01, pitch_floor=75.0, pitch_ceiling=500.0)
        mean_f0 = call(pitch, "Get mean", 0, 0, "Hertz")
        min_f0 = call(pitch, "Get minimum", 0, 0, "Hertz", "Parabolic")
        max_f0 = call(pitch, "Get maximum", 0, 0, "Hertz", "Parabolic")
        stdev_f0 = call(pitch, "Get standard deviation", 0, 0, "Hertz")

        if math.isnan(mean_f0) or mean_f0 == 0:
            mean_f0, min_f0, max_f0, stdev_f0 = 150.0, 100.0, 200.0, 20.0

        # Dải bán cung
        if min_f0 > 0 and max_f0 > min_f0:
            pitch_range = 12 * math.log2(max_f0 / min_f0)
        else:
            pitch_range = 4.0

        stability = "Rất ổn định" if stdev_f0 < 18 else ("Biến đổi vừa phải" if stdev_f0 < 38 else "Diễn cảm / Biến thiên mạnh")

        # HNR (Harmonics-to-Noise Ratio)
        harmonicity = snd.to_harmonicity_cc()
        hnr = call(harmonicity, "Get mean", 0, 0)
        if math.isnan(hnr):
            hnr = 15.0

        # PointProcess cho Jitter & Shimmer
        point_process = call(snd, "To PointProcess (periodic, cc)", 75.0, 500.0)
        jitter = call(point_process, "Get jitter (local)", 0, 0, 0.0001, 0.02, 1.3)
        if math.isnan(jitter):
            jitter = 0.015

        shimmer = call([snd, point_process], "Get shimmer (local)", 0, 0, 0.0001, 0.02, 1.3, 1.6)
        if math.isnan(shimmer):
            shimmer = 0.045

        # Formant F1, F2, F3
        formants = snd.to_formant_burg(max_number_of_formants=5, maximum_formant=5500)
        f1 = call(formants, "Get mean", 1, 0, 0, "Hertz")
        f2 = call(formants, "Get mean", 2, 0, 0, "Hertz")
        f3 = call(formants, "Get mean", 3, 0, 0, "Hertz")

        return {
            "mean_f0": mean_f0,
            "min_f0": min_f0,
            "max_f0": max_f0,
            "stdev_f0": stdev_f0,
            "pitch_range_semitones": pitch_range,
            "pitch_stability": stability,
            "hnr": hnr,
            "jitter": jitter,
            "shimmer": shimmer,
            "f1": f1 if not math.isnan(f1) else 650.0,
            "f2": f2 if not math.isnan(f2) else 1700.0,
            "f3": f3 if not math.isnan(f3) else 2600.0,
        }

    def _detect_temporal_events(self, y: np.ndarray, sr: int) -> List[Dict[str, Any]]:
        """
        Bóc tách dòng thời gian:
        - speech: lời nói
        - pause: khoảng lặng tuyệt đối
        - breath: tiếng lấy hơi / thở ra (đặc trưng HNR âm, ZCR cao, năng lượng vừa phải)
        """
        frame_len = int(sr * 0.025)
        hop_len = int(sr * 0.010)

        rms = librosa.feature.rms(y=y, frame_length=frame_len, hop_length=hop_len)[0]
        zcr = librosa.feature.zero_crossing_rate(y, frame_length=frame_len, hop_length=hop_len)[0]
        spec_cent = librosa.feature.spectral_centroid(y=y, sr=sr, n_fft=frame_len, hop_length=hop_len)[0]
        spec_flat = librosa.feature.spectral_flatness(y=y, n_fft=frame_len, hop_length=hop_len)[0]

        rms_db = librosa.amplitude_to_db(rms, ref=np.max)
        times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop_len)

        # Tiếng hơi thở: âm lượng -45dBFS đến -18dBFS, phẳng phổ > 0.008, tần số trọng tâm 1200-4200Hz, zcr > 0.04
        is_breath = (rms_db > -45) & (rms_db < -18) & (spec_cent > 1200) & (spec_cent < 4200) & (spec_flat > 0.008) & (zcr > 0.04)
        is_speech = rms_db >= -22
        is_silence = rms_db <= -45

        events = []
        cur_type = None
        start_t = 0.0

        for t, b, sp, sil in zip(times, is_breath, is_speech, is_silence):
            if sp:
                t_type = "speech"
            elif b:
                t_type = "breath"
            else:
                t_type = "pause"

            if cur_type is None:
                cur_type = t_type
                start_t = t
            elif t_type != cur_type:
                dur = t - start_t
                if dur >= 0.06:
                    events.append({
                        "type": cur_type,
                        "start": round(start_t, 2),
                        "end": round(t, 2),
                        "duration": round(dur, 2)
                    })
                cur_type = t_type
                start_t = t

        if cur_type:
            events.append({
                "type": cur_type,
                "start": round(start_t, 2),
                "end": round(float(times[-1]), 2),
                "duration": round(float(times[-1]) - start_t, 2)
            })

        return events

    def _extract_intonation_curve(self, snd: parselmouth.Sound, max_points: int = 40) -> List[Dict[str, Any]]:
        """Trích xuất chuỗi thời gian cao độ F0 được nén xuống 30-40 điểm để vẽ biểu đồ trực quan."""
        pitch = snd.to_pitch_ac(time_step=0.02, pitch_floor=75.0, pitch_ceiling=500.0)
        xs = pitch.xs()
        curve = []
        for x in xs:
            val = pitch.get_value_at_time(x)
            curve.append({"time": round(x, 2), "pitch_hz": round(val, 1) if not math.isnan(val) else 0.0})

        if len(curve) > max_points:
            step = len(curve) / max_points
            curve = [curve[int(i * step)] for i in range(max_points)]

        return curve

    def _analyze_rhythm_and_stress(self, y: np.ndarray, sr: int, duration: float, timeline: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Đo tốc độ nói, khoảng nghỉ, sự biến thiên năng lượng và điểm nhấn trọng âm."""
        total_speech = sum(e["duration"] for e in timeline if e["type"] == "speech")
        total_pause = sum(e["duration"] for e in timeline if e["type"] == "pause")
        total_breath = sum(e["duration"] for e in timeline if e["type"] == "breath")

        speech_ratio = total_speech / duration if duration > 0 else 0.7
        pause_ratio = total_pause / duration if duration > 0 else 0.2
        breath_ratio = total_breath / duration if duration > 0 else 0.1

        # Ước lượng số âm tiết phát âm dựa trên Onset Detection của Librosa
        onsets = librosa.onset.onset_detect(y=y, sr=sr, backtrack=False)
        syllable_count = max(1, len(onsets))
        syllables_per_sec = syllable_count / total_speech if total_speech > 0.5 else syllable_count / duration

        if syllables_per_sec < 3.2:
            tempo_cat = "Chậm rãi / Thong thả (Truyện đêm muộn)"
        elif syllables_per_sec <= 4.8:
            tempo_cat = "Vừa phải / Tự nhiên (Kể chuyện & Tâm sự)"
        else:
            tempo_cat = "Nhanh / Dồn dập (Review phim / Tin tức)"

        # Năng lượng động (Dynamic Range)
        rms = librosa.feature.rms(y=y)[0]
        rms_db = librosa.amplitude_to_db(rms, ref=np.max)
        voiced_db = rms_db[rms_db > -35]
        if len(voiced_db) > 10:
            dyn_range = float(np.percentile(voiced_db, 95) - np.percentile(voiced_db, 10))
        else:
            dyn_range = 14.0

        if dyn_range > 20:
            stress_level = "Nhấn nhá rõ nét (Biểu cảm phong phú)"
            emphasis_style = "Nhấn bằng cả biên độ âm lượng và ngắt hơi"
        elif dyn_range > 12:
            stress_level = "Nhấn nhá tự nhiên"
            emphasis_style = "Nhấn nhẹ ở đầu cụm từ"
        else:
            stress_level = "Đều đều / Nhẹ nhàng"
            emphasis_style = "Giữ đều năng lượng (Thư giãn, ru ngủ)"

        pauses = [e["duration"] for e in timeline if e["type"] == "pause"]
        avg_pause = sum(pauses) / len(pauses) if pauses else 0.25

        return {
            "speech_ratio": speech_ratio,
            "pause_ratio": pause_ratio,
            "breath_ratio": breath_ratio,
            "syllables_per_sec": syllables_per_sec,
            "tempo_category": tempo_cat,
            "dynamic_range_db": dyn_range,
            "stress_level": stress_level,
            "emphasis_style": emphasis_style,
            "avg_pause_s": avg_pause,
        }

    def _extract_opensmile_features(self, audio_file: Path) -> Dict[str, Any]:
        """Trích xuất eGeMAPSv02 functionals từ openSMILE."""
        if not self.smile:
            return {"energy_profile": "Cân bằng / Tự nhiên"}
        try:
            feats = self.smile.process_file(str(audio_file))
            loud_mean = float(feats["loudness_sma3_amean"].values[0]) if "loudness_sma3_amean" in feats.columns else 0.7
            if loud_mean > 1.1:
                e_profile = "Năng lượng cao / Hào hứng"
            elif loud_mean < 0.4:
                e_profile = "Nhẹ nhàng / Thì thầm"
            else:
                e_profile = "Cân bằng / Ấm áp"
            return {"energy_profile": e_profile}
        except Exception:
            return {"energy_profile": "Cân bằng / Tự nhiên"}

    def _analyze_vietnamese_tones(self, curve: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Phân tích hình thái cao độ để thống kê tỷ lệ biến thiên thanh điệu tiếng Việt."""
        valid_pitches = [p["pitch_hz"] for p in curve if p["pitch_hz"] > 40]
        if len(valid_pitches) < 4:
            return {"dominant_tones": "Ngang / Huyền / Sắc", "slope_variety": "Tự nhiên"}

        slopes = np.diff(valid_pitches)
        rising = np.sum(slopes > 5)
        falling = np.sum(slopes < -5)
        flat = np.sum(np.abs(slopes) <= 5)
        total = max(1, len(slopes))

        return {
            "rising_tones_pct": round(float(rising) / total * 100, 1),
            "falling_tones_pct": round(float(falling) / total * 100, 1),
            "flat_tones_pct": round(float(flat) / total * 100, 1),
            "inflection_description": "Độ lướt thanh điệu sắc/ngang mềm mại, dứt khoát ở cuối câu" if rising > falling else "Trầm ấm, buông nhẹ ở đuôi câu",
        }

    def _classify_voice_style(self, praat: Dict[str, Any], smile: Dict[str, Any], rhythm: Dict[str, Any]):
        """Xác định danh mục chất giọng và phong cách biểu cảm."""
        hnr = praat["hnr"]
        f0 = praat["mean_f0"]
        jitter = praat["jitter"]

        # Chất giọng (Texture)
        if hnr < 13.0 or jitter > 0.025:
            vocal_type = "Hơi gió / Hơi khàn quyến rũ (Breathy / Husky)"
        elif hnr > 22.0:
            vocal_type = "Trong trẻo / Thanh thoát (Crystal Clear)"
        elif f0 < 135:
            vocal_type = "Trầm ấm / Dày tiếng (Deep & Warm)"
        elif f0 > 230:
            vocal_type = "Trong sáng / Cao vút (Bright & High)"
        else:
            vocal_type = "Tự nhiên / Cân bằng (Natural & Balanced)"

        # Biểu cảm (Emotion)
        if rhythm["syllables_per_sec"] < 3.3 and praat["pitch_range_semitones"] < 6:
            emotion_type = "Tâm sự / Đêm khuya / Thì thầm ấm áp"
        elif rhythm["dynamic_range_db"] > 18:
            emotion_type = "Kể chuyện / Diễn cảm lôi cuốn (Storytelling)"
        elif rhythm["syllables_per_sec"] > 4.6:
            emotion_type = "Dứt khoát / Năng động (Review / Tin tức)"
        else:
            emotion_type = "Truyền cảm / Thân thiện / Nhẹ nhàng"

        return vocal_type, emotion_type


# Singleton Instance
analyzer_instance = VocalAnalyzer()

if __name__ == "__main__":
    import sys, json
    test_file = sys.argv[1] if len(sys.argv) > 1 else "test_speech.wav"
    print(f"Testing VocalAnalyzer on '{test_file}'...")
    res = analyzer_instance.analyze(test_file)
    print(json.dumps(res, ensure_ascii=False, indent=2))
