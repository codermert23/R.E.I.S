"""Sabah brifingi: konuşulacak metin YEREL olarak, Türkçe şablonla üretilir (0 LLM çağrısı).

Qt'den ve ağdan bağımsız mantık; hava durumu ve saat dışarıdan verilebilir (test için).
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from typing import Callable

from . import documents
from .checklist import is_done, parse_task
from .services import weather as weather_svc

log = logging.getLogger(__name__)

MAX_WORDS = 40
GRACE_MIN = 10  # başlamış ama 10 dakikadan az önce başlayan işler hâlâ "yaklaşan" sayılır
MAX_ITEMS = 2
MAX_LABEL_WORDS = 7
NOTES_PHRASE = "Proje notlarını açıyorum."
NOTHING_PHRASE = "Bugün yaklaşan bir şeyin yok."

_TIME = (
    r"(?P<h>\d{1,2})[:.](?P<m>\d{2})"
    r"(?:\s*[–—-]\s*(?P<h2>\d{1,2})[:.](?P<m2>\d{2}))?"
)
_LINE = re.compile(
    r"^\s*(?:(?:[-*+]|\d+[.)])\s+)?(?:\[[ xX]\]\s*)?[*_`~]*" + _TIME + r"(?!\d)"
)


@dataclass(frozen=True)
class TimedItem:
    start: dt.time
    label: str


@dataclass
class BriefingInput:
    now: dt.datetime
    weather: object | None = None  # weather.Weather | None
    daily_md: str = ""
    schedule_md: str = ""
    has_notes: bool = False
    address: str = ""  # hitap (ör. "patron"): selamlamaya eklenir


@dataclass
class Briefing:
    speech: str
    open_notes: bool


def _strip_md(s: str) -> str:
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)  # bağlantılar
    s = re.sub(r"\([^)]*\)", " ", s)  # parantez içi (oda kodu vb.) konuşmayı uzatır
    s = re.sub(r"[*_`~#>]+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" \t:-–—,.;")


def _valid(h: int, m: int) -> bool:
    return 0 <= h <= 23 and 0 <= m <= 59


def extract_items(markdown: str) -> tuple[list[TimedItem], int]:
    """(saatli açık maddeler, saatsiz açık görev sayısı). Biten/üstü çizili görevler sayılmaz."""
    items: list[TimedItem] = []
    untimed = 0
    in_fence = False
    for line in (markdown or "").split("\n"):
        st = line.strip()
        if st.startswith("```") or st.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence or not st or st.startswith("#"):
            continue
        task = parse_task(line)
        if task and is_done(line):
            continue
        body = task.group("body") if task else ""
        if body.strip().startswith("~~"):
            continue
        m = _LINE.match(line)
        if m and _valid(int(m.group("h")), int(m.group("m"))):
            label = _strip_md(line[m.end():])
            if not label:
                continue
            items.append(TimedItem(dt.time(int(m.group("h")), int(m.group("m"))), label))
        elif task and body.strip():
            untimed += 1
    return items, untimed


def upcoming(items: list[TimedItem], now: dt.datetime, limit: int = MAX_ITEMS) -> list[TimedItem]:
    """Başlangıcı (şimdi - 10 dk) veya sonrası olanlar, en yakın önce; aynı saatteki tekrarlar elenir."""
    floor = (now - dt.timedelta(minutes=GRACE_MIN)).time() if now.time() >= dt.time(0, GRACE_MIN) else dt.time(0, 0)
    seen: set[dt.time] = set()
    out: list[TimedItem] = []
    for it in sorted(items, key=lambda x: x.start):  # sort kararlı: günlük plan önce gelir
        if it.start < floor or it.start in seen:
            continue
        seen.add(it.start)
        out.append(it)
        if len(out) >= limit:
            break
    return out


def greeting(now: dt.datetime) -> str:
    h = now.hour
    if 6 <= h < 11:
        return "Günaydın"
    if 11 <= h < 18:
        return "İyi günler"
    if 18 <= h < 23:
        return "İyi akşamlar"
    return "Merhaba"


_BACK = set("aıou")
_FRONT = set("eiöü")
_HARD = set("çfhkpsşt")


def _lower_tr(s: str) -> str:
    return s.replace("İ", "i").replace("I", "ı").lower()


def locative(name: str) -> str:
    """Şehir adı + bulunma eki (İstanbul'da, İzmir'de, Ankara'da, Bursa'da): ünlü uyumu + sert ünsüz."""
    low = _lower_tr(name).strip()
    vowel = next((c for c in reversed(low) if c in _BACK or c in _FRONT), None)
    if vowel is None:
        return name
    t = "t" if low[-1] in _HARD else "d"
    return f"{name}'{t}{'a' if vowel in _BACK else 'e'}"


def weather_sentence(w, with_desc: bool = True) -> str:
    if w is None:
        return ""
    t = int(round(w.temp))
    temp = f"eksi {abs(t)}" if t < 0 else str(t)
    head = f"{locative(w.city)} {temp} derece" if w.city else f"Hava {temp} derece"
    desc = _lower_tr(w.description) if with_desc and w.description and w.description != "Bilinmiyor" else ""
    return f"{head}, {desc}." if desc else f"{head}."


def _item_sentence(it: TimedItem) -> str:
    label = " ".join(it.label.split()[:MAX_LABEL_WORDS])
    return f"Saat {it.start.strftime('%H:%M')}, {label}."


def _wc(text: str) -> int:
    return len(text.split())


def compose(now: dt.datetime, weather, items: list[TimedItem], untimed: int, open_notes: bool,
            max_items: int = MAX_ITEMS, max_words: int = MAX_WORDS, address: str = "") -> str:
    """Bütçeyi aşarsa sırayla atılır: saatsiz görev sayısı, hava açıklaması, fazla maddeler, hava."""
    chosen = items[:max_items]
    state = {"untimed": untimed > 0, "desc": True, "weather": weather is not None}

    def build() -> str:
        parts = [greeting(now) + (f" {address}" if address else "") + "."]
        if state["weather"]:
            parts.append(weather_sentence(weather, state["desc"]))
        if chosen:
            parts.extend(_item_sentence(i) for i in chosen)
            if state["untimed"]:
                parts.append(f"Ve {untimed} açık görevin var.")
        else:
            parts.append(NOTHING_PHRASE)
            if state["untimed"]:
                parts.append(f"{untimed} açık görevin var.")
        if open_notes:
            parts.append(NOTES_PHRASE)
        return " ".join(p for p in parts if p)

    text = build()
    for key in ("untimed", "desc"):
        if _wc(text) > max_words and state[key]:
            state[key] = False
            text = build()
    while _wc(text) > max_words and len(chosen) > 1:
        chosen = chosen[:-1]
        text = build()
    if _wc(text) > max_words and state["weather"]:
        state["weather"] = False
        text = build()
    if _wc(text) > max_words:  # son çare
        words = text.split()[:max_words]
        text = " ".join(words).rstrip(",;:") + "…"
    return text


def build_briefing(inp: BriefingInput, max_items: int = MAX_ITEMS) -> Briefing:
    d_items, d_untimed = extract_items(inp.daily_md)
    s_items, _ = extract_items(inp.schedule_md)
    items = upcoming(d_items + s_items, inp.now, limit=max(max_items, 1))
    speech = compose(inp.now, inp.weather, items, d_untimed, inp.has_notes, max_items,
                     address=(inp.address or "").strip())
    return Briefing(speech=speech, open_notes=inp.has_notes)


# ---------------------------------------------------------------- veri toplama (worker thread)
def get_weather(city: str, cache=None, fetch: Callable | None = None):
    """Önbellekten ya da ağdan (6 sn zaman aşımı). Hata olursa None: hava cümlesi atlanır."""
    cache = weather_svc.SHARED_CACHE if cache is None else cache
    fetch = fetch or weather_svc.fetch_weather
    city = (city or "").strip()
    if not city:
        return None
    cached = cache.get(city.casefold())
    if cached is not None:
        return cached
    try:
        w = fetch(city)
    except Exception as e:
        log.info("Brifing: hava durumu alınamadı (%s: %s)", type(e).__name__, e)
        return None
    cache.set(city.casefold(), w)
    return w


def briefing_projects(settings) -> list[dict]:
    """Brifingte açılacak projeler (briefing_projects boşsa hepsi)."""
    projs = [p for p in settings.projects if p.get("name")]
    chosen = {documents.fold(n) for n in (getattr(settings, "briefing_projects", None) or [])}
    if chosen:
        projs = [p for p in projs if documents.fold(p["name"]) in chosen]
    return projs


def gather(settings, now: dt.datetime | None = None, weather_fn: Callable | None = None) -> Briefing:
    now = now or dt.datetime.now()
    today = now.date()
    daily = documents.load_daily(settings.daily_path, today)
    sched = documents.load_schedule(settings.schedule_path, today)
    w = (weather_fn or get_weather)(settings.city)
    return build_briefing(BriefingInput(
        now=now, weather=w,
        daily_md=daily.markdown if daily.found else "",
        schedule_md=sched.markdown if sched.found else "",
        has_notes=bool(briefing_projects(settings)),
        address=settings.address,
    ))
