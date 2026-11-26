"""'Ses motorunu kur': yazgı için ayrı runtime venv (CPU torch) + model ağırlıkları (~1 GB indirme).

Yalnızca kullanıcı isteğiyle çalışır (ilk kurulum sihirbazı sayfası ya da Ayarlar > Ses). Komutlar ve yollar
saf fonksiyonlardır (test edilebilir); `run_setup` bunları sırayla çalıştırır, ilerleme/iptal bildirir.
Yalnızca resmî kaynaklar: pypi.org, download.pytorch.org, huggingface.co. Kaynaktan çalıştırmada (Python var)
çalışır; paketlenmiş Reis.exe'de sistem Python'u olmadığından kurulum devre dışıdır.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Callable

from ..downloads import DownloadCancelled, download_files
from . import yazgi_provider as yz

log = logging.getLogger(__name__)

HF_REPO = "erkamk/yazgi-tts-1"
HF_REVISION = "87c89ad84556fb3f1c171f7f88a37ddc0f806b04"  # sabitlenmiş commit (içerik değişmesin)
HF_BASE = f"https://huggingface.co/{HF_REPO}/resolve/{HF_REVISION}"
WHEEL_URL = f"{HF_BASE}/dist/yazgi_tts-0.1.0-py3-none-any.whl"
TORCH_INDEX = "https://download.pytorch.org/whl/cpu"
PYPI_INDEX = "https://pypi.org/simple"
TORCH_REQ = "torch==2.15.0+cpu"
EXTRA_REQS = ["normalizer-tr==0.7.0", "numpy==2.5.5", "soundfile==0.14.0"]  # yazgı bağımlılıkları (kurulumda doğrulanan sürümler)
VOICE_NAMES = ["male-01", "male-02", "male-03", "male-04", "female-01", "female-02", "female-03", "female-04"]
MODEL_NAMES = ["config.json", "codec.pt", "model.pt"]
SIZE_WARNING = "Yaklaşık 1 GB indirme (PyTorch CPU ~130 MB + bağımlılıklar + model ~600 MB) ve ~2 GB disk gerekir."
FROZEN_MESSAGE = ("Ses motoru kurulumu paketlenmiş Reis.exe'de yapılamaz (sistem Python'u yok). Reis şimdilik Edge "
                  "(ve kuruluysa Piper) sesini kullanır. Yazgı için Reis'i kaynaktan çalıştır: README'deki "
                  "'Kaynaktan kurulum' adımlarını izle, sonra Ayarlar > Ses > 'Ses motorunu kur'.")

Progress = Callable[[int, int, str], None]


def blocker() -> str:
    """Kurulum yapılamıyorsa Türkçe neden, yapılabiliyorsa boş."""
    if getattr(sys, "frozen", False):
        return FROZEN_MESSAGE
    return ""


def venv_command(base_python: str, root: Path) -> list[str]:
    return [str(base_python), "-m", "venv", str(root)]


def pip_torch_command(py: Path) -> list[str]:
    return [str(py), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--progress-bar", "off",
            TORCH_REQ, "--index-url", TORCH_INDEX, "--extra-index-url", PYPI_INDEX]


def pip_yazgi_command(py: Path) -> list[str]:
    return [str(py), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--progress-bar", "off",
            f"yazgi-tts @ {WHEEL_URL}", *EXTRA_REQS, "--index-url", PYPI_INDEX]


def model_files(root: Path) -> list[tuple[Path, str]]:
    """(hedef, url) listesi: voices önce (küçük), büyük ağırlıklar sonra."""
    root = Path(root)
    out = [(root / "voices" / f"{v}.npz", f"{HF_BASE}/voices/{v}.npz") for v in VOICE_NAMES]
    out += [(root / n, f"{HF_BASE}/{n}") for n in MODEL_NAMES]
    return out


def _run(cmd: list[str], cancelled: Callable[[], bool], on_line: Callable[[str], None] | None = None) -> None:
    """Komutu çalıştırır; iptalde sonlandırır; hata olursa çıktının sonunu mesaja koyar."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags,
                            text=True, encoding="utf-8", errors="replace")
    tail: list[str] = []
    import threading

    def pump():
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                tail.append(line)
                del tail[:-15]
                if on_line:
                    on_line(line)

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    while proc.poll() is None:
        if cancelled():
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise DownloadCancelled()
        try:
            proc.wait(0.3)
        except subprocess.TimeoutExpired:
            pass
    t.join(2)
    if proc.returncode != 0:
        raise RuntimeError(f"Komut başarısız (kod {proc.returncode}): {' '.join(cmd[:4])}…\n" + "\n".join(tail[-6:]))


def run_setup(progress: Progress, cancelled: Callable[[], bool], *, base_python: str | None = None,
              verify: bool = True) -> None:
    """Sırayla: venv -> torch -> yazgı -> model dosyaları -> küçük deneme. 0-100 aralığında ilerleme bildirir."""
    msg = blocker()
    if msg:
        raise RuntimeError(msg)
    base_python = base_python or sys.executable
    root, py = yz.runtime_root(), yz.runtime_python()

    def step(pct: int, label: str) -> None:
        progress(pct, 100, label)

    if not py.is_file():
        step(2, "Çalışma ortamı oluşturuluyor…")
        root.parent.mkdir(parents=True, exist_ok=True)
        _run(venv_command(base_python, root), cancelled)
    step(5, "PyTorch (CPU) kuruluyor; birkaç dakika sürebilir…")
    _run(pip_torch_command(py), cancelled, lambda l: l.startswith(("Collecting", "Installing")) and step(
        20 if l.startswith("Installing") else 10, "PyTorch kuruluyor…"))
    step(45, "Yazgı ve bağımlılıkları kuruluyor…")
    _run(pip_yazgi_command(py), cancelled)

    step(55, "Model indiriliyor…")
    base_pct, span = 55, 38

    def dl_progress(done: int, total: int, label: str) -> None:
        progress(base_pct + int(span * done / max(total, 1)), 100, f"Model indiriliyor: {label}")

    download_files(model_files(yz.model_dir()), dl_progress, cancelled)
    if not yz.models_ready():
        raise RuntimeError("Model dosyaları eksik kaldı; kurulumu yeniden dene.")

    if verify:
        step(94, "Ses motoru deneniyor…")
        w = yz._Worker(yz.default_command(), log_path=root / "worker.log")
        try:
            pcm, sr = w.synth("Merhaba.", yz.DEFAULT_VOICE)
        finally:
            w.close()
        if pcm.size < sr // 4:
            raise RuntimeError("Deneme sesi çok kısa üretildi; kurulum doğrulanamadı.")
    root.mkdir(parents=True, exist_ok=True)
    yz.ready_marker().write_text(json.dumps({"torch": TORCH_REQ, "revision": HF_REVISION}), encoding="utf-8")
    progress(100, 100, "tamam")
