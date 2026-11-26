"""Mikrofon kaydı: 16 kHz mono int16, 30 ms çerçeve, webrtcvad ile "sustu mu" tespiti.

Durma mantığı (`VadSegmenter`) saf Python'dur ve sentetik çerçevelerle test edilir.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480

# Kayıt sonuç durumları
OK = "ok"  # konuşma alındı
NO_SPEECH = "no_speech"  # süre içinde konuşma başlamadı
CANCELLED = "cancelled"
ERROR = "error"


class MicrophoneError(Exception):
    pass


@dataclass
class VadParams:
    silence_ms: int = 1200  # konuşmadan sonra bu kadar sessizlik -> dur
    max_s: float = 30.0
    no_speech_s: float = 6.0
    start_frames: int = 3  # konuşma başlangıcı için gereken konuşma çerçevesi sayısı


class VadSegmenter:
    """Çerçeve çerçeve besle; `feed()` karar döner: None (devam) | OK | NO_SPEECH."""

    def __init__(self, p: VadParams, frame_ms: int = FRAME_MS):
        self.p = p
        self.frame_ms = frame_ms
        self.frames = 0
        self.speech_started = False
        self._speech_run = 0
        self._silence_frames = 0

    @property
    def elapsed_ms(self) -> int:
        return self.frames * self.frame_ms

    def feed(self, is_speech: bool) -> str | None:
        self.frames += 1
        if not self.speech_started:
            self._speech_run = self._speech_run + 1 if is_speech else 0
            if self._speech_run >= self.p.start_frames:
                self.speech_started = True
                self._silence_frames = 0
            elif self.elapsed_ms >= self.p.no_speech_s * 1000:
                return NO_SPEECH
            return None
        self._silence_frames = 0 if is_speech else self._silence_frames + 1
        if self._silence_frames * self.frame_ms >= self.p.silence_ms:
            return OK
        if self.elapsed_ms >= self.p.max_s * 1000:
            return OK
        return None


def record_frames(read_frame: Callable[[], np.ndarray], is_speech: Callable[[bytes], bool], params: VadParams,
                  stop_event: threading.Event, cancel_event: threading.Event) -> tuple[str, np.ndarray]:
    """Çerçeve okuyucu ve VAD ile kaydı yürütür. Donanımdan bağımsız (test edilebilir)."""
    seg = VadSegmenter(params)
    chunks: list[np.ndarray] = []
    while True:
        if cancel_event.is_set():
            return CANCELLED, np.zeros(0, np.int16)
        if stop_event.is_set():  # manuel durdurma (F1 / mikrofon butonu)
            break
        frame = read_frame()
        chunks.append(frame)
        decision = seg.feed(is_speech(frame.tobytes()))
        if decision == NO_SPEECH:
            return NO_SPEECH, np.zeros(0, np.int16)
        if decision == OK:
            break
    audio = np.concatenate(chunks) if chunks else np.zeros(0, np.int16)
    if not seg.speech_started:  # manuel durdurma ama hiç konuşma yok
        return NO_SPEECH, np.zeros(0, np.int16)
    return OK, audio


class Recorder:
    """Ayrı thread'de kaydeder. Geri çağrılar KAYIT thread'inde gelir; çağıran ana thread'e taşımalı."""

    def __init__(self, params: VadParams, aggressiveness: int, on_result: Callable[[str, np.ndarray], None],
                 on_error: Callable[[str], None], on_start: Callable[[], None] | None = None):
        self.params = params
        self.aggressiveness = aggressiveness
        self.on_result = on_result
        self.on_error = on_error
        self.on_start = on_start
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, name="reis-recorder", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop_now(self) -> None:
        self._stop.set()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self) -> None:
        try:
            import sounddevice as sd
            import webrtcvad

            try:
                sd.query_devices(kind="input")
            except Exception as e:
                raise MicrophoneError("Mikrofon bulunamadı. Bir mikrofon bağla ve tekrar dene.") from e
            if self.on_start:  # bip, akış açılmadan önce çalınır ki kayda karışmasın
                self.on_start()
            vad = webrtcvad.Vad(max(0, min(3, self.aggressiveness)))
            try:
                stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME_SAMPLES)
                stream.start()
            except Exception as e:
                raise MicrophoneError(f"Mikrofon açılamadı: {e}") from e
            try:
                def read():
                    data, _overflow = stream.read(FRAME_SAMPLES)
                    return np.ascontiguousarray(data[:, 0])

                status, audio = record_frames(
                    read, lambda b: vad.is_speech(b, SAMPLE_RATE), self.params, self._stop, self._cancel)
            finally:
                stream.stop()
                stream.close()
            if status != CANCELLED:
                self.on_result(status, audio)
        except MicrophoneError as e:
            self.on_error(str(e))
        except Exception as e:
            log.exception("Kayıt hatası")
            self.on_error(f"Kayıt hatası: {e}")
