"""Çevrimiçi (kredili) sesler için disk önbelleği: aynı cümle iki kez kredi harcamaz."""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 50 * 1024 * 1024


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def cache_key(provider: str, voice: str, model: str, text: str) -> str:
    raw = "\x1f".join((provider, voice, model, normalize_text(text)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AudioCache:
    def __init__(self, directory: Path, max_bytes: int = DEFAULT_MAX_BYTES, enabled: bool = True):
        self.dir = Path(directory)
        self.max_bytes = max_bytes
        self.enabled = enabled
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.wav"

    def get(self, key: str) -> tuple[np.ndarray, int] | None:
        if not self.enabled:
            return None
        p = self._path(key)
        try:
            with wave.open(str(p), "rb") as w:
                if w.getnchannels() != 1 or w.getsampwidth() != 2:
                    return None
                sr = w.getframerate()
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.int16)
            if len(pcm) == 0:
                return None
            p.touch()  # en son kullanılan en yeni kalsın
            return pcm, sr
        except (FileNotFoundError, EOFError, wave.Error):
            return None
        except OSError:
            log.exception("Ses önbelleği okunamadı")
            return None

    def put(self, key: str, pcm: np.ndarray, sr: int) -> None:
        if not self.enabled or len(pcm) == 0:
            return
        with self._lock:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                p = self._path(key)
                tmp = p.with_suffix(".tmp")
                with wave.open(str(tmp), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(int(sr))
                    w.writeframes(np.asarray(pcm, dtype="<i2").tobytes())
                tmp.replace(p)
                self._evict()
            except OSError:
                log.exception("Ses önbelleğine yazılamadı")

    def _evict(self) -> None:
        files = []
        for f in self.dir.glob("*.wav"):
            try:
                st = f.stat()
                files.append((st.st_mtime, f.name, f, st.st_size))
            except OSError:
                pass
        total = sum(x[3] for x in files)
        for _m, _n, f, size in sorted(files):  # en eski önce
            if total <= self.max_bytes:
                break
            try:
                f.unlink()
                total -= size
            except OSError:
                pass

    def size_bytes(self) -> int:
        return sum(f.stat().st_size for f in self.dir.glob("*.wav") if f.exists())


class CachedTTS:
    """Bir sağlayıcıyı önbellekle sarar. `peek` yalnızca önbelleğe bakar (kredi harcamaz)."""

    def __init__(self, inner, cache: AudioCache, provider: str, variant):
        self.inner = inner
        self.cache = cache
        self.provider = provider
        self._variant = variant  # () -> (voice, model); sağlayıcı modeli değiştirebilir
        self.last_hit = False

    def _key(self, text: str) -> str:
        voice, model = self._variant()
        return cache_key(self.provider, voice, model, text)

    def peek(self, text: str):
        return self.cache.get(self._key(text))

    def synth(self, text: str):
        hit = self.peek(text)
        self.last_hit = hit is not None
        if hit is not None:
            return hit
        pcm, sr = self.inner.synth(text)
        self.cache.put(self._key(text), pcm, sr)
        return pcm, sr
