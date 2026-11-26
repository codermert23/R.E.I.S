"""Gemini TTS (kullanıcının mevcut Gemini anahtarıyla). Interactions API; çıktı ham PCM 24 kHz s16le."""
from __future__ import annotations

import base64
import logging
import re
import time
from typing import Callable, Iterable

import numpy as np

from .base import TTSError

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000
DEFAULT_MODEL = "gemini-3.8-flash-tts"
DEFAULT_VOICE = "Kore"
VOICES = ["Kore", "Puck", "Zephyr", "Charon", "Aoede", "Leda", "Fenrir", "Sulafat"]
# Anahtarın model listesinde TTS modelleri görünmeyebilir; bilinen adlar sırayla denenir.
FALLBACK_MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.1-flash-tts-preview",
                   "gemini-2.5-flash-preview-tts")
_RETRY_CODES = (429, 500, 503)
_VER = re.compile(r"gemini-(\d+(?:\.\d+)*)")


def pick_tts_model(names: Iterable[str]) -> str | None:
    """Model adları arasından en yüksek sürümlü '*-tts' modelini seç (flash, lite'a; kararlı, önizlemeye tercih)."""
    best = None
    for n in (x.removeprefix("models/") for x in names):
        if "tts" not in n:
            continue
        m = _VER.match(n)
        ver = tuple(int(x) for x in m.group(1).split(".")) if m else (0,)
        score = (ver, "preview" not in n, "lite" not in n, "pro" not in n)
        if best is None or score > best[0]:
            best = (score, n)
    return best[1] if best else None


def _code(e: Exception):
    return getattr(e, "code", None) or getattr(e, "status_code", None)


class GeminiTTS:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, voice: str = DEFAULT_VOICE,
                 on_model_changed: Callable[[str], None] | None = None, client=None):
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.voice = voice or DEFAULT_VOICE
        self.on_model_changed = on_model_changed
        self._client = client
        self._retry_delays = (1.0, 2.5)
        self._sleep = time.sleep

    def _get_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key, http_options={"api_version": "v1beta"})
        return self._client

    def _create(self, model: str, text: str):
        c = self._get_client()
        if not hasattr(c, "interactions"):
            raise TTSError("google-genai paketi eski; Gemini TTS için güncelle (pip install -U google-genai).")
        return c.interactions.create(
            model=model,
            input=[{"type": "user_input", "content": [{"type": "text", "text": text}]}],
            response_format={"type": "audio", "mime_type": "audio/l16", "sample_rate": SAMPLE_RATE},
            generation_config={"speech_config": [{"voice": self.voice}]},
        )

    def _create_retry(self, model: str, text: str):
        delays = self._retry_delays
        for i in range(len(delays) + 1):
            try:
                return self._create(model, text)
            except Exception as e:
                if _code(e) in _RETRY_CODES and i < len(delays):
                    log.warning("Gemini TTS yoğun/limit (%s); %.1f sn sonra yeniden.", _code(e), delays[i])
                    self._sleep(delays[i])
                    continue
                raise

    def _candidates(self) -> list[str]:
        out: list[str] = []
        try:
            found = pick_tts_model(m.name for m in self._get_client().models.list())
            if found:
                out.append(found)
        except Exception:
            log.debug("Gemini model listesi alınamadı", exc_info=True)
        out += [m for m in FALLBACK_MODELS]
        seen = {self.model}
        return [m for m in out if not (m in seen or seen.add(m))]

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        if not self.api_key:
            raise TTSError("Gemini API anahtarı girilmemiş (Ayarlar).")
        try:
            try:
                resp = self._create_retry(self.model, text)
            except Exception as e:
                if _code(e) not in (400, 404) or isinstance(e, TTSError):
                    raise
                resp = None
                for alt in self._candidates():
                    try:
                        resp = self._create_retry(alt, text)
                    except Exception as e2:
                        if _code(e2) in (400, 404):
                            continue
                        raise
                    log.warning("Gemini TTS modeli '%s' yok; '%s' kullanılıyor.", self.model, alt)
                    self.model = alt
                    if self.on_model_changed:
                        self.on_model_changed(alt)
                    break
                if resp is None:
                    raise TTSError(f"Gemini TTS modeli bulunamadı: '{self.model}'. Ayarlar'dan değiştir.") from e
        except TTSError:
            raise
        except Exception as e:
            code = _code(e)
            if code in (401, 403):
                raise TTSError("Gemini anahtarı geçersiz ya da TTS için yetkisiz.") from e
            if code == 429:
                raise TTSError("Gemini TTS ücretsiz kullanım sınırı doldu (429); biraz sonra dene.") from e
            if code in (500, 503):
                raise TTSError(f"Gemini TTS şu an yoğun ({code}).") from e
            raise TTSError(f"Gemini TTS'e ulaşılamadı: {type(e).__name__}") from e
        try:
            raw = base64.b64decode(resp.output_audio.data)
        except Exception as e:
            raise TTSError("Gemini TTS ses döndürmedi.") from e
        if len(raw) < 2:
            raise TTSError("Gemini TTS boş ses döndürdü.")
        return np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.int16), SAMPLE_RATE
