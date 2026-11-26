import threading

import numpy as np
import pytest

from reis.config.settings import Settings, models_dir
from reis.core import downloads
from reis.core import hotkey as hk
from reis.core import recorder as rec
from reis.core.pipeline import Pipeline
from reis.core.router import RouteResult
from reis.core.state import AssistantState, StateBus
from reis.core.stt import SpeechToText, build_initial_prompt

# ---------------------------------------------------------------- VAD / recorder


def run_seg(pattern: str, **kw):
    """pattern: her karakter bir 30 ms çerçeve; 'S' konuşma, '.' sessizlik."""
    seg = rec.VadSegmenter(rec.VadParams(**kw))
    for i, ch in enumerate(pattern):
        d = seg.feed(ch == "S")
        if d:
            return d, i + 1
    return None, len(pattern)


def test_stops_after_silence_following_speech():
    # 1.2 s = 40 çerçeve sessizlik
    d, n = run_seg("." * 5 + "S" * 20 + "." * 60)
    assert d == rec.OK
    assert n == 5 + 20 + 40


def test_silence_shorter_than_threshold_does_not_stop():
    d, _ = run_seg("S" * 10 + "." * 39 + "S" * 10 + "." * 39)
    assert d is None


def test_configurable_silence():
    d, n = run_seg("S" * 5 + "." * 100, silence_ms=300)
    assert d == rec.OK and n == 5 + 10


def test_no_speech_cancels_after_6s():
    d, n = run_seg("." * 400)
    assert d == rec.NO_SPEECH and n == 200  # 6000 / 30


def test_isolated_blip_is_not_speech_start():
    d, _ = run_seg(("S" + "." * 5) * 40)  # tek çerçeveler konuşma başlatmaz
    assert d == rec.NO_SPEECH


def test_max_30s_cap():
    d, n = run_seg("S" * 2000)
    assert d == rec.OK and n == 1000


def frames_reader(pattern):
    it = iter(pattern)

    def read():
        ch = next(it)
        return np.full(rec.FRAME_SAMPLES, 1000 if ch == "S" else 0, np.int16)

    return read


def is_speech(b):
    return b[0] != 0 or b[1] != 0


def test_record_frames_ok_and_audio_length():
    status, audio = rec.record_frames(frames_reader("..SSSSSS" + "." * 50), is_speech, rec.VadParams(),
                                      threading.Event(), threading.Event())
    assert status == rec.OK
    assert len(audio) == (8 + 40) * rec.FRAME_SAMPLES


def test_record_frames_manual_stop_with_speech():
    stop = threading.Event()
    n = {"i": 0}

    def read():
        n["i"] += 1
        if n["i"] == 10:
            stop.set()
        return np.full(rec.FRAME_SAMPLES, 1000, np.int16)

    status, audio = rec.record_frames(read, is_speech, rec.VadParams(), stop, threading.Event())
    assert status == rec.OK and len(audio) == 10 * rec.FRAME_SAMPLES


def test_record_frames_manual_stop_without_speech_is_no_speech():
    stop = threading.Event()
    stop.set()
    status, audio = rec.record_frames(frames_reader(""), is_speech, rec.VadParams(), stop, threading.Event())
    assert status == rec.NO_SPEECH and len(audio) == 0


def test_record_frames_cancel():
    cancel = threading.Event()
    cancel.set()
    status, _ = rec.record_frames(frames_reader("SSS"), is_speech, rec.VadParams(), threading.Event(), cancel)
    assert status == rec.CANCELLED


def test_recorder_without_microphone_reports_turkish_error(monkeypatch):
    import sounddevice as sd

    def boom(**kw):
        raise sd.PortAudioError("Error querying device -1")

    monkeypatch.setattr(sd, "query_devices", boom)
    errs, res = [], []
    r = rec.Recorder(rec.VadParams(), 2, lambda s, a: res.append(s), errs.append)
    r.start()
    r._thread.join(3)
    assert errs and "Mikrofon bulunamadı" in errs[0] and not res


# ---------------------------------------------------------------- hotkey


@pytest.mark.parametrize("text,expected", [
    ("F1", (0, 0x70)),
    ("f12", (0, 0x7B)),
    ("Ctrl+Alt+R", (hk.MOD_CONTROL | hk.MOD_ALT, ord("R"))),
    ("shift + space", (hk.MOD_SHIFT, 0x20)),
    ("Win+9", (hk.MOD_WIN, ord("9"))),
])
def test_parse_hotkey(text, expected):
    assert hk.parse_hotkey(text) == expected


@pytest.mark.parametrize("bad", ["", "Ctrl", "F25", "Foo+R", "Ctrl+Ğ", "Ctrl+Nope"])
def test_parse_hotkey_invalid(bad):
    with pytest.raises(hk.HotkeyError):
        hk.parse_hotkey(bad)


