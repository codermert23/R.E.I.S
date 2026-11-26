from __future__ import annotations

import logging

import httpx
import numpy as np

from .base import TTSError
from .elevenlabs_api import (DEFAULT_MAX_CHARS, DEFAULT_RESERVE, METER, ElevenLabsError, UsageMeter,
                             error_from_response, estimate_cost, fetch_subscription, offline_error,
                             trim_for_cost)

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000
URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


class ElevenLabsTTS:
    def __init__(self, api_key: str, voice_id: str, model: str = "eleven_flash_v2_5",
                 client: httpx.Client | None = None):
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model
        self._client = client

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        if not self.api_key:
            raise ElevenLabsError("ElevenLabs API anahtarı girilmemiş (Ayarlar).", "auth",
                                  "ElevenLabs anahtarı girilmemiş, Edge sesine geçtim.")
        if not self.voice_id:
            raise ElevenLabsError("ElevenLabs ses kimliği (voice id) girilmemiş (Ayarlar).", "auth",
                                  "ElevenLabs sesi seçilmemiş, Edge sesine geçtim.")
        client = self._client or httpx.Client(timeout=30)
        try:
            r = client.post(
                URL.format(voice_id=self.voice_id),
                params={"output_format": f"pcm_{SAMPLE_RATE}"},
                headers={"xi-api-key": self.api_key},
                json={"text": text, "model_id": self.model},
            )
        except httpx.HTTPError as e:
            raise offline_error(e) from e
        finally:
            if self._client is None:
                client.close()
        if r.status_code >= 400:
            raise error_from_response(r)
        data = r.content
        if len(data) < 2:
            raise TTSError("ElevenLabs boş ses döndürdü.")
        return np.frombuffer(data[: len(data) // 2 * 2], dtype="<i2").astype(np.int16), SAMPLE_RATE


class CreditGuardTTS:
    """Kredi güvenliği: metni kısaltır, önbelleğe bakar, bakiye yetmiyorsa ElevenLabsError(quota) atar
    (FallbackTTS bunu yakalayıp Edge/Piper'a düşer). Önbellek isabeti kredi harcamaz."""

    def __init__(self, eleven: ElevenLabsTTS, cached=None, meter: UsageMeter | None = None,
                 max_chars: int = DEFAULT_MAX_CHARS, reserve: int = DEFAULT_RESERVE):
        self.eleven = eleven
        self.cached = cached
        self.meter = meter or METER
        self.max_chars = max_chars
        self.reserve = reserve

    def synth(self, text: str):
        text = trim_for_cost(text, self.max_chars)
        if self.cached is not None:
            hit = self.cached.peek(text)
            if hit is not None:
                return hit
        cost = estimate_cost(text, self.eleven.model)
        if not self.meter.can_afford(cost, self.reserve):
            raise ElevenLabsError("ElevenLabs kredisi yetersiz.", "quota",
                                  "ElevenLabs kredisi bitti, Edge sesine geçtim.")
        try:
            res = (self.cached or self.eleven).synth(text)
        except ElevenLabsError as e:
            if e.code == "quota":
                self.meter.exhausted()
            raise
        self.meter.consume(cost)
        key = self.eleven.api_key
        self.meter.refresh_async(lambda: fetch_subscription(key))  # yalnızca 10 dk'dan eskiyse çalışır
        return res
