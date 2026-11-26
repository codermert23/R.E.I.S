from __future__ import annotations

import logging

from ...config import secrets
from ...config.settings import Settings, cache_dir, models_dir
from .base import TTSError, TTSProvider

log = logging.getLogger(__name__)

PROVIDERS = ("yazgi", "edge", "gemini", "piper", "elevenlabs")
YAZGI_MISSING_NOTICE = ("Yazgı sesi henüz kurulu değil; şimdilik Edge sesi kullanılıyor. Çevrimdışı, ücretsiz "
                        "yazgı sesi için Ayarlar > Ses > \"Ses motorunu kur\".")


def effective_provider(s: Settings) -> str:
    """Kayıtlı sağlayıcı bilinmiyorsa varsayılan yazgıya düşer."""
    return s.tts_provider if s.tts_provider in PROVIDERS else "yazgi"


def tts_key(s: Settings) -> tuple:
    from . import yazgi_provider

    return (effective_provider(s), s.piper_voice, s.edge_voice, s.gemini_tts_model, s.gemini_tts_voice,
            s.tts_cache_enabled, s.yazgi_voice, yazgi_provider.is_installed(), s.elevenlabs_voice_id,
            s.elevenlabs_model)


class FallbackTTS:
    """Birincil sağlayıcı hata verirse sırayla yedeklere (Edge, Piper...) düşer; her biri en çok bir kez
    denenir, döngü yok. Birincil hatanın `notice` alanı varsa, yedek başarılı olunca bir kez bildirilir."""

    def __init__(self, primary: TTSProvider, fallback: TTSProvider | None = None, *more: TTSProvider,
                 notify=None):
        self.primary = primary
        self.fallbacks = [f for f in (fallback, *more) if f is not None]
        self.fallback = self.fallbacks[0] if self.fallbacks else None
        self._notify = notify
        self._noticed: set[str] = set()

    def _announce(self, e: Exception, index: int) -> None:
        notice = getattr(e, "notice", "")
        if not notice or self._notify is None or notice in self._noticed:
            return
        self._noticed.add(notice)
        if index > 0:
            notice = notice.replace("Edge sesine", "Piper sesine")
        try:
            self._notify(notice)
        except Exception:
            log.exception("TTS bildirimi gösterilemedi")

    def synth(self, text: str):
        try:
            return self.primary.synth(text)
        except Exception as e:
            log.warning("TTS birincil sağlayıcı başarısız (%s); yedek deneniyor.", e)
            for i, fb in enumerate(self.fallbacks):
                try:
                    res = fb.synth(text)
                except Exception:
                    log.exception("TTS yedeği başarısız: %s", type(fb).__name__)
                    continue
                self._announce(e, i)
                return res
            if isinstance(e, TTSError):
                raise
            raise TTSError(f"Seslendirme başarısız: {e}") from e


    def synth_stream(self, texts):
        """Parça parça (metin, pcm, sr). Birincil parça k'de başarısız olursa kalan parçalar (k dahil) yedekle
        üretilir; önceki parçalar tekrar üretilmez/çalınmaz ve düşen sağlayıcı bu konuşma içinde bir daha denenmez."""
        chain = [self.primary, *self.fallbacks]
        level = 0
        first_error: Exception | None = None
        for text in texts:
            while True:
                try:
                    pcm, sr = chain[level].synth(text)
                except Exception as e:  # noqa: BLE001
                    if level == 0:
                        first_error = e
                        log.warning("TTS birincil sağlayıcı başarısız (%s); yedek deneniyor.", e)
                    else:
                        log.exception("TTS yedeği başarısız: %s", type(chain[level]).__name__)
                    level += 1
                    if level >= len(chain):
                        err = first_error or e
                        if isinstance(err, TTSError):
                            raise err
                        raise TTSError(f"Seslendirme başarısız: {err}") from err
                    continue
                if level > 0 and first_error is not None:
                    self._announce(first_error, level - 1)
                yield text, pcm, sr
                break


def _make_primary(s: Settings, provider: str) -> TTSProvider:
    if provider == "yazgi":
        from . import yazgi_provider

        if yazgi_provider.is_installed():
            return yazgi_provider.YazgiTTS(s.yazgi_voice)  # yerel ve ücretsiz: önbellek yok
        from .edge import EdgeTTS

        return EdgeTTS(s.edge_voice)  # kurulu değil: yine de konuşsun
    if provider == "gemini":
        from .gemini_tts import GeminiTTS

        def remember(model: str) -> None:
            from ...config.settings import save_settings

            s.gemini_tts_model = model
            try:
                save_settings(s)
            except Exception:
                log.exception("Gemini TTS model adı kaydedilemedi")

        g = GeminiTTS(secrets.get_secret(secrets.GEMINI), s.gemini_tts_model, s.gemini_tts_voice,
                      on_model_changed=remember)
        if not s.tts_cache_enabled:
            return g
        from .cache import AudioCache, CachedTTS

        return CachedTTS(g, AudioCache(cache_dir()), "gemini", lambda: (g.voice, g.model))
    if provider == "piper":
        from .piper_tts import PiperTTS

        return PiperTTS(s.piper_voice, models_dir())
    if provider == "elevenlabs":
        from .elevenlabs_tts import CreditGuardTTS, ElevenLabsTTS

        return CreditGuardTTS(ElevenLabsTTS(secrets.get_secret(secrets.ELEVENLABS), s.elevenlabs_voice_id,
                                            s.elevenlabs_model))  # kredi koruması: kısaltır, bakiye yetmezse yedeğe düşer
    from .edge import EdgeTTS

    return EdgeTTS(s.edge_voice)


def make_tts(s: Settings, notify=None) -> TTSProvider:
    """notify(mesaj): yedeğe geçilince / yazgı kurulu değilken tek seferlik bildirim (worker thread'den çağrılabilir).
    Zincir: yazgı -> Edge -> Piper (kuruluysa); her halka en çok bir kez denenir."""
    provider = effective_provider(s)
    primary = _make_primary(s, provider)
    if provider == "yazgi" and not _yazgi_ready() and notify is not None and not s.yazgi_notice_shown:
        s.yazgi_notice_shown = True
        try:
            from ...config.settings import save_settings

            save_settings(s)
        except Exception:
            log.exception("Ayarlar kaydedilemedi")
        try:
            notify(YAZGI_MISSING_NOTICE)
        except Exception:
            log.exception("TTS bildirimi gösterilemedi")
    if provider == "piper":
        return primary
    from .piper_tts import PiperTTS, is_installed

    chain: list[TTSProvider] = []
    if provider == "elevenlabs" or (provider == "yazgi" and _yazgi_ready()):
        from .edge import EdgeTTS

        chain.append(EdgeTTS(s.edge_voice))
    if is_installed(s.piper_voice, models_dir()):
        chain.append(PiperTTS(s.piper_voice, models_dir()))
    if not chain:
        return primary
    return FallbackTTS(primary, *chain, notify=notify)


def _yazgi_ready() -> bool:
    from . import yazgi_provider

    return yazgi_provider.is_installed()


__all__ = ["TTSError", "TTSProvider", "FallbackTTS", "make_tts", "tts_key", "PROVIDERS", "effective_provider"]
