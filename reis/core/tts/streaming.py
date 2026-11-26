"""Akışlı konuşma: parçalar arka plan thread'inde sırayla üretilir (sınırlı kuyruk), oynatıcı ilk parça hazır
olur olmaz çalmaya başlar. İptal (yeni komut / F1) hem oynatmayı hem bekleyen üretimi durdurur; iptalden sonra
hiçbir eski parça çalınmaz. Qt'siz; oynatıcı ve TTS enjekte edilir (testlerde sahte)."""
from __future__ import annotations

import logging
import queue
import threading

from .base import TTSError
from .chunker import Chunk, pause_after

log = logging.getLogger(__name__)

QUEUE_MAX = 2  # en fazla bu kadar parça önden üretilir (RAM/CPU israfı olmasın)


def iter_synth(tts, texts):
    """(metin, pcm, sr) üretir. Sağlayıcı `synth_stream(texts)` sunuyorsa (FallbackTTS: parçalar arası yedek
    durumu korunur) onu, yoksa her parça için `synth`'i kullanır."""
    fn = getattr(tts, "synth_stream", None)
    if fn is not None:
        yield from fn(texts)
        return
    for t in texts:
        pcm, sr = tts.synth(t)
        yield t, pcm, sr


class SpeechStream:
    WAIT = object()  # get(): zaman aşımı, henüz parça yok
    END = object()  # get(): tüm parçalar bitti

    def __init__(self, tts, texts: list[str], cancel: threading.Event | None = None, maxsize: int = QUEUE_MAX):
        self._tts = tts
        self._texts = list(texts)
        self.cancel = cancel or threading.Event()
        self._q: queue.Queue = queue.Queue(maxsize=max(1, maxsize))
        self._thread: threading.Thread | None = None
        self._error: Exception | None = None
        self.produced = 0

    def start(self) -> "SpeechStream":
        self._thread = threading.Thread(target=self._produce, name="tts-chunks", daemon=True)
        self._thread.start()
        return self

    def _put(self, item) -> bool:
        while not self.cancel.is_set():
            try:
                self._q.put(item, timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    def _produce(self) -> None:
        try:
            for text, pcm, sr in iter_synth(self._tts, self._texts):
                if self.cancel.is_set():
                    return
                self.produced += 1
                if not self._put(Chunk(text, pcm, int(sr), pause_after(text))):
                    return
        except Exception as e:  # noqa: BLE001  (TTSError ya da beklenmeyen)
            if not self.cancel.is_set():
                self._error = e
        finally:
            self._put(self.END)

    def get(self, timeout: float = 0.05):
        """Chunk | WAIT | END. İptal edildiyse END. Üretim hatası, kuyruktaki parçalar bittikten sonra fırlatılır."""
        if self.cancel.is_set():
            return self.END
        try:
            item = self._q.get(timeout=timeout)
        except queue.Empty:
            return self.WAIT
        if item is self.END:
            if self._error is not None and not self.cancel.is_set():
                err, self._error = self._error, None
                raise err
            return self.END
        return item

    def close(self) -> None:
        """Bekleyen üretimi iptal eder (çalınmamış parçalar atılır)."""
        self.cancel.set()
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass


def run_speech(player, stream: SpeechStream, on_first_audio=None) -> bool:
    """Parçaları oynatıcıya verir (bu thread'de bloklar). True: hepsi çalındı; False: iptal edildi.
    Hata (TTSError vb.) fırlatır. Oynatıcı `play_stream` bilmiyorsa parça parça `play` çağrılır."""
    cancel = stream.cancel
    fired = {"v": False}

    def first():
        if not fired["v"]:
            fired["v"] = True
            if on_first_audio:
                on_first_audio()

    if hasattr(player, "play_stream"):
        player.play_stream(stream, on_first_audio=first)
        return not cancel.is_set()
    while True:
        item = stream.get(0.05)
        if item is SpeechStream.END:
            return not cancel.is_set()
        if item is SpeechStream.WAIT:
            continue
        if cancel.is_set():  # ayrıntı: get() ile play() arasında iptal gelmiş olabilir
            return False
        first()
        player.play(item.pcm, item.sr)


__all__ = ["SpeechStream", "run_speech", "iter_synth", "TTSError"]
