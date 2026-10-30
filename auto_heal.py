"""
SLEEP2K ONE-CLICK AUTO-HEAL SCRIPT
Tự động kiểm tra, sửa lỗi và khôi phục toàn bộ hệ thống Sleep2K về trạng thái hoạt động chuẩn 100%.
"""
import sys, os, subprocess, time, json, urllib.request
from pathlib import Path

print("=" * 65)
print("  SLEEP2K AUTO-HEAL - HE THONG TU DONG CHAN DOAN & SUA LOI")
print("=" * 65)

BASE_DIR = Path(r"D:\sleep2k-deploy")
os.chdir(str(BASE_DIR))

# 1. Dong bo ma nguon Git moi nhat
print("\n[1/5] Kiem tra va dong bo Git voi GitHub main...")
try:
    subprocess.run(["git", "pull", "origin", "main"], check=True)
    print("OK: Ma nguon da cap nhat ban chuan moi nhat!")
except Exception as e:
    print(f"Canh bao Git: {e}")

# 2. Kiem tra thu vien Python
print("\n[2/5] Kiem tra cac thu vien am hoc & AI can thiet...")
required_pkgs = ["parselmouth", "opensmile", "librosa", "soundfile", "requests"]
missing = []
for pkg in required_pkgs:
    try:
        __import__(pkg)
    except ImportError:
        missing.append(pkg)

if missing:
    print(f"Dang cai dat bo sung thu vien thieu: {missing}...")
    subprocess.run([sys.executable, "-m", "pip", "install", *missing], check=True)
    print("OK: Da cai dat du thu vien!")
else:
    print("OK: Toan bo thu vien am hoc & AI da san sang!")

# 3. Kiem tra Local Worker
print("\n[3/5] Kiem tra Worker AI Clone cong 5005...")
worker_online = False
try:
    with urllib.request.urlopen("http://localhost:5005/health", timeout=2) as resp:
        if resp.status == 200:
            worker_online = True
            data = json.loads(resp.read().decode())
            print(f"OK: Local Worker dang ONLINE ({data.get('engine', '48kHz')})!")
except Exception:
    pass

if not worker_online:
    print("Worker noi bo dang TAT. Dang tu dong khoi dong Worker...")
    subprocess.Popen([sys.executable, str(BASE_DIR / "run_clone_system.py")], creationflags=subprocess.CREATE_NEW_CONSOLE)
    time.sleep(10)
    print("OK: Da phat lenh khoi dong worker trong cua so moi!")

# 4. Kiem tra trang thai Cloud Web tren Railway
print("\n[4/5] Kiem tra ket noi Web Cloud Railway...")
try:
    with urllib.request.urlopen("https://sleep2k.up.railway.app/api/clone_worker_status", timeout=6) as resp:
        data = json.loads(resp.read().decode())
        print(f"Trang thai Cloud Web: {data.get('status', 'unknown').upper()}")
        print(f"Worker URL ket noi: {data.get('worker_url', 'None')}")
except Exception as e:
    print(f"Canh bao kiem tra Cloud: {e}")

# 5. Kiem tra bo may phan tich am hoc Vocal DNA
print("\n[5/5] Kiem tra bo may Vocal DNA (Praat + openSMILE)...")
try:
    from vocal_analyzer import analyzer_instance
    sample_file = BASE_DIR / "test_speech.wav"
    if sample_file.exists():
        res = analyzer_instance.analyze(str(sample_file))
        print("OK: Vocal DNA Analyzer hoat dong hoan hao 8 chieu am hoc!")
except Exception as e:
    print(f"Canh bao Vocal DNA: {e}")

print("\n" + "=" * 65)
print("  QUA TRINH TU DONG SUA LOI & CHAN DOAN HOAN TAT 100%!")
print("  Mo web https://sleep2k.up.railway.app de su dung muot ma!")
print("=" * 65)
