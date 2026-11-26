"""Brifing: router hızlı yolu, pipeline akışı ve sekmeli not penceresi."""
import datetime as dt
import json

import pytest
from PySide6.QtCore import Qt

from reis.config.settings import Settings
from reis.core import briefing
from reis.core.llm.base import LLMResponse
from reis.core.router import ROUTE_SCHEMA, SYSTEM_PROMPT, Router, fast_path
from reis.core.state import AssistantState
from reis.ui.docs_window import DocsWindow
from test_pipeline import FakePlayer, FakeRouter, FakeTTS, make as make_pipe
from reis.core.router import RouteResult
from reis.core.tts.base import TTSError

NAMES = ["BallClash", "Reis Asistan"]


class FakeLLM:
    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), []

    def complete_json(self, system, user, schema):
        self.calls.append((system, user))
        return LLMResponse(text=json.dumps(self.outputs.pop(0)), prompt_tokens=100, output_tokens=20)


def router(llm):
    return Router(llm, lambda: NAMES, lambda n: "", today=lambda: dt.date(2026, 11, 25))


@pytest.mark.parametrize("phrase", [
    "günaydın", "Günaydın Reis", "GÜNAYDIN", "günaydın reis", "brifing ver", "Brifing ver", "sabah brifingi",
    "Sabah Brifingi", "günün özeti", "GÜNÜN ÖZETİ", "bugün neler var", "BUGÜN NELER VAR?", "bana brifing ver",
    "günün özetini ver", "Reis, sabah brifingi ver",
])
def test_briefing_fast_path_zero_llm_calls(phrase):
    llm = FakeLLM()
    res = router(llm).route(phrase)
    assert res.action == "briefing" and res.fast_path and res.llm_calls == 0 and res.speech == ""
    assert llm.calls == []


def test_briefing_phrases_dont_swallow_other_requests():
    assert fast_path("bugün neler yaptım", NAMES) is None
    assert fast_path("günaydın BallClash'te ne var", NAMES) is None
    assert fast_path("bugünün planı", NAMES).action is None


def test_llm_briefing_show_type_routes_to_action():
    llm = FakeLLM({"speech": "tamam", "show": [{"type": "briefing"}], "need_context": []})
    res = router(llm).route("bana bugünle ilgili genel bir şeyler anlat bakalım")
    assert res.action == "briefing" and res.llm_calls == 1 and res.speech == ""


def test_router_input_stays_under_token_budget():
    llm = FakeLLM({"speech": "x", "show": [], "need_context": []})
    router(llm).route("bir şey söyle bana lütfen şimdi")
    system, user = llm.calls[0]
    assert len(system + user + json.dumps(ROUTE_SCHEMA)) / 3 < 400  # kaba: ~3 karakter/token
    assert "briefing" in SYSTEM_PROMPT


# ---------------------------------------------------------------- pipeline
def _briefing_pipe(qapp, monkeypatch, tts=None, speech="Günaydın. Saat 14:00, Ders. Proje notlarını açıyorum."):
    monkeypatch.setattr(briefing, "gather", lambda s: briefing.Briefing(speech, True))
    p, bus, states, subs, shown = make_pipe(qapp, RouteResult(speech="", action="briefing"), tts=tts)
    p.settings.projects = [{"name": "A", "path": "x"}, {"name": "B", "path": "y"}]
    opened, said, order = [], [], []
    p.briefing_notes_requested.connect(lambda ps: (opened.append(ps), order.append(states[-1])))
    p.reis_said.connect(said.append)
    return p, bus, states, shown, opened, said, order


def test_pipeline_briefing_opens_notes_when_speech_starts(qapp, pump, monkeypatch):
    tts = FakeTTS()
    p, bus, states, shown, opened, said, order = _briefing_pipe(qapp, monkeypatch, tts)
    p.submit_text("günaydın")
    assert pump(lambda: states and states[-1] is AssistantState.IDLE and len(states) >= 3)
    assert states[0] is AssistantState.THINKING and states[-1] is AssistantState.IDLE
    assert AssistantState.SPEAKING in states
    assert [i.type for i in shown[0]] == ["daily_plan"]
    from reis.core.tts.textprep import prepare_speech

    # sohbet/altyazı özgün metni, TTS ise normalleştirilmiş metni alır
    assert said[0].startswith("Günaydın") and "14:00" in said[0]
    # konuşma cümle parçalarına bölünür (akışlı TTS); parçalar birleşince normalleştirilmiş metin elde edilir
    assert " ".join(tts.texts) == prepare_speech(said[0]) and "on dört" in tts.texts[0]
    assert [p_["name"] for p_ in opened[0]] == ["A", "B"]
    assert order == [AssistantState.SPEAKING]  # konuşma başlarken açıldı, bitince değil


def test_pipeline_briefing_tts_failure_still_opens_notes(qapp, pump, monkeypatch):
    p, bus, states, shown, opened, said, order = _briefing_pipe(qapp, monkeypatch, FakeTTS(exc=TTSError("ses yok")))
    p.start_briefing()
    assert pump(lambda: AssistantState.ERROR in states)
    assert len(opened) == 1 and len(shown) == 1


# ---------------------------------------------------------------- pencere
def test_tabbed_window_two_projects(qapp, tmp_path):
    projs = []
    for n in ("Alfa", "Beta"):
        d = tmp_path / n
        d.mkdir()
        (d / "asistannote.md").write_text(f"# {n}\n- [ ] görev {n}\n- [x] ~~bitti~~\n", encoding="utf-8")
        projs.append({"name": n, "path": str(d)})
    w = DocsWindow()
    w.set_sources(Settings(), projs)
    assert w.windowFlags() & Qt.WindowStaysOnTopHint
    assert w.tabs.count() == 4 and [w.tabs.tabText(i) for i in range(2)] == ["Alfa", "Beta"]
    v = w.tabs.widget(1)
    assert "görev Beta" in v.browser.toPlainText() and "bitti" not in v.browser.toPlainText()
    v.show_done.setChecked(True)
    assert "bitti" in v.browser.toPlainText()
    w.open_centered()
    geo = w.screen().availableGeometry()
    assert abs(w.width() - int(geo.width() * 0.55)) <= 2 and abs(w.height() - int(geo.height() * 0.85)) <= 2
    w.close()
    assert w.tabs.count() == 0


def test_settings_dialog_briefing_project_checklist(qapp):
    from reis.ui.settings_dialog import SettingsDialog

    s = Settings(projects=[{"name": "A", "path": "x"}, {"name": "B", "path": "y"}], briefing_projects=["b"])
    d = SettingsDialog(s)
    assert d.brief_list.count() == 2
    assert [d.brief_list.item(i).checkState() == Qt.Checked for i in range(2)] == [False, True]
    d.brief_list.item(0).setCheckState(Qt.Checked)
    d._save()
    assert s.briefing_projects == ["A", "B"]


def test_address_in_briefing_greeting_and_router_prompt():
    from reis.core import briefing as b

    now = dt.datetime(2026, 11, 26, 8, 0)
    assert b.compose(now, None, [], 0, False, address="patron").startswith("Günaydın patron.")
    assert b.compose(now, None, [], 0, False).startswith("Günaydın.")
    r = Router(None, lambda: NAMES, lambda n: "", today=lambda: dt.date(2026, 11, 25), address=lambda: "patron")
    assert "'patron' diye hitap et" in r.build_user_prompt("merhaba", NAMES)
    r2 = Router(None, lambda: NAMES, lambda n: "", today=lambda: dt.date(2026, 11, 25))
    assert "Hitap" not in r2.build_user_prompt("merhaba", NAMES)
