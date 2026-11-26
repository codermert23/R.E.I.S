"""yazgı TTS: gerçek torch/indirme yok; sahte worker süreci (aynı protokol), saf komut fonksiyonları."""
import json
import sys
import textwrap
import time

import numpy as np
import pytest

from reis.config.settings import Settings, load_settings, models_dir, runtime_dir, settings_path
from reis.core.tts import FallbackTTS, make_tts, tts_key, yazgi_setup, yazgi_provider
from reis.core.tts.base import TTSError
from reis.core.tts.edge import EdgeTTS
from reis.core.tts.yazgi_provider import YazgiError, YazgiTTS, _Worker

FAKE = textwrap.dedent('''
    import json, os, sys, time
    mode = os.environ.get("FAKE_MODE", "ok")
    out = sys.stdout.buffer
    def send(o, payload=b""):
        out.write(json.dumps(o).encode() + b"\\n" + payload); out.flush()
    if mode == "badload":
        send({"ready": False, "error": "ImportError: torch"}); sys.exit(2)
    send({"ready": True, "sr": 24000, "voices": ["male-01"]})
    count = 0
    for line in sys.stdin.buffer:
        req = json.loads(line)
        count += 1
        marker = os.environ.get("FAKE_MARKER")
        first = not (marker and os.path.exists(marker))
        if mode == "crash_once" and first:
            open(marker, "w").write("x"); os._exit(3)
        if mode == "hang_once" and first:
            open(marker, "w").write("x"); time.sleep(30)
        if mode == "crash_always":
            os._exit(3)
        if mode == "error":
            send({"id": req["id"], "ok": False, "error": "ValueError: unknown voice"}); continue
        n = 2400
        pcm = (np_ := bytes(n * 2))
        send({"id": req["id"], "ok": True, "sr": 24000, "n": len(pcm)}, pcm)
''')


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE, encoding="utf-8")
    monkeypatch.setenv("FAKE_MARKER", str(tmp_path / "marker"))
    made = []

    def make(mode="ok", first_timeout=10.0):
        monkeypatch.setenv("FAKE_MODE", mode)
        w = _Worker([sys.executable, "-u", str(script)], first_timeout=first_timeout)
        made.append(w)
        return w

    yield make
    for w in made:
        w.close()


def test_protocol_roundtrip_and_lazy_start(fake):
    w = fake()
    assert w.starts == 0  # tembel: ilk kullanıma kadar süreç yok
    pcm, sr = w.synth("Merhaba", "male-01")
    assert sr == 24000 and pcm.dtype == np.int16 and len(pcm) == 2400
    w.synth("tekrar", "male-01")
    assert w.starts == 1 and w.voices == ["male-01"]  # canlı kalır


def test_provider_uses_voice_and_rejects_empty(fake):
    t = YazgiTTS("male-01", worker=fake())
    assert t.synth("x")[1] == 24000
    with pytest.raises(YazgiError, match="boş"):
        t.synth("  ")


def test_crash_restarts_once_then_succeeds(fake):
    w = fake("crash_once")
    pcm, _ = w.synth("a", "male-01")
    assert len(pcm) == 2400 and w.starts == 2


def test_crash_always_gives_turkish_error_after_one_restart(fake):
    w = fake("crash_always")
    with pytest.raises(YazgiError, match="yanıt vermedi"):
        w.synth("a", "male-01")
    assert w.starts == 2


def test_timeout_kills_and_restarts(fake, monkeypatch):
    monkeypatch.setattr(yazgi_provider, "CALL_TIMEOUT_S", 0.5)
    monkeypatch.setattr(yazgi_provider, "PER_CHAR_S", 0.0)
    w = fake("hang_once")
    t0 = time.monotonic()
    pcm, _ = w.synth("a", "male-01")
    assert len(pcm) == 2400 and w.starts == 2 and time.monotonic() - t0 < 10


def test_app_level_error_is_not_retried(fake):
    w = fake("error")
    with pytest.raises(YazgiError, match="unknown voice"):
        w.synth("a", "nope")
    assert w.starts == 1


def test_bad_load_message_mentions_install(fake):
    w = fake("badload")
    with pytest.raises(YazgiError, match="yüklenemedi.*Ses motorunu kur"):
        w.synth("a", "male-01")


def test_missing_python_gives_clean_error(tmp_path):
    w = _Worker([str(tmp_path / "yok" / "python.exe"), "x"])
    with pytest.raises(YazgiError, match="başlatılamadı"):
        w.synth("a", "male-01")


