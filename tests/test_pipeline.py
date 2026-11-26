import threading

import numpy as np
import pytest

from reis.config.settings import Settings
from reis.core.pipeline import Pipeline
from reis.core.router import RouteResult, RouterError, ShowItem
from reis.core.state import AssistantState, StateBus
from reis.core.tts.base import TTSError


class FakeRouter:
    def __init__(self, result=None, exc=None):
        self.result, self.exc = result, exc

    def route(self, text):
        if self.exc:
            raise self.exc
        return self.result


class FakeTTS:
    def __init__(self, exc=None):
        self.exc = exc
        self.texts = []

    def synth(self, text):
        self.texts.append(text)
        if self.exc:
            raise self.exc
        return np.zeros(160, dtype=np.int16), 16000


class FakePlayer:
    def __init__(self, block: threading.Event | None = None):
        self.played = []
        self.stops = 0
        self.block = block

    def play(self, pcm, sr):
        self.played.append(len(pcm))
        if self.block:
            self.block.wait(3)

    def stop(self):
        self.stops += 1
        if self.block:
            self.block.set()

    def beep(self):
        pass


_PIPES = []


@pytest.fixture(autouse=True)
def _drain(pump):
    yield
    for p in _PIPES:
        pump(lambda: not p._tasks)
    _PIPES.clear()


def make(qapp, result=None, exc=None, tts=None, player=None):
    bus = StateBus()
    states = []
    bus.state_changed.connect(states.append)
    subs = []
    bus.subtitle_changed.connect(subs.append)
    p = Pipeline(Settings(), bus, router_factory=lambda: FakeRouter(result, exc),
                 tts_factory=lambda s: tts or FakeTTS(), player=player or FakePlayer())
    _PIPES.append(p)
    shown = []
    p.show_requested.connect(shown.append)
    return p, bus, states, subs, shown


def test_text_to_speech_state_sequence(qapp, pump):
    res = RouteResult(speech="Merhaba efendim", show=[ShowItem(type="daily_plan")])
    tts, player = FakeTTS(), FakePlayer()
    p, bus, states, subs, shown = make(qapp, res, tts=tts, player=player)
    p.submit_text("selam")
    assert pump(lambda: len(states) >= 3 and states[-1] is AssistantState.IDLE)
    assert states == [AssistantState.THINKING, AssistantState.SPEAKING, AssistantState.IDLE]
    assert tts.texts == ["Merhaba efendim"] and player.played == [160]
    assert len(shown) == 1 and subs[-1] == "Merhaba efendim"


def test_only_speech_is_spoken_not_documents(qapp, pump):
    res = RouteResult(speech="Kısa cevap", show=[ShowItem(type="project", name="X")])
    tts = FakeTTS()
    p, bus, states, *_ = make(qapp, res, tts=tts)
    p.submit_text("x")
    assert pump(lambda: states and states[-1] is AssistantState.IDLE and len(states) == 3)
    assert tts.texts == ["Kısa cevap"]


def test_tts_failure_shows_error_but_keeps_documents(qapp, pump):
    res = RouteResult(speech="Merhaba", show=[ShowItem(type="schedule")])
    p, bus, states, subs, shown = make(qapp, res, tts=FakeTTS(exc=TTSError("Piper sesi yok")))
    p.submit_text("x")
    assert pump(lambda: AssistantState.ERROR in states)
    assert len(shown) == 1  # belge gösterildi
    assert "Piper sesi yok" in subs[-1]


def test_router_error_goes_to_error(qapp, pump):
    p, bus, states, subs, shown = make(qapp, exc=RouterError("Gemini anahtarı yok"))
    p.submit_text("x")
    assert pump(lambda: AssistantState.ERROR in states)
    assert states == [AssistantState.THINKING, AssistantState.ERROR] and subs[-1] == "Gemini anahtarı yok"


def test_new_command_interrupts_playback(qapp, pump):
    block = threading.Event()
    player = FakePlayer(block)
    res = RouteResult(speech="Uzun konuşma")
    p, bus, states, *_ = make(qapp, res, player=player)
    p.submit_text("bir")
    assert pump(lambda: AssistantState.SPEAKING in states and player.played)
    before = player.stops
    p.submit_text("iki")  # yeni komut: önceki konuşma kesilmeli
    assert player.stops > before
    assert pump(lambda: states.count(AssistantState.IDLE) >= 1 and states[-1] is AssistantState.IDLE)
    # eski konuşmanın bitişi IDLE'ı erken tetiklememeli: iki komut -> en fazla bir IDLE sonrası sıra tutarlı
    assert states[-1] is AssistantState.IDLE


def test_empty_speech_goes_idle_without_tts(qapp, pump):
    tts = FakeTTS()
    p, bus, states, *_ = make(qapp, RouteResult(speech=" "), tts=tts)
    p.submit_text("x")
    assert pump(lambda: states and states[-1] is AssistantState.IDLE)
    assert tts.texts == []
