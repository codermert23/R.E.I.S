"""Performans: yazgı ön ısıtma / boşta kapatma (enjekte saat + sahte süreç), Whisper boşta bırakma, animasyon
zamanlayıcılarının gizliyken durması, gizliyken yenilememe, tembel pano."""
import subprocess
import sys
import time

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from reis.config.settings import Settings, load_settings, save_settings
from reis.core.pipeline import Pipeline
from reis.core.state import AssistantState, StateBus
from reis.core.tts import yazgi_provider as yp
from reis.core.tts.yazgi_provider import _Worker
from test_yazgi import FAKE


# ---------------------------------------------------------------- yazgı worker: ön ısıtma + boşta kapatma
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def fake_worker(tmp_path, monkeypatch):
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE, encoding="utf-8")
    monkeypatch.setenv("FAKE_MARKER", str(tmp_path / "marker"))
    monkeypatch.setenv("FAKE_MODE", "ok")
    made = []

    def make(clock=None):
        w = _Worker([sys.executable, "-u", str(script)], first_timeout=15.0, clock=clock or time.monotonic)
        made.append(w)
        return w

    yield make
    for w in made:
        w.close()


def test_idle_shutdown_after_n_minutes_then_transparent_restart(fake_worker):
    clk = Clock()
    w = fake_worker(clk)
    w.synth("Merhaba", "male-01")
    assert w.is_running() and w.starts == 1
    clk.t += 5 * 60
    assert w.close_if_idle(10 * 60) is True and w.is_running()  # 5 dk < 10 dk: canlı kalır
    clk.t += 6 * 60
    assert w.close_if_idle(10 * 60) is False and not w.is_running()  # 11 dk boşta: kapandı
    pcm, sr = w.synth("tekrar", "male-01")  # şeffaf yeniden başlatma
    assert sr == 24000 and len(pcm) == 2400 and w.starts == 2 and w.is_running()


def test_idle_close_skips_busy_worker_and_dead_worker(fake_worker):
    clk = Clock()
    w = fake_worker(clk)
    assert w.close_if_idle(1) is False  # hiç başlamadı
    w.synth("x", "male-01")
    clk.t += 3600
    assert w._lock.acquire(blocking=False)  # istek sürüyor gibi
    try:
        assert w.close_if_idle(1) is True and w.is_running()  # meşgulken dokunmaz
    finally:
        w._lock.release()


def test_ensure_ready_starts_once_and_prewarm_background_dedupes(fake_worker):
    w = fake_worker()
    assert w.prewarm_background() is True
    assert w.prewarm_background() is False or w.is_running()  # ısınırken ikinci çağrı yeni süreç açmaz
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not w.is_running():
        time.sleep(0.05)
    assert w.is_running() and w.starts == 1
    assert w.prewarm_background() is False  # zaten canlı
    w.ensure_ready()
    assert w.starts == 1
    t0 = time.perf_counter()
    w.synth("ilk cevap", "male-01")  # ısınmış: soğuk başlangıç yok
    assert time.perf_counter() - t0 < 2.0


def test_close_idle_workers_registry_reports_alive(fake_worker, monkeypatch):
    clk = Clock()
    w = fake_worker(clk)
    w.synth("x", "male-01")
    monkeypatch.setattr(yp, "_workers", {("k",): w})
    assert yp.close_idle_workers(600) is True
    clk.t += 601
    assert yp.close_idle_workers(600) is False


def test_prewarm_background_skips_other_providers_and_uninstalled(monkeypatch):
    monkeypatch.setattr(yp, "is_installed", lambda: False)
    assert yp.prewarm_background("yazgi") is False
    monkeypatch.setattr(yp, "is_installed", lambda: True)
    assert yp.prewarm_background("edge") is False and yp.prewarm_background("gemini") is False


# ---------------------------------------------------------------- Pipeline: ön ısıtma tetikleri + boşta zamanlayıcı
class Router:
    def route(self, text):
        from reis.core.router import RouteResult

        return RouteResult(speech="")


def make_pipe(settings=None, **kw):
    bus = StateBus()
    calls = {"warm": 0, "idle": []}

    def warm(s):
        calls["warm"] += 1
        return True

    def idle(minutes):
        calls["idle"].append(minutes)
        return calls.get("alive", True)

    p = Pipeline(settings or Settings(), bus, router_factory=lambda: Router(), tts_factory=lambda s: None,
                 prewarmer=warm, idle_closer=idle, **kw)
    return p, bus, calls


