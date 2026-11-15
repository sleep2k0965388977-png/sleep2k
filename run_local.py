import os, sys, time, webbrowser, threading
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parent
os.chdir(ROOT_DIR)

print("=" * 72)
print("  🌸 SLEEP2K TTS - BẢN CHẠY LOCAL NHÂN BẢN GIỌNG NÓI CHUẨN 100% 🌸")
print("  ⚡ Tận dụng trực tiếp CPU/RAM máy tính để nhân bản chuẩn âm sắc gốc")
print("=" * 72)
print("\n[1/2] Đang nạp bộ máy AI Voice Clone VieNeu (v3 Turbo 48kHz)...")

# Pre-warm Vieneu in background so UI opens fast
def prewarm_vieneu():
    try:
        from app import get_nano_tts
        tts = get_nano_tts()
        if tts:
            print("  ✨ Bộ máy AI VieNeu đã sẵn sàng trong RAM (48kHz)!")
    except Exception as ex:
        print(f"  ⚠️ Lưu ý nạp AI: {ex}")

warm_thread = threading.Thread(target=prewarm_vieneu, daemon=True)
warm_thread.start()

print("[2/2] Đang khởi động máy chủ Web Local trên cổng 5000...")
local_url = "http://127.0.0.1:5000"

def open_browser():
    time.sleep(1.8)
    try:
        webbrowser.open(local_url)
    except Exception:
        pass

threading.Thread(target=open_browser, daemon=True).start()

print("\n" + "=" * 72)
print("  🎉 MÁY CHỦ LOCAL ĐÃ HOẠT ĐỘNG HOÀN HẢO!")
print(f"  🌐 Địa chỉ Web: {local_url} (hoặc http://localhost:5000)")
print("  🎙️ Nhân bản giọng: 100% AI cục bộ, không phụ thuộc vào Cloud/Render")
print("  💡 Đã tự động mở trình duyệt! Bạn có thể bắt đầu sử dụng ngay.")
print("  ❌ Để tắt máy chủ: Đóng cửa sổ này hoặc nhấn Ctrl + C.")
print("=" * 72 + "\n")

# Run app via waitress with auto-restart resilience
while True:
    try:
        from app import app
        from waitress import serve
        serve(app, host="127.0.0.1", port=5000, threads=8, channel_timeout=3600)
    except KeyboardInterrupt:
        print("\n[!] Máy chủ đã dừng bởi người dùng.")
        break
    except Exception as e:
        print(f"\n[⚠️] Lỗi máy chủ: {e}. Đang tự động khởi động lại sau 2 giây...")
        time.sleep(2)
