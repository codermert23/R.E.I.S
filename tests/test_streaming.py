"""Akışlı TTS: parça sırası, iptal (eski parça asla çalmaz), yedeğe mid-stream düşme, oynatıcı akışı (sahte sounddevice)."""
import sys
import threading
import time
import types

import numpy as np
import pytest

from reis.config.settings import Settings
from reis.core.audio import Player, edge_fade, resample_linear
from reis.core.pipeline import Pipeline
from reis.core.router import RouteResult
from reis.core.state import AssistantState, StateBus
from reis.core.tts import FallbackTTS
from reis.core.tts.base import TTSError
from reis.core.tts.streaming import SpeechStream, iter_synth, run_speech


def pcm(n=160, v=1000):
    return np.full(n, v, dtype=np.int16)


class RecTTS:
    """Parça başına bir sinyal değeri üretir; çağrı sırasını kaydeder."""

    def __init__(self, fail_at=None, delay=0.0, sr=16000, gate=None):
        self.calls, self.fail_at, self.delay, self.sr, self.gate = [], fail_at, delay, sr, gate

    def synth(self, text):
        i = len(self.calls)
        self.calls.append(text)
        if self.gate is not None:
            self.gate(i)
        if self.delay:
            time.sleep(self.delay)
        if self.fail_at == i:
            raise TTSError(f"parça {i} hata")
        return pcm(160, 100 + i), self.sr


class ListPlayer:
    """play() ile çalar (play_stream yok): kaydeder."""

    def __init__(self, on_play=None):
        self.played, self.stops, self.on_play = [], 0, on_play

    def play(self, p, sr):
        self.played.append(int(p[0]))
        if self.on_play:
            self.on_play(len(self.played))

    def stop(self):
        self.stops += 1


def drain(stream, limit=10):
    out = []
    for _ in range(int(limit / 0.02)):
        it = stream.get(0.02)
        if it is stream.END:
            return out
        if it is not stream.WAIT:
            out.append(it)
    raise AssertionError("akış bitmedi")


# ---------------------------------------------------------------- sıra / sınır / hata
def test_chunks_play_in_order_and_complete():
    tts = RecTTS()
    s = SpeechStream(tts, ["bir", "iki", "üç", "dört"]).start()
    got = drain(s)
    assert [c.text for c in got] == ["bir", "iki", "üç", "dört"]
    assert [int(c.pcm[0]) for c in got] == [100, 101, 102, 103]
    assert tts.calls == ["bir", "iki", "üç", "dört"]


def test_queue_is_bounded_producer_does_not_run_ahead():
    tts = RecTTS()
    s = SpeechStream(tts, [str(i) for i in range(10)], maxsize=2).start()
    time.sleep(0.4)  # tüketici yok: üretici kuyruk dolunca durmalı (kuyruk 2 + eldeki 1)
    assert len(tts.calls) <= 4
    got = drain(s)
    assert len(got) == 10 and len(tts.calls) == 10


def test_cancel_stops_pending_synthesis_and_drops_queue():
    started = threading.Event()
    release = threading.Event()

    def gate(i):
        if i == 1:
            started.set()
            release.wait(3)

    tts = RecTTS(gate=gate)
    s = SpeechStream(tts, ["a", "b", "c", "d"]).start()
    assert started.wait(2)
    s.close()  # iptal: parça 1 üretiliyor, 2 ve 3 hiç üretilmemeli
    release.set()
    time.sleep(0.2)
    assert s.get(0.01) is s.END
    assert tts.calls == ["a", "b"]


def test_error_after_partial_audio_raises_after_queued_chunks():
    s = SpeechStream(RecTTS(fail_at=2), ["a", "b", "c", "d"]).start()
    got = []
    with pytest.raises(TTSError, match="parça 2"):
        for _ in range(500):
            it = s.get(0.02)
            if it is s.END:
                break
            if it is not s.WAIT:
                got.append(it.text)
    assert got == ["a", "b"]  # hata öncesi parçalar yine de çalındı


def test_run_speech_fallback_player_sequential_and_first_audio_once():
    tts, player = RecTTS(), ListPlayer()
    fired = []
    s = SpeechStream(tts, ["a", "b", "c"]).start()
    assert run_speech(player, s, lambda: fired.append(1)) is True
    assert player.played == [100, 101, 102] and fired == [1]


def test_cancel_during_playback_no_stale_chunk_plays():
    tts = RecTTS()
    holder = {}

    def on_play(n):
        if n == 1:
            holder["s"].close()  # birinci parça çalarken iptal (yeni komut / F1)

    player = ListPlayer(on_play)
    s = holder["s"] = SpeechStream(tts, ["a", "b", "c", "d"]).start()
    assert run_speech(player, s) is False
    assert player.played == [100]  # iptalden sonra hiçbir eski parça çalmadı


