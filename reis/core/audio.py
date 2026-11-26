"""Oynatma (sounddevice). Kesilebilir; sounddevice tembel içe aktarılır."""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

log = logging.getLogger(__name__)


def make_beep(sr: int = 16000, freq: float = 880.0, ms: int = 110) -> np.ndarray:
    n = int(sr * ms / 1000)
    t = np.arange(n) / sr
    env = np.minimum(1.0, np.minimum(t, t[-1] - t) / 0.01)
    return (np.sin(2 * np.pi * freq * t) * env * 0.35 * 32767).astype(np.int16)


FADE_IN_S = 0.004  # parça kenarlarında tık sesi olmasın
FADE_OUT_S = 0.008
BLOCK_S = 0.02  # yazma/iptal kontrol ölçeği
TAIL_S = 0.06


def resample_linear(pcm: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    """Doğrusal örnekleme hızı dönüşümü (yedek sağlayıcı farklı hızda dönerse parçalar aynı akışa uysun)."""
    if sr_from == sr_to or len(pcm) == 0:
        return pcm
    n = max(1, int(round(len(pcm) * sr_to / sr_from)))
    x = np.linspace(0, len(pcm) - 1, n)
    return np.interp(x, np.arange(len(pcm)), pcm.astype(np.float32)).astype(np.int16)


def edge_fade(pcm: np.ndarray, sr: int, fade_in: bool = True, fade_out: bool = True) -> np.ndarray:
    """Parçanın başına/sonuna çok kısa yumuşatma (parçalar arası tık/patlama olmasın)."""
    out = pcm.astype(np.float32)
    n_in = min(int(sr * FADE_IN_S), len(out) // 2) if fade_in else 0
    n_out = min(int(sr * FADE_OUT_S), len(out) // 2) if fade_out else 0
    if n_in:
        out[:n_in] *= np.linspace(0.0, 1.0, n_in, dtype=np.float32)
    if n_out:
        out[-n_out:] *= np.linspace(1.0, 0.0, n_out, dtype=np.float32)
    return out.astype(np.int16)


class Player:
    def __init__(self):
        self._stop = threading.Event()
        self._stream = None  # play_stream'in açık OutputStream'i (stop() başka thread'den keser)

    def play(self, pcm: np.ndarray, sr: int) -> None:
        """Bloklar; stop() çağrılırsa hemen döner."""
        import sounddevice as sd

        self._stop.clear()
        sd.play(pcm, sr)
        deadline = time.monotonic() + len(pcm) / sr + 1.0
        try:
            while not self._stop.wait(0.02):
                stream = sd.get_stream()
                if not stream.active or time.monotonic() > deadline:
                    break
        finally:
            sd.stop()

    def play_stream(self, source, on_first_audio=None) -> None:
        """Parçaları (source.get(timeout) -> Chunk | WAIT | END) kesintisiz tek çıkış akışına yazar: ilk parça
        gelince akış açılır, parçalar arası doğal duraklama sessizlik olarak eklenir, bekleme sırasında akış
        sessizlikle beslenir (underflow cızırtısı olmaz). source.cancel ya da stop() anında keser.
        Bloklar; source.get hata fırlatırsa (üretim hatası) akışı kapatıp iletir."""
        import sounddevice as sd

        cancel = source.cancel
        if not cancel.is_set():
            self._stop.clear()

        def cancelled() -> bool:
            return cancel.is_set() or self._stop.is_set()

        stream = None
        sr = 0
        first = True
        try:
            while not cancelled():
                item = source.get(BLOCK_S)
                if item is source.END:
                    break
                if item is source.WAIT:
                    if stream is not None:
                        self._write(stream, np.zeros(int(sr * BLOCK_S), np.int16), cancelled)
                    continue
                if cancelled():
                    break
                if stream is None:
                    sr = item.sr
                    stream = sd.OutputStream(samplerate=sr, channels=1, dtype="int16")
                    self._stream = stream
                    stream.start()
                pcm = resample_linear(item.pcm, item.sr, sr)
                pcm = edge_fade(pcm, sr, fade_in=first)
                if first:
                    first = False
                    if on_first_audio:
                        on_first_audio()
                pad = np.zeros(int(sr * max(item.pause_s, 0.0)), np.int16)
                self._write(stream, np.concatenate([pcm, pad]), cancelled)
            if stream is not None and not cancelled():
                self._write(stream, np.zeros(int(sr * TAIL_S), np.int16), cancelled)
                stream.stop()  # çalma tamponunu boşaltır
        finally:
            self._stream = None
            if stream is not None:
                try:
                    if cancelled():
                        stream.abort()
                    stream.close()
                except Exception:
                    log.debug("akış kapatılamadı", exc_info=True)

    @staticmethod
    def _write(stream, pcm: np.ndarray, cancelled) -> None:
        step = max(1, int(stream.samplerate * BLOCK_S))
        for i in range(0, len(pcm), step):
            if cancelled():
                return
            try:
                stream.write(np.ascontiguousarray(pcm[i:i + step]).reshape(-1, 1))
            except Exception:  # noqa: BLE001  (stop() akışı abort ettiyse PortAudioError)
                if cancelled():
                    return
                raise

    def stop(self) -> None:
        self._stop.set()
        stream = self._stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                log.debug("akış durdurulamadı", exc_info=True)
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:
            log.debug("sd.stop başarısız", exc_info=True)

    def beep(self) -> None:
        try:
            self.play(make_beep(), 16000)
        except Exception:
            log.debug("bip çalınamadı", exc_info=True)
