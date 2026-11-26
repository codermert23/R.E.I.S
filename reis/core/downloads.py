"""Model indirme (Piper sesleri, Whisper). Ağ erişimi httpx ile; test için client enjekte edilir."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import httpx
from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)

PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
WHISPER_REPO = "https://huggingface.co/Systran/faster-whisper-{size}/resolve/main"
PIPER_VOICES = ["tr_TR-dfki-medium", "tr_TR-fahrettin-medium", "tr_TR-fettah-medium"]
WHISPER_SIZES = ["tiny", "base", "small", "medium"]
WHISPER_FILES = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]

Progress = Callable[[int, int, str], None]  # (indirilen_bayt, toplam_bayt, etiket)


class DownloadCancelled(Exception):
    pass


def piper_voice_urls(voice: str) -> tuple[str, str]:
    """'tr_TR-dfki-medium' -> (.onnx url, .onnx.json url)."""
    try:
        region, name, quality = voice.split("-", 2)
        lang = region.split("_")[0]
    except ValueError:
        raise ValueError(f"Geçersiz Piper ses adı: {voice!r}")
    base = f"{PIPER_BASE}/{lang}/{region}/{name}/{quality}/{voice}"
    return base + ".onnx", base + ".onnx.json"


def whisper_urls(size: str) -> list[tuple[str, str]]:
    base = WHISPER_REPO.format(size=size)
    return [(f, f"{base}/{f}") for f in WHISPER_FILES]


def whisper_dir(models_root: Path, size: str) -> Path:
    return Path(models_root) / "whisper" / f"faster-whisper-{size}"


def whisper_ready(models_root: Path, size: str) -> bool:
    d = whisper_dir(models_root, size)
    return all((d / f).is_file() for f in ("config.json", "model.bin", "tokenizer.json"))


def _size_of(client: httpx.Client, url: str) -> int:
    try:
        r = client.head(url, follow_redirects=True)
        return int(r.headers.get("content-length", 0)) if r.status_code < 400 else 0
    except (httpx.HTTPError, ValueError):
        return 0


def download_files(files: list[tuple[Path, str]], progress: Progress | None = None,
                   cancelled: Callable[[], bool] | None = None, client: httpx.Client | None = None,
                   optional: set[str] | None = None) -> None:
    """(hedef_yol, url) listesini sırayla indirir; .part dosyasına yazıp yeniden adlandırır.
    Var olan dosyalar atlanır. `optional` içindeki dosya adları 404 olursa sessizce geçilir."""
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(30, read=60), follow_redirects=True)
    optional = optional or set()
    cancelled = cancelled or (lambda: False)
    progress = progress or (lambda *_: None)
    try:
        todo = [(p, u) for p, u in files if not p.is_file()]
        sizes = [_size_of(client, u) for _, u in todo]
        total = sum(sizes) or 1
        done_before = 0
        for (path, url), size in zip(todo, sizes):
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_name(path.name + ".part")
            got = 0
            with client.stream("GET", url) as r:
                if r.status_code == 404 and path.name in optional:
                    continue
                r.raise_for_status()
                with part.open("wb") as f:
                    for chunk in r.iter_bytes(65536):
                        if cancelled():
                            f.close()
                            part.unlink(missing_ok=True)
                            raise DownloadCancelled()
                        f.write(chunk)
                        got += len(chunk)
                        progress(done_before + got, max(total, done_before + got), path.name)
            part.replace(path)
            done_before += size or got
        progress(total, total, "tamam")
    finally:
        if own:
            client.close()


def download_piper_voice(voice: str, models_root: Path, progress: Progress | None = None,
                         cancelled=None, client: httpx.Client | None = None) -> None:
    onnx, cfg = piper_voice_urls(voice)
    d = Path(models_root) / "piper"
    # json önce, büyük model sonra; ikisi de varsa "kurulu" sayılır
    download_files([(d / f"{voice}.onnx.json", cfg), (d / f"{voice}.onnx", onnx)], progress, cancelled, client)


def download_whisper(size: str, models_root: Path, progress: Progress | None = None,
                     cancelled=None, client: httpx.Client | None = None) -> None:
    d = whisper_dir(models_root, size)
    files = [(d / name, url) for name, url in whisper_urls(size)]
    download_files(files, progress, cancelled, client, optional={"vocabulary.txt"})


class DownloadTask(QThread):
    """fn(progress, cancelled) çalıştırır; ilerlemeyi yüzde olarak yayar."""
    progress = Signal(int, str)
    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        def prog(done, total, label):
            self.progress.emit(int(done * 100 / max(total, 1)), label)

        try:
            self._fn(prog, lambda: self._cancel)
            self.succeeded.emit()
        except DownloadCancelled:
            self.failed.emit("İptal edildi")
        except Exception as e:
            log.exception("İndirme hatası")
            self.failed.emit(f"İndirme başarısız: {e}")