def test_cancel_before_start_plays_nothing():
    cancel = threading.Event()
    cancel.set()
    player = ListPlayer()
    s = SpeechStream(RecTTS(), ["a", "b"], cancel).start()
    assert run_speech(player, s) is False and player.played == []


# ---------------------------------------------------------------- FallbackTTS mid-stream
class Named:
    def __init__(self, name, fail_from=None, sr=16000, log=None):
        self.name, self.fail_from, self.sr, self.log = name, fail_from, sr, log if log is not None else []
        self.n = 0

    def synth(self, text):
        self.log.append((self.name, text))
        self.n += 1
        if self.fail_from is not None and self.n > self.fail_from:
            raise TTSError("düştü")
        return pcm(160, 7 if self.name == "primary" else 9), self.sr


def test_fallback_mid_stream_switches_remaining_chunks_without_replay():
    log = []
    prim, edge = Named("primary", fail_from=2, log=log), Named("edge", log=log)
    f = FallbackTTS(prim, edge)
    out = list(f.synth_stream(["c1", "c2", "c3", "c4", "c5"]))
    assert [t for t, _, _ in out] == ["c1", "c2", "c3", "c4", "c5"]
    assert [int(p[0]) for _, p, _ in out] == [7, 7, 9, 9, 9]
    # c3 birincilde bir kez denendi ve düştü; c4/c5 için birincil bir daha denenmedi; c1/c2 tekrar üretilmedi
    assert log == [("primary", "c1"), ("primary", "c2"), ("primary", "c3"), ("edge", "c3"), ("edge", "c4"),
                   ("edge", "c5")]


def test_fallback_chain_no_loops_and_error_when_all_fail():
    log = []
    f = FallbackTTS(Named("primary", fail_from=0, log=log), Named("edge", fail_from=0, log=log),
                    Named("piper", fail_from=0, log=log))
    with pytest.raises(TTSError):
        list(f.synth_stream(["x", "y"]))
    assert log == [("primary", "x"), ("edge", "x"), ("piper", "x")]  # her halka en çok bir kez


def test_fallback_notice_announced_once():
    class Bad:
        def synth(self, t):
            e = TTSError("yok")
            e.notice = "Edge sesine geçildi."
            raise e

    notes = []
    f = FallbackTTS(Bad(), Named("edge"), notify=notes.append)
    list(f.synth_stream(["a", "b", "c"]))
    assert notes == ["Edge sesine geçildi."]


def test_iter_synth_uses_plain_synth_for_simple_providers():
    t = RecTTS()
    assert [x[0] for x in iter_synth(t, ["a", "b"])] == ["a", "b"] and t.calls == ["a", "b"]


# ---------------------------------------------------------------- Player.play_stream (sahte sounddevice)
class FakeStream:
    instances = []

    def __init__(self, samplerate, channels, dtype):
        self.samplerate, self.writes = samplerate, []
        self.started = self.stopped = self.aborted = self.closed = False
        FakeStream.instances.append(self)

    def start(self):
        self.started = True

    def write(self, data):
        assert data.ndim == 2 and data.shape[1] == 1 and data.dtype == np.int16
        self.writes.append(data[:, 0].copy())

    def stop(self):
        self.stopped = True

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True

    @property
    def audio(self):
        return np.concatenate(self.writes) if self.writes else np.zeros(0, np.int16)


@pytest.fixture
def fake_sd(monkeypatch):
    FakeStream.instances.clear()
    mod = types.SimpleNamespace(OutputStream=FakeStream, stop=lambda: None)
    monkeypatch.setitem(sys.modules, "sounddevice", mod)
    return FakeStream


def test_play_stream_gapless_single_stream_with_pauses_and_fades(fake_sd):
    tts = RecTTS(sr=16000)
    s = SpeechStream(tts, ["Bir iki üç.", "Dört beş altı.", "Yedi sekiz dokuz."]).start()
    fired = []
    Player().play_stream(s, on_first_audio=lambda: fired.append(1))
    assert len(fake_sd.instances) == 1  # tek akış: parçalar arasında aygıt yeniden açılmaz
    st = fake_sd.instances[0]
    assert st.started and st.stopped and st.closed and not st.aborted and fired == [1]
    a = st.audio
    # 3 parça (160 örnek) + 3 duraklama (0.28 sn = 4480 örnek) + kuyruk sessizliği (+ bekleme sessizlikleri)
    assert len(a) >= 3 * 160 + 3 * 4480
    # kenarlar yumuşatıldı: parçanın ilk örneği tam genlikte değil (tık yok); orta kısım tam genlik
    assert abs(int(a[0])) < 100 and int(a[80]) == 100


