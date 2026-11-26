from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

from .base import TTSError

log = logging.getLogger(__name__)


def voice_paths(voice: str, models_root: Path) -> tuple[Path, Path]:
    d = Path(models_root) / "piper"
    return d / f"{voice}.onnx", d / f"{voice}.onnx.json"


def is_installed(voice: str, models_root: Path) -> bool:
    m, c = voice_paths(voice, models_root)
    return m.is_file() and c.is_file()


class PiperTTS:
    def __init__(self, voice: str, models_root: Path):
        self.voice_name = voice
        self.models_root = Path(models_root)
        self._voice = None
        self._lock = threading.Lock()

    def _load(self):
        if self._voice is None:
            if not is_installed(self.voice_name, self.models_root):
                raise TTSError(f"Piper sesi '{self.voice_name}' indirilmemiş. Ayarlar > Ses bölümünden indir.")
            from piper import PiperVoice

            model, cfg = voice_paths(self.voice_name, self.models_root)
            self._voice = PiperVoice.load(model, config_path=cfg)
        return self._voice

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        with self._lock:
            voice = self._load()
            chunks = []
            sr = voice.config.sample_rate
            for ch in voice.synthesize(text):
                chunks.append(ch.audio_int16_array)
                sr = ch.sample_rate
        if not chunks:
            raise TTSError("Piper ses üretemedi (boş çıktı).")
        return np.concatenate(chunks).astype(np.int16), int(sr)