def test_prewarm_on_text_submit_mic_and_briefing(qapp, pump):
    p, bus, calls = make_pipe()
    p.submit_text("selam")
    assert calls["warm"] == 1
    p.start_briefing()
    assert calls["warm"] == 2
    p.start_listening()  # whisper yok -> indirme akışı; yine de önce ısıtır
    assert calls["warm"] == 3
    p.interrupt()
    pump(lambda: not p._tasks)


def test_idle_timer_armed_on_prewarm_and_stops_when_worker_gone(qapp):
    s = Settings()
    s.yazgi_idle_minutes = 7
    p, bus, calls = make_pipe(s)
    assert p._idle_timer is None  # tepsideyken hiç zamanlayıcı yok
    p.prewarm()
    assert p._idle_timer is not None and p._idle_timer.isActive()
    p.check_idle()
    assert calls["idle"] == [7] and p._idle_timer.isActive()  # worker canlı: sürer
    calls["alive"] = False
    p.check_idle()
    assert not p._idle_timer.isActive()  # worker kapandı: zamanlayıcı da durur


def test_idle_minutes_zero_never_shuts_down(qapp):
    s = Settings()
    s.yazgi_idle_minutes = 0
    p, bus, calls = make_pipe(s)
    p.prewarm()
    assert p._idle_timer is None
    p.check_idle()
    assert calls["idle"] == []


def test_no_real_worker_with_fake_tts_factory(qapp):
    bus = StateBus()
    p = Pipeline(Settings(), bus, tts_factory=lambda s: None)
    assert p._prewarmer is None and p._idle_closer is None
    p.prewarm()  # hiçbir şey yapmaz, hata vermez
    p.check_idle()


def test_settings_defaults_and_roundtrip(tmp_path):
    s = Settings()
    assert s.yazgi_prewarm_on_start is False and s.yazgi_idle_minutes == 10
    s.yazgi_prewarm_on_start, s.yazgi_idle_minutes = True, 0
    path = tmp_path / "s.json"
    save_settings(s, path)
    s2 = load_settings(path)
    assert s2.yazgi_prewarm_on_start is True and s2.yazgi_idle_minutes == 0


# ---------------------------------------------------------------- Whisper: boşta bırakma + zamanlayıcı
def _stt(qapp, monkeypatch, clock):
    from reis.core import stt as stt_mod

    monkeypatch.setattr(stt_mod, "whisper_ready", lambda d, s: True)
    made = []

    class Model:
        def transcribe(self, audio, **kw):
            return iter([type("S", (), {"text": " merhaba "})()]), None

    def factory(path):
        made.append(path)
        return Model()

    s = Settings()
    s.whisper_idle_minutes = 10
    return stt_mod.SpeechToText(s, model_factory=factory, clock=clock), made


def test_stt_timer_only_while_loaded_and_unloads_by_clock(qapp, monkeypatch, pump):
    clk = Clock()
    stt, made = _stt(qapp, monkeypatch, clk)
    assert not stt._timer.isActive()  # model yokken zamanlayıcı yok
    stt.preload()
    pump(lambda: stt._timer.isActive())
    assert stt.loaded and len(made) == 1 and stt._timer.isActive()
    clk.t += 9 * 60
    stt.unload_if_idle()
    assert stt.loaded
    stt.transcribe(np.ones(1600, np.int16), [])  # kullanım: süre sıfırlanır
    clk.t += 9 * 60
    stt.unload_if_idle()
    assert stt.loaded
    clk.t += 2 * 60
    stt.unload_if_idle()
    assert not stt.loaded and not stt._timer.isActive()
    stt.transcribe(np.ones(1600, np.int16), [])
    assert len(made) == 2  # şeffaf yeniden yükleme


# ---------------------------------------------------------------- zamanlayıcılar gizliyken durur
def test_animation_gate_pauses_on_hide_and_resumes_on_show(qapp):
    from reis.ui.animgate import AnimationGate

    w = QWidget()
    ticks = []
    g = AnimationGate(w, lambda: ticks.append(1), 20, on_resume=lambda: ticks.append("r"))
    assert not g.active
    w.show()
    assert g.active and g.resumes == 1
    w.hide()
    assert not g.active
    w.show()
    assert g.active and g.resumes == 2
    g.set_interval(77)
    assert g.timer.interval() == 77
    w.hide()
    w.close()


