"""Konuşma metni normalizasyonu (tüm TTS sağlayıcıları için, senkron ve deterministik).

Altyazı/sohbet özgün metni gösterir; yalnızca seslendirmeye giden metin burada düzeltilir:
saatler, sayılar, yüzdeler, derece işareti Türkçe okunuşa çevrilir; markdown/emoji temizlenir.
"""
from __future__ import annotations

import re

from ..documents import TR_MONTHS

_ONES = ["sıfır", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
MAX_NUMBER = 99999


def number_to_words(n: int) -> str:
    """0-99999 arası tam sayı -> Türkçe okunuş. Aralık dışı: rakamlar olduğu gibi döner."""
    if n < 0 or n > MAX_NUMBER:
        return str(n)
    if n == 0:
        return _ONES[0]
    parts: list[str] = []
    thousands, rest = divmod(n, 1000)
    if thousands:
        parts.append("bin" if thousands == 1 else _under_1000(thousands) + " bin")
    if rest:
        parts.append(_under_1000(rest))
    return " ".join(parts)


def _under_1000(n: int) -> str:
    h, r = divmod(n, 100)
    out: list[str] = []
    if h:
        out.append("yüz" if h == 1 else _ONES[h] + " yüz")
    t, o = divmod(r, 10)
    if t:
        out.append(_TENS[t])
    if o:
        out.append(_ONES[o])
    return " ".join(out)


def clock_to_words(h: int, m: int) -> str:
    """14:00 -> 'on dört'; 14:30 -> 'on dört otuz'; 09:05 -> 'dokuz sıfır beş'."""
    hw = number_to_words(h)
    if m == 0:
        return hw
    if m < 10:
        return f"{hw} sıfır {_ONES[m]}"
    return f"{hw} {number_to_words(m)}"


_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF\U00002B00-\U00002BFF"
    "\U0000FE0F\U0000200D\U000020E3\U00002190-\U000021FF]")
_D = r"(?<![0-9A-Za-zÇĞİÖŞÜçğıöşü_])"  # sayının solunda harf/rakam yok (B204 korunur)
_DATE = re.compile(_D + r"(\d{1,2})[./](\d{1,2})[./](\d{4})(?![0-9])")
_TIME = re.compile(_D + r"(\d{1,2}):(\d{2})(?![0-9])")
_PCT_PRE = re.compile(r"%\s?(\d{1,5})(?![0-9])")
_PCT_POST = re.compile(_D + r"(\d{1,5})\s?%")
_DEG = re.compile(r"\s?°\s?[CcFf]?(?![A-Za-z])")
_MINUS_DEG = re.compile(r"(?<![0-9A-Za-zÇĞİÖŞÜçğıöşü])[-−–](?=\d{1,5}\s*(?:derece|°))")
_DECIMAL = re.compile(_D + r"(\d{1,5})[,.](\d{1,2})(?![0-9])")
_THOUSANDS = re.compile(_D + r"(\d{1,2})\.(\d{3})(?![0-9.,])")
_INT = re.compile(_D + r"(\d{1,5})(?![0-9])")


def _date(m: re.Match) -> str:
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= d <= 31 and 1 <= mo <= 12):
        return m.group(0)
    return f"{number_to_words(d)} {TR_MONTHS[mo - 1]} {number_to_words(y)}"


def _time(m: re.Match) -> str:
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return m.group(0)
    return clock_to_words(h, mi)


def _decimal(m: re.Match) -> str:
    frac = m.group(2)
    return f"{number_to_words(int(m.group(1)))} virgül {' '.join(_ONES[int(c)] for c in frac)}"


def _int(m: re.Match) -> str:
    return number_to_words(int(m.group(1)))


def strip_markup(text: str) -> str:
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # bağlantılar
    text = re.sub(r"^\s{0,3}(?:[-*+•]|\d+[.)])\s+", "", text, flags=re.M)  # madde imleri
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = _EMOJI.sub(" ", text)
    text = re.sub(r"[*`~#>|]+", " ", text)
    text = text.replace("_", " ")
    return text


def prepare_speech(text: str) -> str:
    """Seslendirmeye gidecek metni üretir (altyazı özgün metinle kalır)."""
    if not text:
        return ""
    t = strip_markup(text)
    t = _DATE.sub(_date, t)
    t = _TIME.sub(_time, t)
    t = _MINUS_DEG.sub("eksi ", t)
    t = _PCT_PRE.sub(lambda m: "yüzde " + number_to_words(int(m.group(1))), t)
    t = _PCT_POST.sub(lambda m: "yüzde " + number_to_words(int(m.group(1))), t)
    t = _DEG.sub(" derece", t)
    t = _THOUSANDS.sub(lambda m: number_to_words(int(m.group(1) + m.group(2))), t)
    t = _DECIMAL.sub(_decimal, t)
    t = _INT.sub(_int, t)
    t = re.sub(r"\s+[–—]\s+", ", ", t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t
