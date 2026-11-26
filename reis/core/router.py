"""Token-minimal yönlendirici: hızlı yol (0 token) -> 1. çağrı -> gerekirse 2. çağrı."""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from .documents import fold, weekday_name

log = logging.getLogger(__name__)

MAX_SPEECH_WORDS = 40
MAX_CONTEXT_PROJECTS = 3

SYSTEM_PROMPT = (
    "Sen Reis, kısa konuşan Türkçe sesli asistansın. Yalnızca JSON döndür.\n"
    "speech: en fazla 30 kelime, doğal ve kısa.\n"
    "show: ekranda gösterilecekler; type project (name ile), daily_plan, schedule veya briefing (günün özeti/brifing isteği).\n"
    "Kullanıcı bir projenin içeriğini soruyorsa show'a projeyi ekle, need_context'e proje adını yaz, "
    "speech'e kısa bir 'bakıyorum' yaz. Proje adı listedeyse tam adı kullan."
)
SYSTEM_PROMPT_2 = (
    "Sen Reis, Türkçe sesli asistansın. Verilen not özetine göre soruyu en fazla 30 kelimeyle yanıtla. "
    "Yalnızca JSON döndür. need_context boş kalsın."
)

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "speech": {"type": "string"},
        "show": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["project", "daily_plan", "schedule", "briefing"]},
                    "name": {"type": "string"},
                },
                "required": ["type"],
            },
        },
        "need_context": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["speech", "show", "need_context"],
}


class RouterError(Exception):
    pass


class ShowItem(BaseModel):
    type: Literal["project", "daily_plan", "schedule", "briefing"]
    name: str | None = None


class LLMOutput(BaseModel):
    speech: str = ""
    show: list[ShowItem] = Field(default_factory=list)
    need_context: list[str] = Field(default_factory=list)


@dataclass
class RouteResult:
    speech: str
    show: list[ShowItem] = field(default_factory=list)
    llm_calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    fast_path: bool = False
    action: str | None = None  # "briefing": konuşma yerel şablonla üretilir (LLM'siz)


def trim_words(text: str, limit: int = MAX_SPEECH_WORDS) -> str:
    words = text.split()
    if len(words) <= limit:
        return text.strip()
    return " ".join(words[:limit]).rstrip(",;:") + "…"


# ---------------------------------------------------------------- hızlı yol
_TOKEN_SPLIT = re.compile(r"[^\w]+", re.UNICODE)
_FILLER = {
    "reis", "lutfen", "bana", "bir", "su", "hadi", "ya", "de", "da",
    "goster", "ac", "getir", "gosterir", "misin", "misiniz", "acar", "mi", "ekrana",
    "proje", "projesi", "projeyi", "projenin", "rapor", "raporu", "raporunu",
    "not", "notu", "notlari", "notlarini", "notlar",
}
_PLAN_WORDS = {"gunluk", "plan", "plani", "planimi", "bugunun", "bugun", "planim", "planini"}
_SCHEDULE_WORDS = {"ders", "is", "program", "programi", "programimi", "bugunun", "bugun", "programim",
                   "programini", "calisma"}


_BRIEF_FILLER = {"reis", "lutfen", "bana", "bir", "hadi", "ya", "sabah", "ver", "goster", "anlat", "ac", "misin"}
_BRIEF_PHRASES = {("gunaydin",), ("gunun", "ozeti"), ("gunun", "ozetini"), ("bugun", "neler", "var")}


def _is_briefing(toks: list[str]) -> bool:
    if any(t.startswith("brifing") for t in toks):
        return all(t.startswith("brifing") or t in _BRIEF_FILLER for t in toks)
    core = tuple(t for t in toks if t not in _BRIEF_FILLER)
    return core in _BRIEF_PHRASES


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(fold(text)) if t]


