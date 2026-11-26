"""ElevenLabs yardımcıları: modeller, ses listesi, kredi sayacı, hata sınıflandırma, metin kırpma.

Doğrulanan belgeler (Kasım 2026): Flash/Turbo modelleri API'de karakter başına 0,5 kredi, diğerleri 1;
ücretsiz planda Voice Library sesleri API ile kullanılamaz (yalnızca 'premade' ve hesaba ait sesler).
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import threading
import time
from dataclasses import dataclass, field

import httpx

from ..services.datefmt import format_day_month_tr
from .base import TTSError

log = logging.getLogger(__name__)

API = "https://api.elevenlabs.io"
VOICES_V2 = API + "/v2/voices"
VOICES_V1 = API + "/v1/voices"
SUBSCRIPTION = API + "/v1/user/subscription"

# model kimliği -> (açıklama, karakter başına kredi çarpanı)
MODELS: dict[str, tuple[str, float]] = {
    "eleven_flash_v2_5": ("eleven_flash_v2_5 — ucuz (yarı kredi), önerilen", 0.5),
    "eleven_multilingual_v2": ("eleven_multilingual_v2 — en kaliteli (tam kredi)", 1.0),
}
DEFAULT_MODEL = "eleven_flash_v2_5"
FREE_CATEGORIES = {"premade", "generated", "cloned"}  # hazır/hesaba ait sesler; kütüphane sesleri değil
REFRESH_TTL_S = 600.0
DEFAULT_MAX_CHARS = 250
DEFAULT_RESERVE = 300
TIMEOUT = 15.0


def credit_multiplier(model: str) -> float:
    """Bilinmeyen modelde güvenli tarafta kal: tam kredi (flash/turbo adı geçiyorsa yarım)."""
    if model in MODELS:
        return MODELS[model][1]
    return 0.5 if ("flash" in (model or "") or "turbo" in (model or "")) else 1.0


def estimate_cost(text: str, model: str) -> float:
    return len(text) * credit_multiplier(model)


# ------------------------------------------------------------------ hata sınıflandırma
class ElevenLabsError(TTSError):
    """code: auth | quota | paid_plan | rate | offline | http. notice: yedeğe geçince gösterilecek kısa mesaj."""

    def __init__(self, message: str, code: str = "http", notice: str = ""):
        super().__init__(message)
        self.code = code
        self.notice = notice


def _detail(resp: httpx.Response) -> tuple[str, str]:
    try:
        d = resp.json().get("detail")
    except Exception:
        return "", ""
    if isinstance(d, dict):
        return str(d.get("status", "")), str(d.get("message", ""))
    return "", str(d or "")


def error_from_response(resp: httpx.Response) -> ElevenLabsError:
    status, msg = _detail(resp)
    code = resp.status_code
    if code == 402 or status == "paid_plan_required":
        return ElevenLabsError(
            "Bu ses ücretsiz planda API ile kullanılamıyor (Voice Library sesi). Listeden 'ücretsiz' "
            "işaretli (premade) bir ses seç.", "paid_plan",
            "Bu ElevenLabs sesi ücretsiz planda kullanılamıyor, Edge sesine geçtim.")
    if status in ("quota_exceeded", "insufficient_credits"):
        return ElevenLabsError("ElevenLabs kredin bitti.", "quota",
                               "ElevenLabs kredisi bitti, Edge sesine geçtim.")
    if code in (401, 403):
        return ElevenLabsError("ElevenLabs API anahtarı geçersiz ya da yetkisiz (401).", "auth",
                               "ElevenLabs anahtarı geçersiz, Edge sesine geçtim.")
    if code == 429:
        return ElevenLabsError("ElevenLabs şu an çok fazla istek alıyor (429); biraz sonra tekrar dene.",
                               "rate", "ElevenLabs yoğun, Edge sesine geçtim.")
    return ElevenLabsError(f"ElevenLabs hatası ({code}). {msg}".strip(), "http")


def offline_error(e: Exception) -> ElevenLabsError:
    return ElevenLabsError("ElevenLabs'e ulaşılamadı; internet bağlantını kontrol et.", "offline")


# ------------------------------------------------------------------ ses listesi
_GENDER = {"female": "kadın", "male": "erkek", "neutral": "nötr"}


@dataclass(frozen=True)
class Voice:
    voice_id: str
    name: str
    category: str = ""
    labels: dict = field(default_factory=dict, compare=False, hash=False)
    languages: tuple = ()

    @property
    def free_ok(self) -> bool:
        return self.category in FREE_CATEGORIES

    def speaks_turkish(self) -> bool:
        lab = str(self.labels.get("language", "")).lower()
        return lab in ("tr", "turkish", "türkçe") or any(
            str(x).lower() in ("tr", "turkish") for x in self.languages)

    def display(self) -> str:
        bits = []
        g = str(self.labels.get("gender", "")).lower()
        if g:
            bits.append(_GENDER.get(g, g))
        for k in ("language", "accent", "age", "use_case", "descriptive"):
            v = self.labels.get(k)
            if v:
                bits.append(str(v).replace("_", " "))
        extra = f" ({', '.join(bits)})" if bits else ""
        tag = "ücretsiz ✓" if self.free_ok else "ücretli plan ✗"
        return f"{self.name}{extra} · {tag}"


def _sort_key(v: Voice):
    return (not v.free_ok, not v.speaks_turkish(), v.name.casefold())


def parse_voices(data: dict) -> list[Voice]:
    out: list[Voice] = []
    for v in (data or {}).get("voices", []) or []:
        vid = v.get("voice_id")
        if not vid:
            continue
        labels = v.get("labels") or {}
        langs = tuple(x.get("language", "") for x in (v.get("verified_languages") or []) if isinstance(x, dict))
        out.append(Voice(vid, v.get("name") or vid, v.get("category") or "", dict(labels), langs))
    out.sort(key=_sort_key)  # ücretsiz kullanılabilenler önce, sonra Türkçe bilenler, sonra ada göre
    return out


def preferred_voice(voices: list[Voice], current_id: str = "") -> Voice | None:
    """Mevcut seçim varsa o; yoksa ilk ücretsiz kullanılabilir (premade) ses."""
    for v in voices:
        if current_id and v.voice_id == current_id:
            return v
    for v in voices:
        if v.category == "premade":
            return v
    for v in voices:
        if v.free_ok:
            return v
    return voices[0] if voices else None


def fetch_voices(api_key: str, client: httpx.Client | None = None) -> list[Voice]:
    if not api_key:
        raise ElevenLabsError("Önce ElevenLabs API anahtarını gir.", "auth")
    c = client or httpx.Client(timeout=TIMEOUT)
    try:
        voices: list[Voice] = []
        token = None
        for _ in range(5):  # sayfalama sınırı: sonsuz döngü yok
            params = {"page_size": 100}
            if token:
                params["next_page_token"] = token
            r = c.get(VOICES_V2, params=params, headers={"xi-api-key": api_key})
            if r.status_code in (404, 405, 410) and not voices:  # eski uç noktaya dön
                r = c.get(VOICES_V1, headers={"xi-api-key": api_key})
                if r.status_code >= 400:
                    raise error_from_response(r)
                return parse_voices(r.json())
            if r.status_code >= 400:
                raise error_from_response(r)
            data = r.json()
            voices += parse_voices(data)
            token = data.get("next_page_token")
            if not data.get("has_more") or not token:
                break
        return sorted(voices, key=_sort_key)
    except httpx.HTTPError as e:
        raise offline_error(e) from e
    finally:
        if client is None:
            c.close()


# ------------------------------------------------------------------ kredi sayacı
@dataclass
class Usage:
    used: float
    limit: float
    reset_unix: int = 0
    tier: str = ""

    @property
    def remaining(self) -> float:
        return max(0.0, self.limit - self.used)


def parse_subscription(data: dict) -> Usage:
    return Usage(float(data.get("character_count", 0) or 0), float(data.get("character_limit", 0) or 0),
                 int(data.get("next_character_count_reset_unix", 0) or 0), str(data.get("tier", "") or ""))


def fetch_subscription(api_key: str, client: httpx.Client | None = None) -> Usage:
    if not api_key:
        raise ElevenLabsError("Önce ElevenLabs API anahtarını gir.", "auth")
    c = client or httpx.Client(timeout=TIMEOUT)
    try:
        r = c.get(SUBSCRIPTION, headers={"xi-api-key": api_key})
        if r.status_code >= 400:
            raise error_from_response(r)
        return parse_subscription(r.json())
    except httpx.HTTPError as e:
        raise offline_error(e) from e
    finally:
        if client is None:
            c.close()


def _thousands(n: float) -> str:
    return f"{int(round(n)):,}".replace(",", ".")


def format_usage(u: Usage, tz: dt.tzinfo | None = None) -> str:
    """'ElevenLabs: 2.400 / 10.000 karakter · yenilenme: 12 Aralık'"""
    s = f"ElevenLabs: {_thousands(u.used)} / {_thousands(u.limit)} karakter"
    if u.reset_unix:
        day = dt.datetime.fromtimestamp(u.reset_unix, tz).date()
        s += f" · yenilenme: {format_day_month_tr(day)}"
    return s


class UsageMeter:
    """Süreç genelinde paylaşılan kredi bilgisi (iş parçacığı güvenli)."""

    def __init__(self, clock=time.monotonic):
        self._lock = threading.Lock()
        self._clock = clock
        self.usage: Usage | None = None
        self.fetched_at: float | None = None
        self._busy = False
        self.listeners: list = []

    def set(self, u: Usage) -> None:
        with self._lock:
            self.usage = u
            self.fetched_at = self._clock()
        self._notify()

    def clear(self) -> None:
        with self._lock:
            self.usage = None
            self.fetched_at = None
        self._notify()

    def consume(self, cost: float) -> None:
        with self._lock:
            if self.usage is not None:
                self.usage.used += cost
        self._notify()

    def exhausted(self) -> None:
        with self._lock:
            if self.usage is not None:
                self.usage.used = self.usage.limit
        self._notify()

    def remaining(self) -> float | None:
        with self._lock:
            return None if self.usage is None else self.usage.remaining

    def snapshot(self) -> Usage | None:
        with self._lock:
            return None if self.usage is None else Usage(**self.usage.__dict__)

    def stale(self, ttl: float = REFRESH_TTL_S) -> bool:
        with self._lock:
            return self.fetched_at is None or self._clock() - self.fetched_at >= ttl

    def can_afford(self, cost: float, reserve: float) -> bool:
        """Bakiye bilinmiyorsa yeterli say; bilinen bakiye maliyetten ya da yedekten azsa False."""
        rem = self.remaining()
        return rem is None or not (rem < cost or rem < reserve)

    def refresh_async(self, fetcher, force: bool = False, on_done=None) -> bool:
        """Bayatsa (10 dk) ya da force ise arka planda yeniler. Başlattıysa True."""
        with self._lock:
            fresh = self.fetched_at is not None and self._clock() - self.fetched_at < REFRESH_TTL_S
            if self._busy or (fresh and not force):
                return False
            self._busy = True

        def work():
            try:
                self.set(fetcher())
            except Exception as e:
                log.info("ElevenLabs kullanım bilgisi alınamadı: %s", e)
            finally:
                with self._lock:
                    self._busy = False
                if on_done:
                    on_done()

        threading.Thread(target=work, daemon=True).start()
        return True

    def _notify(self) -> None:
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:
                log.exception("Kullanım dinleyicisi hatası")


METER = UsageMeter()


# ------------------------------------------------------------------ metin kırpma
def trim_for_cost(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """max_chars'ı aşan metni cümle, yoksa kelime sınırında keser."""
    text = (text or "").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    window = text[:max_chars]
    ends = [m.end() for m in re.finditer(r"[.!?…](?=\s|$)", window)]
    if ends and ends[-1] >= max_chars * 0.4:
        return window[: ends[-1]].strip()
    sp = window.rfind(" ")
    cut = window[:sp] if sp > max_chars * 0.4 else window
    return cut.rstrip(" ,;:-–—") + "."
