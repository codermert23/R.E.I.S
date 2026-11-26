"""Konuşma metnini cümle/yan cümle parçalarına böler (akışlı TTS için; Qt'siz, saf mantık).

Girdi `prepare_speech` çıktısıdır (sayılar zaten yazıyla). Yine de ondalık (3.5), saat (14:30), kısaltma
(Dr., vb.), sıra sayısı (3. gün), üç nokta ve Türkçe tırnaklar bozulmaz.
Kurallar: . ! ? … ; : sonrası boşluk -> sınır; çok uzun cümle virgüllerden; <3 kelimelik parça komşusuna katılır;
parça en fazla MAX_CHARS karakter.
"""
from __future__ import annotations

import re

MAX_CHARS = 160
MIN_WORDS = 3

# Sonrasında nokta cümle bitirmeyen kısaltmalar (küçük harf, noktasız).
ABBREVIATIONS = frozenset("""
dr prof doç yrd öğr üy sn av bkz vb vs vd yy örn sok cad mah müh uzm op bşk gen alb yzb ütğm apt blv hz mr mrs
""".split())

_CLOSERS = "\"'”’»)]}›"
_WORD_CHARS = re.compile(r"[0-9A-Za-zÇĞİÖŞÜçğıöşüÂÎÛâîû]")
_SENT_END = ".!?…;:"


class Chunk:
    """Bir parçanın sesi: pcm int16 mono, sr, parçadan sonraki doğal duraklama (sn)."""

    __slots__ = ("text", "pcm", "sr", "pause_s")

    def __init__(self, text: str, pcm, sr: int, pause_s: float):
        self.text, self.pcm, self.sr, self.pause_s = text, pcm, sr, pause_s


def pause_after(text: str) -> float:
    """Parça sağlayıcıdan ayrı üretildiği için aralarına konan doğal duraklama."""
    t = text.rstrip().rstrip(_CLOSERS)
    if t.endswith(("...", "…")):
        return 0.30
    if t[-1:] in ".!?":
        return 0.28
    if t[-1:] in ";:,":
        return 0.16
    return 0.10


def _is_abbrev(token: str) -> bool:
    return token.strip("(\"'“‘«[").rstrip(".").lower() in ABBREVIATIONS


def _sentence_split(text: str) -> list[str]:
    """Noktalama sınırlarında böler; kısaltma/ondalık/saat/sıra sayısı/baş harf korunur."""
    out: list[str] = []
    n = len(text)
    start = 0
    i = 0
    while i < n:
        ch = text[i]
        if ch not in _SENT_END:
            i += 1
            continue
        j = i + 1
        while j < n and text[j] in ".!?…":  # "...", "?!", "!!"
            j += 1
        while j < n and text[j] in _CLOSERS:  # ."  .”  .)
            j += 1
        at_end = j >= n
        if not at_end and not text[j].isspace():
            i = j  # 14.30, 3.5, 1:30, "a.b" -> boşluksuz: sınır değil
            continue
        run = text[i:j].rstrip(_CLOSERS)
        boundary = True
        if run != text[i:j] and text[j:].lstrip()[:1].islower():  # "gelirim." dedi: tırnaktan sonra cümle sürer
            boundary = False
        if boundary and ch == "." and run == ".":
            ws = text.rfind(" ", start, i) + 1
            token = text[ws:i]
            nxt = text[j:].lstrip()[:1]
            if _is_abbrev(token):
                boundary = False
            elif token.isdigit() and nxt and nxt.islower():  # "3. gün" sıra sayısı
                boundary = False
            elif len(token) == 1 and token.isalpha() and token.isupper() and nxt.isupper():  # "M. Kemal"
                boundary = False
        if boundary:
            out.append(text[start:j].strip())
            start = j
        i = j
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


def _hard_split(piece: str, max_chars: int) -> list[str]:
    """Virgülsüz uzun parçayı kelime sınırından böler."""
    out: list[str] = []
    cur = ""
    for w in piece.split():
        if cur and len(cur) + 1 + len(w) > max_chars:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
        while len(cur) > max_chars:  # tek kelime sınırı aşıyorsa (URL vb.)
            out.append(cur[:max_chars])
            cur = cur[max_chars:]
    if cur:
        out.append(cur)
    return out


def _split_long(sentence: str, max_chars: int) -> list[str]:
    """Sınırı aşan cümleyi virgüllerden, gerekirse kelimelerden bölerek en fazla max_chars'lık parçalar yapar."""
    if len(sentence) <= max_chars:
        return [sentence]
    clauses = [c.strip() for c in re.split(r"(?<=,)\s+", sentence) if c.strip()]
    out: list[str] = []
    cur = ""
    for c in clauses:
        if len(c) > max_chars:
            if cur:
                out.append(cur)
                cur = ""
            out.extend(_hard_split(c, max_chars))
            continue
        if cur and len(cur) + 1 + len(c) > max_chars:
            out.append(cur)
            cur = c
        else:
            cur = f"{cur} {c}".strip()
    if cur:
        out.append(cur)
    return out


def _words(s: str) -> int:
    return sum(1 for w in s.split() if _WORD_CHARS.search(w))


def split_chunks(text: str, max_chars: int = MAX_CHARS, min_words: int = MIN_WORDS) -> list[str]:
    """Konuşma metnini sırayla seslendirilecek parçalara böler. Boş metin -> []."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    pieces: list[str] = []
    for s in _sentence_split(text):
        pieces.extend(_split_long(s, max_chars))
    # çok kısa parçaları (<min_words) komşuyla birleştir (sığıyorsa; önce sonrakiyle, son parça öncekiyle)
    merged: list[str] = []
    carry = ""
    for p in pieces:
        cur = f"{carry} {p}".strip() if carry else p
        if _words(cur) < min_words:
            carry = cur
            continue
        merged.append(cur)
        carry = ""
    if carry:
        if merged and len(merged[-1]) + 1 + len(carry) <= max_chars:
            merged[-1] = f"{merged[-1]} {carry}"
        else:
            merged.append(carry)
    # birleştirme sonrası sınırı aşan (kısa+uzun) durum
    final: list[str] = []
    for m in merged:
        final.extend(_split_long(m, max_chars) if len(m) > max_chars else [m])
    return final