def test_shutdown_terminates_process_and_allows_reuse(fake):
    w = fake()
    w.synth("a", "male-01")
    proc = w._proc
    w.close()
    assert proc.poll() is not None and w._proc is None
    w.synth("b", "male-01")  # kapatılmış worker yeniden kullanılabilir
    assert w.starts == 2


def test_call_timeout_scales_with_text():
    assert yazgi_provider.call_timeout("a") < yazgi_provider.call_timeout("a" * 600)
    assert yazgi_provider.call_timeout("") == pytest.approx(8.0)


def test_error_carries_fallback_notice():
    assert "Edge sesine" in YazgiError("x").notice


# ---- fabrika / varsayılan zincir
def _install(monkeypatch, tmp_path, on=True):
    monkeypatch.setattr(yazgi_provider, "is_installed", lambda: on)


class Boom:
    def __init__(self, err):
        self.err = err

    def synth(self, text):
        raise self.err


def test_default_is_yazgi_and_falls_back_to_edge_when_not_installed(monkeypatch, tmp_path):
    assert Settings().tts_provider == "yazgi"
    _install(monkeypatch, tmp_path, False)
    assert isinstance(make_tts(Settings()), EdgeTTS)


def test_missing_install_notice_shown_once(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, False)
    seen = []
    s = Settings()
    make_tts(s, notify=seen.append)
    make_tts(s, notify=seen.append)
    assert len(seen) == 1 and "Ses motorunu kur" in seen[0] and s.yazgi_notice_shown
    assert load_settings().yazgi_notice_shown  # kalıcı


def test_chain_yazgi_edge_piper_without_loops(monkeypatch, tmp_path):
    from reis.core.tts.piper_tts import PiperTTS, voice_paths

    _install(monkeypatch, tmp_path, True)
    m, c = voice_paths("tr_TR-dfki-medium", models_dir())
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_bytes(b"1")
    c.write_bytes(b"1")
    t = make_tts(Settings(), notify=lambda _m: None)
    assert isinstance(t, FallbackTTS) and isinstance(t.primary, YazgiTTS)
    assert [type(f) for f in t.fallbacks] == [EdgeTTS, PiperTTS]
    calls = []

    class Rec:
        def __init__(self, name, err=None):
            self.name, self.err = name, err

        def synth(self, text):
            calls.append(self.name)
            if self.err:
                raise self.err
            return np.zeros(4, np.int16), 24000

    notices = []
    chain = FallbackTTS(Rec("yazgi", YazgiError("x")), Rec("edge", TTSError("y")), Rec("piper"),
                        notify=notices.append)
    chain.synth("a")
    assert calls == ["yazgi", "edge", "piper"]
    assert notices == ["Yazgı sesi çalışmadı; Piper sesine geçildi."]


def test_yazgi_voice_goes_to_provider_and_no_cache_wrapper(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, True)
    t = make_tts(Settings(yazgi_voice="female-02"))
    prim = t.primary if isinstance(t, FallbackTTS) else t
    assert isinstance(prim, YazgiTTS) and prim.voice == "female-02"  # önbellek sarmalayıcısı yok


def test_unknown_old_provider_maps_to_yazgi_chain(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, False)
    assert isinstance(make_tts(Settings(tts_provider="eski_bilinmeyen")), EdgeTTS)


def test_tts_key_tracks_install_state(monkeypatch):
    monkeypatch.setattr(yazgi_provider, "is_installed", lambda: False)
    a = tts_key(Settings())
    monkeypatch.setattr(yazgi_provider, "is_installed", lambda: True)
    assert tts_key(Settings()) != a


def test_old_settings_json_loads_with_defaults():
    settings_path().write_text(json.dumps({"tts_provider": "elevenlabs", "elevenlabs_voice_id": "v",
                                           "city": "Ankara"}), encoding="utf-8")
    s = load_settings()
    assert s.city == "Ankara" and s.yazgi_voice == "male-01" and s.tts_provider == "elevenlabs"