def fast_path(text: str, project_names: list[str]) -> RouteResult | None:
    toks = _tokens(text)
    if not toks or len(toks) > 8:
        return None
    if _is_briefing(toks):
        return RouteResult(speech="", action="briefing", fast_path=True)
    rest = [t for t in toks if t not in _FILLER]
    if not rest:
        return None
    # proje adı birebir
    for name in project_names:
        if rest == _tokens(name):
            return RouteResult(
                speech=f"{name} notlarını gösteriyorum.",
                show=[ShowItem(type="project", name=name)], fast_path=True,
            )
    if "plan" in " ".join(rest) and all(t in _PLAN_WORDS for t in rest):
        return RouteResult(speech="Günlük planı gösteriyorum.",
                           show=[ShowItem(type="daily_plan")], fast_path=True)
    if any(t.startswith("program") for t in rest) and all(t in _SCHEDULE_WORDS for t in rest):
        return RouteResult(speech="Programı gösteriyorum.",
                           show=[ShowItem(type="schedule")], fast_path=True)
    return None


# ---------------------------------------------------------------- yönlendirici
class Router:
    def __init__(
        self,
        provider,
        project_names: Callable[[], list[str]],
        project_summary: Callable[[str], str],
        today: Callable[[], dt.date] = dt.date.today,
        address: Callable[[], str] = lambda: "",
    ):
        self.provider = provider
        self._names = project_names
        self._summary = project_summary
        self._today = today
        self._address = address

    def build_user_prompt(self, text: str, names: list[str]) -> str:
        d = self._today()
        addr = (self._address() or "").strip()
        hitap = f"Hitap: kullanıcıya '{addr}' diye hitap et\n" if addr else ""
        return (
            f"Bugün: {d.isoformat()} {weekday_name(d)}\n"
            f"{hitap}"
            f"Projeler: {', '.join(names) if names else '-'}\n"
            f"Kullanıcı: {text}"
        )

    def _call(self, system: str, user: str) -> tuple[LLMOutput, int, int]:
        if self.provider is None:
            raise RouterError("Gemini API anahtarı ayarlanmamış (Ayarlar > Anahtarlar).")
        try:
            resp = self.provider.complete_json(system, user, ROUTE_SCHEMA)
        except RouterError:
            raise
        except Exception as e:  # ağ, kota, anahtar...
            raise RouterError(f"LLM çağrısı başarısız: {e}") from e
        try:
            data = json.loads(resp.text)
            out = LLMOutput.model_validate(data)
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            raise RouterError(f"Geçersiz LLM çıktısı: {e}") from e
        return out, resp.prompt_tokens, resp.output_tokens

    def _canonical(self, items: list[ShowItem], names: list[str]) -> list[ShowItem]:
        by_fold = {fold(n): n for n in names}
        result: list[ShowItem] = []
        for it in items:
            if it.type == "project":
                canon = by_fold.get(fold(it.name or ""))
                if canon is None:
                    continue
                it = ShowItem(type="project", name=canon)
            else:
                it = ShowItem(type=it.type)
            if it not in result:
                result.append(it)
        return result

    def route(self, text: str) -> RouteResult:
        names = list(self._names())
        fast = fast_path(text, names)
        if fast:
            return fast

        out, pt, ot = self._call(SYSTEM_PROMPT, self.build_user_prompt(text, names))
        calls = 1
        show = self._canonical(out.show, names)
        speech = out.speech
        if any(i.type == "briefing" for i in show):  # LLM brifing dedi: konuşmayı yerel şablon üretir
            return RouteResult(speech="", llm_calls=calls, prompt_tokens=pt, output_tokens=ot, action="briefing")

        by_fold = {fold(n): n for n in names}
        wanted = []
        for n in out.need_context:
            canon = by_fold.get(fold(n))
            if canon and canon not in wanted:
                wanted.append(canon)
        wanted = wanted[:MAX_CONTEXT_PROJECTS]
        if wanted:
            parts = []
            for n in wanted:
                s = self._summary(n)
                if s:
                    parts.append(f"[{n}]\n{s}")
                if not any(i.type == "project" and i.name == n for i in show):
                    show.append(ShowItem(type="project", name=n))
            if parts:
                user2 = "Not özeti:\n" + "\n\n".join(parts) + f"\nSoru: {text}"
                out2, pt2, ot2 = self._call(SYSTEM_PROMPT_2, user2)
                calls += 1
                pt += pt2
                ot += ot2
                if out2.speech.strip():
                    speech = out2.speech
        log.info("Router: %d çağrı, girdi=%d çıktı=%d token", calls, pt, ot)
        return RouteResult(speech=trim_words(speech), show=show, llm_calls=calls,
                           prompt_tokens=pt, output_tokens=ot)
