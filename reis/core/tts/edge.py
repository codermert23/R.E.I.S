"""Microsoft Edge çevrimiçi nöral sesleri (edge-tts paketi, anahtar gerekmez).

Resmî olmayan bir uç noktadır; Microsoft değiştirirse çalışmayabilir. Çıktı MP3 akışıdır;
bellekte PyAV ile int16 PCM'e çözülür (ffmpeg gerekmez, geçici dosya yok).
"""
from __future__ import annotations

import asyncio
import io
import logging

import numpy as np

from .base import TTSError

log = logging.getLogger(__name__)

VOICES = {"tr-TR-EmelNeural": "Emel (kadın)", "tr-TR-AhmetNeural": "Ahmet (erkek)"}
DEFAULT_VOICE = "tr-TR-EmelNeural"
TIMEOUT_S = 8.0
OUT_RATE = 24000


def decode_audio(data: bytes, out_rate: int = OUT_RATE) -> tuple[np.ndarray, int]:
    """Kapsayıcılı ses baytlarını (MP3, WAV…) mono int16 PCM'e çevirir."""
    import av

    chunks: list[np.ndarray] = []
    try:
        with av.open(io.BytesIO(data)) as container:
            stream = container.streams.audio[0]
            rs = av.AudioResampler(format="s16", layout="mono", rate=out_rate)
            for frame in container.decode(stream):
                for f in rs.resample(frame):
                    chunks.append(f.to_ndarray().reshape(-1))
            for f in rs.resample(None):
                chunks.append(f.to_ndarray().reshape(-1))
    except Exception as e:
        raise TTSError(f"Edge sesi çözülemedi: {e}") from e
    if not chunks:
        raise TTSError("Edge TTS boş ses döndürdü.")
    return np.concatenate(chunks).astype(np.int16), out_rate


class EdgeTTS:
    def __init__(self, voice: str = DEFAULT_VOICE, timeout: float = TIMEOUT_S, communicate_factory=None):
        self.voice = voice or DEFAULT_VOICE
        self.timeout = timeout
        self._factory = communicate_factory  # testler için

    async def _fetch(self, text: str) -> bytes:
        if self._factory is not None:
            comm = self._factory(text, self.voice)
        else:
            import edge_tts

            comm = edge_tts.Communicate(text, self.voice, connect_timeout=int(self.timeout))
        buf = bytearray()
        async for chunk in comm.stream():
            if chunk.get("type") == "audio":
                buf += chunk["data"]
        return bytes(buf)

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        try:
            data = asyncio.run(asyncio.wait_for(self._fetch(text), self.timeout))
        except TTSError:
            raise
        except (asyncio.TimeoutError, TimeoutError) as e:
            raise TTSError("Edge TTS zaman aşımına uğradı (internet yavaş ya da kapalı).") from e
        except ImportError as e:
            raise TTSError("edge-tts paketi kurulu değil.") from e
        except Exception as e:
            name = type(e).__name__
            if name == "NoAudioReceived":
                raise TTSError("Edge TTS ses döndürmedi (uç nokta değişmiş olabilir).") from e
            if name in ("WebSocketError", "UnexpectedResponse", "UnknownResponse", "SkewAdjustmentError"):
                raise TTSError(f"Edge TTS hatası ({name}); uç nokta değişmiş olabilir.") from e
            raise TTSError("Edge TTS'e ulaşılamadı; internet bağlantını kontrol et.") from e
        if not data:
            raise TTSError("Edge TTS boş ses döndürdü.")
        return decode_audio(data)
