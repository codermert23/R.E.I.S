from __future__ import annotations

import logging
import re
import time
from typing import Callable, Iterable

from .base import LLMResponse

log = logging.getLogger(__name__)

_BAD_PARTS = ("image", "tts", "live", "audio", "thinking", "preview", "exp", "vision", "embedding", "robotics",
              "computer", "native", "learnlm", "gemma")
_OVERLOAD_CODES = (500, 503)
_FLASH = re.compile(r"^gemini-(?P<ver>\d+(?:\.\d+)*)-flash(?P<lite>-lite)?$")


def pick_model(names: Iterable[str]) -> str | None:
    """Kullanılabilir model adları arasından hızlı/ucuz uygun olanı seç.

    Önce flash-lite, sonra flash; aynı türde en yüksek sürüm. Önizleme/deneysel/ses/görsel
    modeller elenir. Hiçbiri kalmazsa 'gemini-flash-lite-latest' / 'gemini-flash-latest' takma adları denenir.
    """
    clean = [n.removeprefix("models/") for n in names]
    best: tuple | None = None
    for n in clean:
        if any(b in n for b in _BAD_PARTS):
            continue
        m = _FLASH.match(n)
        if not m:
            continue
        ver = tuple(int(x) for x in m.group("ver").split("."))
        score = (1 if m.group("lite") else 0, ver)
        if best is None or score > best[0]:
            best = (score, n)
    if best:
        return best[1]
    for alias in ("gemini-flash-lite-latest", "gemini-flash-latest"):
        if alias in clean:
            return alias
    return None


def uses_thinking_level(model: str) -> bool:
    """Gemini 3 ve üstü (ve *-latest takma adları) thinking_level ister; 2.x thinking_budget ister."""
    m = re.match(r"^(?:models/)?gemini-(\d+)", model)
    if m:
        return int(m.group(1)) >= 3
    return "latest" in model


def _is_not_found(e: Exception) -> bool:
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    return code == 404 or "NOT_FOUND" in str(e)


class GeminiProvider:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash-lite", max_output_tokens: int = 150,
                 on_model_changed: Callable[[str], None] | None = None):
        self.api_key = api_key
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.on_model_changed = on_model_changed
        self._client = None
        self._thinking_idx: int | None = None
        self._retry_delays = (1.0, 2.5)  # 503'te yeniden deneme bekleme süreleri (sn)
        self._sleep = time.sleep

    def _get_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _available_models(self) -> list[str]:
        out = []
        for m in self._get_client().models.list():
            actions = getattr(m, "supported_actions", None)
            if actions is None or "generateContent" in actions:
                out.append(m.name)
        return out

    def _generate(self, user: str, config):
        return self._get_client().models.generate_content(model=self.model, contents=user, config=config)

    def _thinking_variants(self) -> list:
        """Düşünmeyi kapatma biçimleri, bu modele uygun olandan başlayarak.

        Gemini 2.x: thinking_budget=0. Gemini 3+ (ve *-latest takma adları): thinking_level=MINIMAL;
        3+ modellerde thinking_budget=0 geçersiz argüman (400) döner. Son çare: hiç ayar gönderme.
        """
        from google.genai import types

        budget = types.ThinkingConfig(thinking_budget=0)
        level = types.ThinkingConfig(thinking_level="MINIMAL")
        order = [level, budget] if uses_thinking_level(self.model) else [budget, level]
        if self._thinking_idx is not None:  # daha önce çalışan biçimi tekrar kullan
            order = [order[self._thinking_idx]] + [o for i, o in enumerate(order) if i != self._thinking_idx]
        return order + [None]

    def _generate_with_thinking_fallback(self, system: str, user: str, schema: dict):
        from google.genai import types

        variants = self._thinking_variants()
        last: Exception | None = None
        for tc in variants:
            kwargs = dict(
                system_instruction=system,
                response_mime_type="application/json",
                response_json_schema=schema,
                max_output_tokens=self.max_output_tokens,
                temperature=0.3,
            )
            if tc is not None:
                kwargs["thinking_config"] = tc
            try:
                resp = self._generate(user, types.GenerateContentConfig(**kwargs))
                if tc is not None:
                    pref = [types.ThinkingConfig(thinking_level="MINIMAL"), types.ThinkingConfig(thinking_budget=0)]
                    if not uses_thinking_level(self.model):
                        pref.reverse()
                    self._thinking_idx = pref.index(tc) if tc in pref else None
                return resp
            except Exception as e:
                last = e
                if getattr(e, "code", None) != 400:
                    raise  # 404/401/429/ağ hataları başka yerde ele alınır
        assert last is not None
        raise last

    def _generate_resilient(self, system: str, user: str, schema: dict):
        """Geçici sunucu yoğunluğunda (500/503) kısa beklemeyle yeniden dene; olmazsa başka modele geç.

        Başka modele geçiş yalnızca bu istek için geçerlidir, ayarlara kaydedilmez.
        """
        delays = self._retry_delays
        for i in range(len(delays) + 1):
            try:
                return self._generate_with_thinking_fallback(system, user, schema)
            except Exception as e:
                if getattr(e, "code", None) not in _OVERLOAD_CODES:
                    raise
                if i < len(delays):
                    log.warning("Gemini yoğun (%s), %.1f sn sonra yeniden denenecek.", e.code, delays[i])
                    self._sleep(delays[i])
                    continue
                overload = e
        # Aynı model hâlâ yoğun: başka bir uygun modeli bir kez dene.
        current = self.model
        try:
            names = [n for n in self._available_models() if n.removeprefix("models/") != current]
            alt = pick_model(names)
        except Exception:
            alt = None
        if alt:
            log.warning("Gemini '%s' yoğun; bu istek için '%s' deneniyor.", current, alt)
            self.model, saved_idx = alt, self._thinking_idx
            self._thinking_idx = None
            try:
                return self._generate_with_thinking_fallback(system, user, schema)
            except Exception as e2:
                if getattr(e2, "code", None) not in _OVERLOAD_CODES:
                    raise
            finally:
                self.model, self._thinking_idx = current, saved_idx
        raise RuntimeError(
            "Gemini şu an çok yoğun (503). Birkaç saniye sonra tekrar dene."
        ) from overload

    def complete_json(self, system: str, user: str, schema: dict) -> LLMResponse:
        try:
            resp = self._generate_resilient(system, user, schema)
        except Exception as e:
            if not _is_not_found(e):
                raise
            # Model adı artık yok / bu anahtarda yok: geçerli modellerden uygun olanı bul, bir kez dene.
            old = self.model
            new = pick_model(self._available_models())
            if not new or new == old:
                raise RuntimeError(
                    f"Gemini modeli bulunamadı: '{old}'. Ayarlar'dan geçerli bir model adı gir."
                ) from e
            log.warning("Gemini modeli '%s' bulunamadı; '%s' kullanılıyor.", old, new)
            self.model = new
            self._thinking_idx = None
            resp = self._generate_resilient(system, user, schema)
            if self.on_model_changed:
                self.on_model_changed(new)
        um = getattr(resp, "usage_metadata", None)
        pt = int(getattr(um, "prompt_token_count", 0) or 0)
        ot = int(getattr(um, "candidates_token_count", 0) or 0)
        log.info("Gemini kullanım: girdi=%d çıktı=%d (model=%s)", pt, ot, self.model)
        return LLMResponse(text=resp.text or "", prompt_tokens=pt, output_tokens=ot)