class FakeUser32:
    def __init__(self, ok=True):
        self.ok, self.calls, self.unreg = ok, [], 0

    def RegisterHotKey(self, hwnd, id_, mods, vk):
        self.calls.append((id_, mods, vk))
        return 1 if self.ok else 0

    def UnregisterHotKey(self, hwnd, id_):
        self.unreg += 1


def test_register_success_and_unregister(qapp):
    u = FakeUser32()
    m = hk.HotkeyManager(user32=u)
    assert m.register("F1")
    assert u.calls == [(hk.HOTKEY_ID, hk.MOD_NOREPEAT, 0x70)]
    m.register("F2")  # eskisi kaldırılır
    assert u.unreg == 1
    m.unregister()
    assert u.unreg == 2


def test_register_conflict_warns(qapp):
    m = hk.HotkeyManager(user32=FakeUser32(ok=False))
    warns = []
    m.warning.connect(warns.append)
    assert not m.register("F1")
    assert warns and "başka bir program" in warns[0]


def test_register_invalid_warns(qapp):
    m = hk.HotkeyManager(user32=FakeUser32())
    warns = []
    m.warning.connect(warns.append)
    assert not m.register("Ctrl")
    assert warns


def test_hotkey_debounce(qapp):
    m = hk.HotkeyManager(user32=FakeUser32())
    n = []
    m.pressed.connect(lambda: n.append(1))
    m._on_hotkey()
    m._on_hotkey()
    assert len(n) == 1


# ---------------------------------------------------------------- STT


class FakeWhisperModel:
    def __init__(self):
        self.kwargs = None

    def transcribe(self, audio, **kw):
        self.kwargs = kw
        assert audio.dtype == np.float32 and abs(audio).max() <= 1.0

        class Seg:
            def __init__(self, t):
                self.text = t

        return iter([Seg(" merhaba "), Seg("dünya")]), None


def ready_model(size="small"):
    d = downloads.whisper_dir(models_dir(), size)
    d.mkdir(parents=True)
    for f in ("config.json", "model.bin", "tokenizer.json"):
        (d / f).write_bytes(b"1")


def test_initial_prompt_contains_projects():
    p = build_initial_prompt(["BallClash", "Reis Asistan"])
    assert "BallClash" in p and "Reis Asistan" in p
    assert build_initial_prompt([]) is None


def test_stt_lazy_load_transcribe_and_idle_unload(qapp):
    ready_model()
    made = []

    def factory(path):
        m = FakeWhisperModel()
        made.append((path, m))
        return m

    s = Settings(whisper_idle_minutes=10)
    stt = SpeechToText(s, model_factory=factory)
    assert not stt.loaded and stt.model_ready()
    text = stt.transcribe((np.ones(1600) * 3000).astype(np.int16), ["BallClash"])
    assert text == "merhaba dünya" and stt.loaded and len(made) == 1
    kw = made[0][1].kwargs
    assert kw["language"] == "tr" and "BallClash" in kw["initial_prompt"]
    stt.unload_if_idle()
    assert stt.loaded  # henüz 10 dk geçmedi
    stt._last_used -= 11 * 60
    stt.unload_if_idle()
    assert not stt.loaded
    stt.transcribe(np.ones(160, np.int16), [])
    assert len(made) == 2  # tekrar yüklendi


def test_stt_model_missing_raises(qapp):
    from reis.core.stt import STTError

    with pytest.raises(STTError):
        SpeechToText(Settings(), model_factory=lambda p: None).transcribe(np.ones(10, np.int16), [])


# ---------------------------------------------------------------- pipeline: dinleme akışı


class FakeRecorder:
    def __init__(self, on_result, on_error):
        self.on_result, self.on_error = on_result, on_error
        self.started = self.stopped = self.cancelled = False

    def start(self):
        self.started = True

    def stop_now(self):
        self.stopped = True

    def cancel(self):
        self.cancelled = True


class FakeSTT:
    def __init__(self, text="günlük planı göster", ready=True):
        self.text, self.ready, self.calls = text, ready, []

    def model_ready(self):
        return self.ready

    def transcribe(self, audio, names):
        self.calls.append((len(audio), names))
        return self.text


class FakeRouter:
    def __init__(self, seen):
        self.seen = seen

    def route(self, text):
        self.seen.append(text)
        return RouteResult(speech="Tamam efendim")


