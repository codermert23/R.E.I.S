"""faster-whisper: tembel yükleme, boşta kalınca bellekten bırakma."""
from __future__ import annotations

import gc
import logging
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from ..config.settings import Settings, models_dir
from .downloads import whisper_dir, whisper_ready

log = logging.getLogger(__name__)


class STTError(Exception):
    pass


def build_initial_prompt(project_names: list[str]) -> str | None:
    names = [n for n in project_names if n]
    if not names:
        return None
    return "Reis asistan. Projeler: " + ", ".join(names) + "."


class SpeechToText(QObject):
    _loaded_sig = Signal()

    def __init__(self, settings: Settings, parent=None, model_factory=None, clock=time.monotonic):
        super().__init__(parent)
        self.settings = settings
        self._factory = model_factory
        self._model = None
        self._loaded_size: str | None = None
        self._last_used = 0.0
        self._lock = threading.Lock()
        self._clock = clock
        self._timer = QTimer(self)  # yalnızca model yüklüyken çalışır (tepsideyken hiçbir şey uyanmaz)
        self._timer.setInterval(30_000)
        self._timer.timeout.connect(self.unload_if_idle)
        self._loaded_sig.connect(self._arm_timer)

    def model_ready(self) -> bool:
        return whisper_ready(models_dir(), self.settings.whisper_model)

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self):
        size = self.settings.whisper_model
        if self._model is not None and self._loaded_size != size:
            self._model = None
        if self._model is None:
            if not whisper_ready(models_dir(), size):
                raise STTError(f"Whisper '{size}' modeli indirilmemiş.")
            path = str(whisper_dir(models_dir(), size))
            if self._factory:
                self._model = self._factory(path)
            else:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(path, device="cpu", compute_type="int8")
            self._loaded_size = size
            log.info("Whisper yüklendi: %s", size)
        self._last_used = self._clock()
        self._start_timer()
        return self._model

    def _arm_timer(self) -> None:
        if self._model is not None and not self._timer.isActive():
            self._timer.start()

    def _start_timer(self) -> None:
        self._loaded_sig.emit()  # worker thread'den gelir; sinyal QTimer'ı ana thread'de başlatır

    def preload(self) -> None:
        """Kayıt sürerken modeli yükler (ilk çeviri gecikmesini gizler). Worker thread'den çağır."""
        if not self.model_ready():
            return
        with self._lock:
            self._load()

    def transcribe(self, pcm: np.ndarray, project_names: list[str]) -> str:
        with self._lock:
            model = self._load()
            audio = pcm.astype(np.float32) / 32768.0
            segments, _info = model.transcribe(
                audio,
                language=self.settings.stt_language or None,
                initial_prompt=build_initial_prompt(project_names),
                beam_size=3,
                vad_filter=False,
                condition_on_previous_text=False,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            self._last_used = self._clock()
            return text

    def unload_if_idle(self) -> None:
        minutes = self.settings.whisper_idle_minutes
        if self._model is None:
            self._timer.stop()
            return
        if minutes <= 0:
            return
        if not self._lock.acquire(blocking=False):
            return  # çeviri sürüyor
        try:
            if self._model is not None and self._clock() - self._last_used >= minutes * 60:
                self._model = None
                self._loaded_size = None
                gc.collect()
                log.info("Whisper boşta kaldığı için bellekten bırakıldı")
            if self._model is None:
                self._timer.stop()
        finally:
            self._lock.release()
