import datetime as dt
import json

import pytest

from reis.core.llm.base import LLMResponse
from reis.core.router import Router, RouterError, fast_path

NAMES = ["BallClash", "Reis Asistan"]


class FakeLLM:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def complete_json(self, system, user, schema):
        self.calls.append((system, user))
        o = self.outputs.pop(0)
        return LLMResponse(text=o if isinstance(o, str) else json.dumps(o), prompt_tokens=100, output_tokens=20)


def make(llm, summaries=None):
    summaries = summaries or {}
    return Router(llm, lambda: NAMES, lambda n: summaries.get(n, ""), today=lambda: dt.date(2026, 11, 25))


def test_fast_path_plan_schedule_project():
    llm = FakeLLM()
    r = make(llm)
    assert r.route("günlük planı göster").show[0].type == "daily_plan"
    assert r.route("Reis programı aç").show[0].type == "schedule"
    res = r.route("ballclash")
    assert res.show[0].name == "BallClash" and res.fast_path
    assert r.route("BallClash notlarını göster").show[0].name == "BallClash"
    assert llm.calls == []


def test_fast_path_does_not_catch_questions():
    assert fast_path("BallClash'te sıradaki iş ne?", NAMES) is None
    assert fast_path("bugünün planında ne var", NAMES) is None


def test_single_call_prompt_is_small_and_has_no_content():
    llm = FakeLLM({"speech": "Günaydın.", "show": [{"type": "daily_plan"}], "need_context": []})
    res = make(llm, {"BallClash": "GİZLİ İÇERİK"}).route("merhaba reis nasılsın")
    assert len(llm.calls) == 1 and res.llm_calls == 1
    assert "GİZLİ İÇERİK" not in llm.calls[0][1]
    assert "BallClash" in llm.calls[0][1] and "2026-11-25 Çarşamba" in llm.calls[0][1]
    assert res.prompt_tokens == 100 and res.show[0].type == "daily_plan"


def test_need_context_triggers_second_call_with_summary_only():
    llm = FakeLLM(
        {"speech": "Bakıyorum.", "show": [], "need_context": ["ballclash"]},
        {"speech": "Sıradaki iş seviye tasarımı.", "show": [], "need_context": []},
    )
    res = make(llm, {"BallClash": "# BallClash\nSıradaki: seviye tasarımı"}).route("BallClash'te sıradaki iş ne?")
    assert len(llm.calls) == 2 and res.llm_calls == 2
    assert "seviye tasarımı" in llm.calls[1][1]
    assert res.speech.startswith("Sıradaki")
    assert [s.name for s in res.show] == ["BallClash"]  # proje otomatik gösterilir


def test_speech_trimmed_to_40_words():
    long = " ".join(f"k{i}" for i in range(100))
    llm = FakeLLM({"speech": long, "show": [], "need_context": []})
    res = make(llm).route("bir şey anlat")
    assert len(res.speech.rstrip("…").split()) == 40


def test_unknown_project_dropped():
    llm = FakeLLM({"speech": "ok", "show": [{"type": "project", "name": "Yok"}], "need_context": ["Yok"]})
    res = make(llm).route("bir şey")
    assert res.show == [] and res.llm_calls == 1


@pytest.mark.parametrize("bad", ["bu json değil", '{"show": [{"type": "bilinmeyen"}]}'])
def test_invalid_json_raises(bad):
    with pytest.raises(RouterError):
        make(FakeLLM(bad)).route("merhaba")


def test_no_provider_raises_but_fast_path_works():
    r = Router(None, lambda: NAMES, lambda n: "")
    assert r.route("günlük plan").show[0].type == "daily_plan"
    with pytest.raises(RouterError):
        r.route("merhaba nasılsın")


def test_provider_exception_wrapped():
    class Boom:
        def complete_json(self, *a):
            raise RuntimeError("401")

    with pytest.raises(RouterError):
        make(Boom()).route("merhaba")