class P:
    def __init__(self, text="günlük planı göster", ready=True, downloader=None):
        self.bus = StateBus()
        self.states, self.subs, self.routed = [], [], []
        self.bus.state_changed.connect(self.states.append)
        self.bus.subtitle_changed.connect(self.subs.append)
        self.recs = []
        self.played = []

        class Player:
            def play(s, pcm, sr):
                self.played.append(1)

            def stop(s):
                self.stopped = getattr(self, "stopped", 0) + 1

            def beep(s):
                pass

        class TTS:
            def synth(s, t):
                return np.zeros(100, np.int16), 16000

        def rf(on_result, on_error):
            r = FakeRecorder(on_result, on_error)
            self.recs.append(r)
            return r

        self.stt = FakeSTT(text, ready)
        self.p = Pipeline(Settings(projects=[{"name": "BallClash", "path": "x"}]), self.bus,
                          router_factory=lambda: FakeRouter(self.routed), tts_factory=lambda s: TTS(),
                          player=Player(), stt=self.stt, recorder_factory=rf, whisper_downloader=downloader)


@pytest.fixture(autouse=True)
def _drain_pipes(pump):
    yield
    for p in P_LIST:
        pump(lambda: not p._tasks)
    P_LIST.clear()


P_LIST = []


def mk(**kw):
    x = P(**kw)
    P_LIST.append(x.p)
    return x


def test_f1_idle_starts_listening_then_stop_processes(qapp, pump):
    x = mk()
    x.p.toggle_listening()
    assert x.bus.state is AssistantState.LISTENING and x.recs[0].started
    x.p.toggle_listening()  # dinlerken F1: durdur ve işle
    assert x.recs[0].stopped and len(x.recs) == 1
    x.recs[0].on_result(rec.OK, np.ones(480, np.int16))
    assert pump(lambda: x.states[-1] is AssistantState.IDLE and AssistantState.SPEAKING in x.states)
    assert x.states[:4] == [AssistantState.LISTENING, AssistantState.THINKING, AssistantState.THINKING,
                            AssistantState.SPEAKING]
    assert x.routed == ["günlük planı göster"]  # metin kutusuyla aynı akış
    assert x.stt.calls == [(480, ["BallClash"])]
    assert "» günlük planı göster" in x.subs


def test_f1_while_speaking_interrupts_and_listens(qapp, pump):
    x = mk()
    x.p.submit_text("selam")
    assert pump(lambda: AssistantState.SPEAKING in x.states)
    x.p.toggle_listening()
    assert x.bus.state is AssistantState.LISTENING and x.recs and x.stopped >= 1
    assert pump(lambda: not x.p._tasks)
    assert x.bus.state is AssistantState.LISTENING  # eski konuşmanın bitişi IDLE'a çevirmemeli


def test_no_speech_returns_to_idle_without_llm(qapp, pump):
    x = mk()
    x.p.toggle_listening()
    x.recs[0].on_result(rec.NO_SPEECH, np.zeros(0, np.int16))
    assert pump(lambda: x.bus.state is AssistantState.IDLE)
    assert x.routed == [] and x.stt.calls == []


def test_mic_error_shows_error(qapp, pump):
    x = mk()
    x.p.toggle_listening()
    x.recs[0].on_error("Mikrofon bulunamadı. Bir mikrofon bağla ve tekrar dene.")
    assert pump(lambda: x.bus.state is AssistantState.ERROR)
    assert "Mikrofon bulunamadı" in x.subs[-1]


def test_empty_transcript_goes_idle(qapp, pump):
    x = mk(text="  ")
    x.p.toggle_listening()
    x.recs[0].on_result(rec.OK, np.ones(480, np.int16))
    assert pump(lambda: x.bus.state is AssistantState.IDLE and x.stt.calls)
    assert x.routed == []


def test_cancelled_recording_result_ignored_after_new_command(qapp, pump):
    x = mk()
    x.p.toggle_listening()
    first = x.recs[0]
    x.p.submit_text("yazılı komut")  # kaydı iptal eder
    assert first.cancelled
    first.on_result(rec.OK, np.ones(480, np.int16))  # geç gelen sonuç
    assert pump(lambda: x.bus.state is AssistantState.IDLE and x.states.count(AssistantState.SPEAKING) == 1)
    assert x.stt.calls == []


def test_first_use_downloads_whisper_with_progress_then_listens(qapp, pump):
    calls = []

    def dl(size, root, prog):
        calls.append(size)
        prog(50, 100, "model.bin")
        prog(100, 100, "tamam")

    x = mk(ready=False, downloader=dl)
    x.p.toggle_listening()
    assert x.bus.state is AssistantState.THINKING and "indiriliyor" in x.subs[-1]
    assert pump(lambda: x.recs)
    assert calls == ["small"] and x.bus.state is AssistantState.LISTENING
    assert any("%50" in s for s in x.subs)


def test_whisper_download_failure_shows_error(qapp, pump):
    def dl(size, root, prog):
        raise RuntimeError("ağ yok")

    x = mk(ready=False, downloader=dl)
    x.p.toggle_listening()
    assert pump(lambda: x.bus.state is AssistantState.ERROR)
    assert "indirilemedi" in x.subs[-1] and not x.recs
    x.p.toggle_listening  # sonraki F1 yeniden denenebilir
    assert not x.p._downloading