def test_animation_gate_pauses_when_window_minimized(qapp):
    from reis.ui.animgate import AnimationGate

    top = QWidget()
    child = QWidget(top)
    g = AnimationGate(child, lambda: None, 20)
    top.show()
    qapp.processEvents()
    assert g.active
    top.setWindowState(top.windowState() | Qt.WindowMinimized)
    qapp.processEvents()
    if not top.isMinimized():
        pytest.skip("platform pencere küçültmeyi desteklemiyor")
    assert not g.active
    top.setWindowState(Qt.WindowNoState)
    top.show()
    qapp.processEvents()
    assert g.active
    top.close()


def test_orb_sidebar_timers_follow_visibility(qapp):
    from reis.ui.orb import OrbWidget
    from reis.ui.sidebar import Sidebar

    bus = StateBus()
    host = QWidget()
    orb = OrbWidget(bus, host)
    assert not orb._gate.active  # henüz gösterilmedi
    host.resize(400, 400)
    host.show()
    qapp.processEvents()
    assert orb._gate.active
    host.hide()
    qapp.processEvents()
    assert not orb._gate.active
    host.close()

    s = Sidebar(Settings(), bus)
    assert not s._sys_gate.active and not s.orb._gate.active  # tepside: hiçbir şey çalışmıyor
    s.show()
    qapp.processEvents()
    assert s._sys_gate.active and s.orb._gate.active
    s.toggle_collapsed()  # yalnızca küçük kare görünür: içerik gizli -> hepsi durur
    qapp.processEvents()
    assert not s._sys_gate.active and not s.orb._gate.active
    assert s.width() == s.height() < 100
    s.toggle_collapsed()
    qapp.processEvents()
    assert s._sys_gate.active and s.orb._gate.active
    s.hide()
    qapp.processEvents()
    assert not s._sys_gate.active and not s.orb._gate.active


def test_orb_idle_uses_slower_interval(qapp):
    from reis.ui.orb import ACTIVE_MS, IDLE_MS, OrbWidget

    bus = StateBus()
    orb = OrbWidget(bus)
    orb.show()
    qapp.processEvents()
    assert orb._gate.timer.interval() == IDLE_MS
    bus.set_state(AssistantState.SPEAKING)
    assert orb._gate.timer.interval() == ACTIVE_MS
    bus.set_state(AssistantState.IDLE)
    assert orb._gate.timer.interval() == IDLE_MS
    orb.close()


# ---------------------------------------------------------------- gizliyken yenileme yok
def test_markdown_viewer_defers_reload_while_hidden_and_skips_identical_render(qapp, tmp_path):
    from reis.core.documents import DocResult
    from reis.ui.viewer import MarkdownViewer

    f = tmp_path / "n.md"
    f.write_text("- [ ] bir\n", encoding="utf-8")
    loads = []

    def loader():
        loads.append(1)
        return DocResult(f.read_text(encoding="utf-8"), "n", f, True, [str(f)])

    v = MarkdownViewer()
    v.set_loader(loader)
    assert len(loads) == 1
    sets = []
    orig = v.browser.setMarkdown
    v.browser.setMarkdown = lambda t: (sets.append(t), orig(t))[1]
    f.write_text("- [ ] bir\n- [ ] iki\n", encoding="utf-8")
    v._debounced_reload()  # görünmez: okuma/çizim yok, yalnızca işaret
    assert len(loads) == 1 and v._dirty and not sets
    v.show()
    qapp.processEvents()
    assert len(loads) == 2 and len(sets) == 1 and not v._dirty  # göründüğünde tek yenileme
    v.reload()  # içerik aynı: QTextDocument yeniden kurulmaz
    assert len(loads) == 3 and len(sets) == 1
    v.close()


# ---------------------------------------------------------------- tembel pano
def test_app_import_does_not_pull_dashboard_or_heavy_modules():
    code = ("import sys, reis.app; "
            "bad=[m for m in ("
            "'faster_whisper','ctranslate2','onnxruntime','piper','google.genai','edge_tts','av','sounddevice') "
            "if m in sys.modules]; print('BAD', bad)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "BAD []" in out.stdout, out.stdout