def test_play_stream_cancel_aborts_and_writes_nothing_more(fake_sd):
    gate = threading.Event()
    tts = RecTTS(gate=lambda i: gate.wait(3) if i == 1 else None)
    s = SpeechStream(tts, ["Bir iki üç.", "Dört beş altı.", "Yedi sekiz dokuz."]).start()
    p = Player()
    t = threading.Thread(target=p.play_stream, args=(s,))
    t.start()
    time.sleep(0.3)  # birinci parça çalındı, ikincisi bekleniyor
    s.close()
    p.stop()
    gate.set()
    t.join(3)
    assert not t.is_alive()
    st = fake_sd.instances[0]
    assert st.aborted and st.closed
    n = len(st.writes)
    time.sleep(0.2)
    assert len(st.writes) == n  # iptalden sonra yazma yok


def test_play_stream_propagates_synth_error_and_closes(fake_sd):
    s = SpeechStream(RecTTS(fail_at=1), ["Bir iki üç.", "Dört beş altı."]).start()
    with pytest.raises(TTSError):
        Player().play_stream(s)
    assert fake_sd.instances[0].closed


def test_play_stream_resamples_mixed_rates(fake_sd):
    class Mixed:
        def __init__(self):
            self.n = 0

        def synth(self, t):
            self.n += 1
            return (pcm(240, 500), 24000) if self.n == 1 else (pcm(220, 500), 22050)

    s = SpeechStream(Mixed(), ["Bir iki üç.", "Dört beş altı."]).start()
    Player().play_stream(s)
    st = fake_sd.instances[0]
    assert st.samplerate == 24000  # ilk parçanın hızı; ikinci parça 24 kHz'e dönüştürüldü


def test_resample_and_edge_fade_helpers():
    x = pcm(220, 500)
    y = resample_linear(x, 22050, 24000)
    assert abs(len(y) - 240) <= 1 and set(np.unique(y)) == {500}
    assert resample_linear(x, 16000, 16000) is x
    f = edge_fade(pcm(1600, 1000), 16000)
    assert f[0] == 0 and f[-1] == 0 and f[800] == 1000


# ---------------------------------------------------------------- Pipeline entegrasyonu
class FakeRouter:
    def __init__(self, result):
        self.result = result

    def route(self, text):
        return self.result


def make_pipe(qapp, speech, tts, player):
    bus = StateBus()
    states = []
    bus.state_changed.connect(states.append)
    p = Pipeline(Settings(), bus, router_factory=lambda: FakeRouter(RouteResult(speech=speech)),
                 tts_factory=lambda s: tts, player=player)
    return p, bus, states


LONG = "Bugün hava çok güzel ve güneşli. Yarın yağmur bekleniyor ama sıcaklık değişmeyecek. Haftaya bahar geliyor."


def test_pipeline_streams_chunks_speaking_on_first_audio_idle_after_last(qapp, pump):
    tts, player = RecTTS(), ListPlayer()
    p, bus, states = make_pipe(qapp, LONG, tts, player)
    p.submit_text("hava")
    assert pump(lambda: states and states[-1] is AssistantState.IDLE)
    assert states == [AssistantState.THINKING, AssistantState.SPEAKING, AssistantState.IDLE]
    assert len(tts.calls) == 3 and player.played == [100, 101, 102]
    pump(lambda: not p._tasks)


def test_pipeline_interrupt_cancels_pending_synthesis_and_stale_chunks(qapp, pump):
    reached = threading.Event()
    release = threading.Event()

    def gate(i):
        if i == 1:
            reached.set()
            release.wait(3)

    tts = RecTTS(gate=gate)
    player = ListPlayer()
    p, bus, states = make_pipe(qapp, LONG, tts, player)
    p.submit_text("bir")
    assert pump(lambda: reached.is_set())
    assert pump(lambda: player.played == [100])  # parça 0 çalındı, parça 1 üretiliyor
    p.interrupt()  # F1 / yeni komut
    release.set()
    assert pump(lambda: not p._tasks)
    assert len(tts.calls) == 2  # parça 2 hiç üretilmedi
    assert player.played == [100]  # parça 1 üretilse de çalınmadı
    assert player.stops >= 1
    assert AssistantState.IDLE not in states  # eski konuşmanın bitişi IDLE'ı tetiklemez


def test_pipeline_error_on_later_chunk_sets_error(qapp, pump):
    tts, player = RecTTS(fail_at=1), ListPlayer()
    p, bus, states = make_pipe(qapp, LONG, tts, player)
    p.submit_text("x")
    assert pump(lambda: AssistantState.ERROR in states)
    assert player.played == [100]
    pump(lambda: not p._tasks)
