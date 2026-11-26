"""yazgı-tts-1 (Apache-2.0): çevrimdışı, ücretsiz Türkçe TTS; Reis'in varsayılan sesi.

Model torch ister; ana uygulamayı küçük tutmak ve torch çökmesinin arayüzü düşürmemesi için AYRI bir
runtime venv'de (%LOCALAPPDATA%/Reis/runtime/yazgi) çalışan küçük bir alt süreçte (yazgi_worker.py) koşar.
İletişim: stdin/stdout üzerinden satır JSON + ham PCM (HTTP portu yok; ana süreç ölürse stdin kapanır,
worker kendiliğinden çıkar). Alt süreç ilk kullanımda başlar, uygulama açıkken canlı kalır, çıkışta kapatılır.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from ...config.settings import models_dir, runtime_dir
from .base import TTSError

log = logging.getLogger(__name__)

VOICES = {
    "male-01": "male-01 (erkek, derin, sıcak anlatıcı)",
    "male-02": "male-02 (erkek, genç, yumuşak)",
    "male-03": "male-03 (erkek, haber sunucusu)",
    "male-04": "male-04 (erkek, alçak, yumuşak)",
    "female-01": "female-01 (kadın, parlak)",
    "female-02": "female-02 (kadın, sakin)",
    "female-03": "female-03 (kadın, kendinden emin)",
    "female-04": "female-04 (kadın, net, rahat)",
}
DEFAULT_VOICE = "male-01"
FIRST_TIMEOUT_S = 240.0  # model yükleme + ısınma (soğuk disk, kurulum sonrası ilk açılış)
CALL_TIMEOUT_S = 8.0  # kısa cümle için taban süre
PER_CHAR_S = 0.15  # CPU'da gerçek zamana yakın hızda üretildiği için metin uzunluğuna göre uzar (brifing)
MODEL_FILES = ("config.json", "model.pt", "codec.pt")
NOTICE = "Yazgı sesi çalışmadı; Edge sesine geçildi."
INSTALL_HINT = "Ayarlar > Ses bölümünden \"Ses motorunu kur\" ile yazgı sesini kur."


class YazgiError(TTSError):
    """notice: yedek sese geçilince kullanıcıya bir kez gösterilir (FallbackTTS)."""
    notice = NOTICE


class _WorkerDied(Exception):
    pass


def runtime_root() -> Path:
    return runtime_dir() / "yazgi"


def runtime_python() -> Path:
    return runtime_root() / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ready_marker() -> Path:
    return runtime_root() / "ready.json"


def model_dir() -> Path:
    return models_dir() / "yazgi"


def voices_present(root: Path) -> bool:
    v = Path(root) / "voices"
    return v.is_dir() and any(v.glob("*.npz"))


def models_ready(root: Path | None = None) -> bool:
    root = Path(root) if root else model_dir()
    return all((root / f).is_file() for f in MODEL_FILES) and voices_present(root)


def is_installed() -> bool:
    return runtime_python().is_file() and ready_marker().is_file() and models_ready()


def worker_script() -> Path:
    return Path(__file__).with_name("yazgi_worker.py")


def default_command() -> list[str]:
    return [str(runtime_python()), "-I", "-u", str(worker_script()), str(model_dir())]


def call_timeout(text: str) -> float:
    return CALL_TIMEOUT_S + PER_CHAR_S * len(text)


class _Worker:
    """Tek alt süreç + okuyucu thread. Aynı anda tek istek (kilit)."""

    def __init__(self, command: list[str], first_timeout: float = FIRST_TIMEOUT_S, log_path: Path | None = None,
                 clock=time.monotonic):
        self.command = list(command)
        self._clock = clock
        self.last_used = clock()
        self._warming = False
        self.first_timeout = first_timeout
        self.log_path = log_path
        self._proc: subprocess.Popen | None = None
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._id = 0
        self.starts = 0
        self._closed = False
        self.voices: list[str] = []

    # ---- süreç yaşam döngüsü
    def _spawn(self) -> None:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        stderr = subprocess.DEVNULL
        opened = None
        if self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                opened = stderr = open(self.log_path, "ab")
            except OSError:
                stderr = subprocess.DEVNULL
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        try:
            proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                    creationflags=flags, env=env)
        except OSError as e:
            raise YazgiError(f"Yazgı ses motoru başlatılamadı: {e}. {INSTALL_HINT}") from e
        finally:
            if opened is not None:
                opened.close()
        self._proc = proc
        self._q = queue.Queue()
        self.starts += 1
        threading.Thread(target=self._read_loop, args=(proc, self._q), daemon=True, name="yazgi-reader").start()

    @staticmethod
    def _read_loop(proc, q: queue.Queue) -> None:
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line.decode("utf-8"))
                except ValueError:
                    continue  # protokol dışı satır
                payload = b""
                n = obj.get("n") if isinstance(obj, dict) else None
                if n:
                    payload = proc.stdout.read(int(n))
                    if len(payload) != int(n):
                        break
                q.put((obj, payload))
        except Exception:  # noqa: BLE001
            log.debug("yazgı okuyucu sonlandı", exc_info=True)
        finally:
            q.put(None)

    def _get(self, timeout: float):
        try:
            item = self._q.get(timeout=timeout)
        except queue.Empty:
            raise _WorkerDied(f"zaman aşımı ({timeout:.0f} sn)") from None
        if item is None:
            raise _WorkerDied("süreç beklenmedik biçimde kapandı")
        return item

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def is_running(self) -> bool:
        return self._alive()

    def ensure_ready(self) -> None:
        """Alt süreç yoksa başlatır ve modeli (ısınma dahil) yükler; zaten canlıysa hiçbir şey yapmaz."""
        with self._lock:
            self._closed = False
            if not self._alive():
                self._kill()
                self._start_and_wait_ready()
            self.last_used = self._clock()

    def prewarm_background(self) -> bool:
        """ensure_ready'yi arka plan thread'inde başlatır. Zaten canlı/ısınıyorsa False döner."""
        if self._alive() or self._warming:
            return False
        self._warming = True

        def run():
            try:
                self.ensure_ready()
            except Exception:  # noqa: BLE001
                log.warning("yazgı ön ısıtma başarısız", exc_info=True)
            finally:
                self._warming = False

        threading.Thread(target=run, daemon=True, name="yazgi-prewarm").start()
        return True

    def close_if_idle(self, idle_s: float) -> bool:
        """`idle_s` saniyedir kullanılmayan canlı süreci kapatır (RSS ~935 MB geri döner); sonraki kullanım
        şeffafça yeniden başlatır. Meşgulse (istek sürüyor/ısınıyor) dokunmaz. True: süreç hâlâ canlı."""
        if self._warming or not self._lock.acquire(blocking=False):
            return True
        try:
            if not self._alive():
                return False
            if self._clock() - self.last_used < idle_s:
                return True
            proc = self._proc
            try:
                proc.stdin.close()  # nazikçe: stdin EOF -> worker çıkar
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass
            self._kill()
            log.info("yazgı worker boşta kaldığı için kapatıldı")
            return False
        finally:
            self._lock.release()

    def _start_and_wait_ready(self) -> None:
        self._spawn()
        try:
            obj, _ = self._get(self.first_timeout)
        except _WorkerDied as e:
            self._kill()
            raise YazgiError(f"Yazgı modeli yüklenemedi ({e}). {INSTALL_HINT}") from e
        if not obj.get("ready"):
            self._kill()
            raise YazgiError(f"Yazgı modeli yüklenemedi: {obj.get('error', 'bilinmeyen hata')}. {INSTALL_HINT}")
        self.voices = list(obj.get("voices") or [])

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.kill()
        except OSError:
            pass
        for s in (proc.stdin, proc.stdout):
            try:
                if s:
                    s.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        """Nazikçe kapat (stdin EOF), olmazsa öldür. Süren bir istek varsa onu beklemeden öldürür."""
        self._closed = True  # süren istek yeniden başlatma denemesin
        got = self._lock.acquire(timeout=1.0)
        try:
            proc = self._proc
            if proc is None:
                return
            try:
                proc.stdin.close()
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass
            self._kill()
        finally:
            if got:
                self._lock.release()

    # ---- istek
    def _request(self, text: str, voice: str):
        if self._closed:
            raise YazgiError("Yazgı ses motoru kapatıldı.")
        if not self._alive():
            self._kill()
            self._start_and_wait_ready()
        self._id += 1
        rid = self._id
        try:
            self._proc.stdin.write(json.dumps({"id": rid, "text": text, "voice": voice}, ensure_ascii=False)
                                   .encode("utf-8") + b"\n")
            self._proc.stdin.flush()
        except OSError as e:
            raise _WorkerDied(f"yazılamadı: {e}") from e
        timeout = call_timeout(text)
        while True:
            obj, payload = self._get(timeout)
            if obj.get("id") == rid:
                return obj, payload  # eski (zaman aşımından kalan) yanıtlar atlanır

    def synth(self, text: str, voice: str) -> tuple[np.ndarray, int]:
        with self._lock:
            try:
                return self._synth_locked(text, voice)
            finally:
                self.last_used = self._clock()

    def _synth_locked(self, text: str, voice: str) -> tuple[np.ndarray, int]:
        self._closed = False
        last: Exception | None = None
        for attempt in (1, 2):  # çökme/zaman aşımında bir kez yeniden başlat
            try:
                obj, payload = self._request(text, voice)
            except _WorkerDied as e:
                last = e
                log.warning("yazgı worker sorunu (deneme %d): %s", attempt, e)
                self._kill()
                continue
            if not obj.get("ok"):
                raise YazgiError(f"Yazgı ses üretemedi: {obj.get('error', 'bilinmeyen hata')}")
            pcm = np.frombuffer(payload, dtype="<i2").astype(np.int16)
            if pcm.size == 0:
                raise YazgiError("Yazgı ses üretemedi (boş çıktı).")
            return pcm, int(obj.get("sr", 24000))
        raise YazgiError(f"Yazgı ses motoru yanıt vermedi ({last}).")


_workers: dict[tuple, _Worker] = {}
_workers_lock = threading.Lock()


def get_worker(command: list[str] | None = None, **kw) -> _Worker:
    """Aynı komut için tek paylaşılan worker (ses değişince yeni süreç açılmaz)."""
    command = list(command or default_command())
    key = tuple(command)
    with _workers_lock:
        w = _workers.get(key)
        if w is None:
            kw.setdefault("log_path", runtime_root() / "worker.log")
            w = _workers[key] = _Worker(command, **kw)
        return w


def prewarm_background(provider: str, voice: str = DEFAULT_VOICE) -> bool:
    """Seçili ses yazgıysa ve kuruluysa worker'ı arka planda yükler. True: ısıtma (ya da canlı worker) var,
    boşta-kapatma zamanlayıcısı kurulmalı."""
    if provider in ("edge", "gemini", "piper"):
        return False
    if not is_installed():
        return False
    w = get_worker()
    w.prewarm_background()
    return True


def close_idle_workers(idle_s: float) -> bool:
    """Boşta kalan worker'ları kapatır. True: hâlâ canlı worker var."""
    with _workers_lock:
        ws = list(_workers.values())
    alive = False
    for w in ws:
        try:
            alive = w.close_if_idle(idle_s) or alive
        except Exception:  # noqa: BLE001
            log.exception("yazgı boşta kapatma hatası")
    return alive


def shutdown_all() -> None:
    with _workers_lock:
        ws = list(_workers.values())
    for w in ws:
        try:
            w.close()
        except Exception:  # noqa: BLE001
            log.exception("yazgı worker kapatılamadı")


atexit.register(shutdown_all)


class YazgiTTS:
    def __init__(self, voice: str = DEFAULT_VOICE, command: list[str] | None = None, worker: _Worker | None = None):
        self.voice = voice or DEFAULT_VOICE
        self._worker = worker or get_worker(command)

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        if not text.strip():
            raise YazgiError("Seslendirilecek metin boş.")
        return self._worker.synth(text, self.voice)

    def prewarm(self) -> None:
        """Modeli yükler (isteğe bağlı, worker thread'den çağır)."""
        self._worker.ensure_ready()