# ---- yollar ve kurulum akışı (saf fonksiyonlar)
def test_runtime_and_model_dirs_overridable(monkeypatch, tmp_path):
    monkeypatch.setenv("REIS_RUNTIME_DIR", str(tmp_path / "rt"))
    monkeypatch.setenv("REIS_MODELS_DIR", str(tmp_path / "md"))
    assert runtime_dir() == tmp_path / "rt"
    assert yazgi_provider.runtime_python() == tmp_path / "rt" / "yazgi" / "Scripts" / "python.exe"
    assert yazgi_provider.model_dir() == tmp_path / "md" / "yazgi"


def test_is_installed_needs_python_marker_and_models(monkeypatch, tmp_path):
    monkeypatch.setenv("REIS_RUNTIME_DIR", str(tmp_path / "rt"))
    assert not yazgi_provider.is_installed()
    py = yazgi_provider.runtime_python()
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    yazgi_provider.ready_marker().write_text("{}")
    assert not yazgi_provider.is_installed()  # model yok
    d = yazgi_provider.model_dir()
    (d / "voices").mkdir(parents=True)
    for f in yazgi_provider.MODEL_FILES:
        (d / f).write_bytes(b"1")
    (d / "voices" / "male-01.npz").write_bytes(b"1")
    assert yazgi_provider.is_installed()


def test_setup_commands_are_pinned_and_official(tmp_path):
    py = tmp_path / "py.exe"
    t = yazgi_setup.pip_torch_command(py)
    assert "torch==2.15.0+cpu" in t and "https://download.pytorch.org/whl/cpu" in t
    y = yazgi_setup.pip_yazgi_command(py)
    joined = " ".join(y)
    assert yazgi_setup.HF_REVISION in joined and "normalizer-tr==0.7.0" in y
    assert "https://pypi.org/simple" in y
    for cmd in (t, y):
        assert cmd[:3] == [str(py), "-m", "pip"]
        for a in cmd:
            if a.startswith("http"):
                assert a.startswith(("https://pypi.org/", "https://download.pytorch.org/", "https://huggingface.co/"))
    assert yazgi_setup.venv_command("python", tmp_path / "v") == ["python", "-m", "venv", str(tmp_path / "v")]


def test_model_files_urls_and_paths(tmp_path):
    files = yazgi_setup.model_files(tmp_path)
    names = {p.relative_to(tmp_path).as_posix() for p, _ in files}
    assert {"model.pt", "codec.pt", "config.json", "voices/male-01.npz", "voices/female-04.npz"} <= names
    assert len(files) == 11
    assert all(u.startswith(yazgi_setup.HF_BASE + "/") for _, u in files)


def test_frozen_blocks_setup_with_turkish_message(monkeypatch):
    assert yazgi_setup.blocker() == ""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    msg = yazgi_setup.blocker()
    assert "Reis.exe" in msg and "kaynaktan" in msg.lower() and "Edge" in msg
    with pytest.raises(RuntimeError, match="Reis.exe"):
        yazgi_setup.run_setup(lambda *a: None, lambda: False)


def test_setup_row_ui_frozen_shows_message_without_install(qapp, monkeypatch):
    from reis.ui.settings_dialog import yazgi_row

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(yazgi_provider, "is_installed", lambda: False)
    row = yazgi_row(None)
    row.start()
    assert "Reis.exe" in row.status.text() and not row.busy()


def test_setup_row_declined_confirmation_does_nothing(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from reis.ui.settings_dialog import yazgi_row

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    monkeypatch.setattr(yazgi_provider, "is_installed", lambda: False)
    row = yazgi_row(None)
    row.start()
    assert not row.busy()


def test_run_setup_order_and_cancel(monkeypatch, tmp_path):
    monkeypatch.setenv("REIS_RUNTIME_DIR", str(tmp_path / "rt"))
    monkeypatch.setenv("REIS_MODELS_DIR", str(tmp_path / "md"))
    cmds = []
    monkeypatch.setattr(yazgi_setup, "_run", lambda cmd, canc, on_line=None: cmds.append(cmd))

    def fake_dl(files, progress, cancelled):
        for p, _ in files:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"1")

    monkeypatch.setattr(yazgi_setup, "download_files", fake_dl)
    steps = []
    yazgi_setup.run_setup(lambda d, t, l: steps.append(d), lambda: False, base_python="BASE", verify=False)
    assert cmds[0][:3] == ["BASE", "-m", "venv"] and "torch==2.15.0+cpu" in cmds[1] and any(
        "yazgi-tts @" in a for a in cmds[2])
    assert steps[-1] == 100 and steps == sorted(steps)
    assert yazgi_provider.ready_marker().is_file()
