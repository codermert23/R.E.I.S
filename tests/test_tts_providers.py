import io
import json

import numpy as np
import pytest

from reis.config.settings import Settings, load_settings, settings_path
from reis.core.tts import FallbackTTS, make_tts, tts_key
from reis.core.tts import edge as edge_mod
from reis.core.tts.base import TTSError
from reis.core.tts.edge import EdgeTTS, decode_audio
from reis.core.tts.gemini_tts import GeminiTTS, pick_tts_model


def _tone(sr=22050, secs=0.3):
    t = np.arange(int(sr * secs)) / sr
    return (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)


def _encode(fmt: str, codec: str, sr=22050) -> bytes:
    import av

    buf = io.BytesIO()
    with av.open(buf, "w", format=fmt) as c:
        st = c.add_stream(codec, rate=sr)
        st.layout = "mono"
        pcm = _tone(sr)
        pad = (-len(pcm)) % 1152
        frame = av.AudioFrame.from_ndarray(np.concatenate([pcm, np.zeros(pad, np.int16)]).reshape(1, -1),
                                           format="s16", layout="mono")
        frame.sample_rate = sr
        for p in st.encode(frame):
            c.mux(p)
        for p in st.encode(None):
            c.mux(p)
    return buf.getvalue()


def test_decode_wav_resamples_to_24k_mono():
    pcm, sr = decode_audio(_encode("wav", "pcm_s16le"))
    assert sr == 24000 and pcm.dtype == np.int16 and pcm.ndim == 1
    assert abs(len(pcm) - int(0.3 * 24000)) < 1500
    assert np.abs(pcm).max() > 5000


def test_decode_mp3_if_encoder_available():
    try:
        data = _encode("mp3", "libmp3lame")
    except Exception:
        pytest.skip("mp3 kodlayıcı yok")
    pcm, sr = decode_audio(data)
    assert sr == 24000 and len(pcm) > 24000 * 0.25 and np.abs(pcm).max() > 3000


def test_decode_garbage_raises_tts_error():
    with pytest.raises(TTSError):
        decode_audio(b"bu ses degil")


class FakeComm:
    def __init__(self, chunks=None, exc=None, delay=0.0):
        self.chunks, self.exc, self.delay = chunks or [], exc, delay

    async def stream(self):
        import asyncio

        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        for c in self.chunks:
            yield c


def test_edge_synth_ok_and_ignores_non_audio_chunks():
    wav = _encode("wav", "pcm_s16le")
    comm = FakeComm([{"type": "WordBoundary"}, {"type": "audio", "data": wav[:100]}, {"type": "audio", "data": wav[100:]}])
    pcm, sr = EdgeTTS("tr-TR-AhmetNeural", communicate_factory=lambda t, v: comm).synth("merhaba")
    assert sr == 24000 and len(pcm) > 1000


def test_edge_timeout_and_offline_messages():
    with pytest.raises(TTSError, match="zaman aşımı"):
        EdgeTTS(timeout=0.05, communicate_factory=lambda t, v: FakeComm(delay=1)).synth("x")
    with pytest.raises(TTSError, match="internet"):
        EdgeTTS(communicate_factory=lambda t, v: FakeComm(exc=ConnectionError("down"))).synth("x")
    with pytest.raises(TTSError, match="boş"):
        EdgeTTS(communicate_factory=lambda t, v: FakeComm([])).synth("x")


def test_edge_no_audio_received_message():
    class NoAudioReceived(Exception):
        pass

    with pytest.raises(TTSError, match="ses döndürmedi"):
        EdgeTTS(communicate_factory=lambda t, v: FakeComm(exc=NoAudioReceived())).synth("x")


# ---- Gemini TTS
def test_pick_tts_model():
    names = ["models/gemini-3.8-flash", "models/gemini-3.1-flash-tts-preview", "models/gemini-3.8-flash-lite-tts",
             "models/gemini-3.8-flash-tts", "models/gemini-2.5-pro-preview-tts"]
    assert pick_tts_model(names) == "gemini-3.8-flash-tts"
    assert pick_tts_model(["gemini-2.5-flash", "gemini-3.8-flash"]) is None
    assert pick_tts_model(["gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts"]) == "gemini-2.5-flash-preview-tts"


class ApiError(Exception):
    def __init__(self, code):
        super().__init__(f"{code}")
        self.code = code


class FakeInteractions:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def create(self, **kw):
        self.calls.append(kw)
        r = self.script.pop(0)
        if isinstance(r, Exception):
            raise r
        import base64
        from types import SimpleNamespace

        return SimpleNamespace(output_audio=SimpleNamespace(data=base64.b64encode(r).decode()))


class FakeClient:
    def __init__(self, script, models=()):
        from types import SimpleNamespace

        self.interactions = FakeInteractions(script)
        self.models = SimpleNamespace(list=lambda: [SimpleNamespace(name=n) for n in models])


PCM = np.arange(50, dtype="<i2").tobytes()


def _g(script, models=(), **kw):
    g = GeminiTTS("K", client=FakeClient(script, models), **kw)
    g._sleep = lambda s: None
    return g


def test_gemini_ok_request_shape():
    g = _g([PCM], voice="Puck")
    pcm, sr = g.synth("selam")
    assert sr == 24000 and len(pcm) == 50 and pcm.dtype == np.int16
    kw = g._client.interactions.calls[0]
    assert kw["model"] == "gemini-3.8-flash-tts"
    assert kw["generation_config"]["speech_config"] == [{"voice": "Puck"}]


def test_gemini_retries_429_then_succeeds_and_gives_up():
    g = _g([ApiError(429), ApiError(503), PCM])
    assert len(g.synth("x")[0]) == 50 and len(g._client.interactions.calls) == 3
    g2 = _g([ApiError(429)] * 3)
    with pytest.raises(TTSError, match="sınırı"):
        g2.synth("x")
    assert len(g2._client.interactions.calls) == 3  # sonsuz döngü yok


def test_gemini_unknown_model_discovers_and_remembers():
    seen = []
    g = _g([ApiError(404), PCM], models=["models/gemini-9-flash-tts"], model="eski-tts",
           on_model_changed=seen.append)
    g.synth("x")
    assert g.model == "gemini-9-flash-tts" and seen == ["gemini-9-flash-tts"]


def test_gemini_all_models_missing_and_auth_errors():
    with pytest.raises(TTSError, match="bulunamadı"):
        _g([ApiError(404)] * 6, model="yok").synth("x")
    with pytest.raises(TTSError, match="geçersiz"):
        _g([ApiError(403)]).synth("x")
    with pytest.raises(TTSError, match="anahtarı girilmemiş"):
        GeminiTTS("").synth("x")


# ---- fabrika / yedek / ayarlar
class Boom:
    def __init__(self, exc=None):
        self.exc, self.n = exc or TTSError("çevrimdışı"), 0

    def synth(self, text):
        self.n += 1
        raise self.exc


class Ok:
    def __init__(self):
        self.n = 0

    def synth(self, text):
        self.n += 1
        return np.zeros(10, np.int16), 16000


def test_fallback_uses_backup_once():
    a, b = Boom(), Ok()
    assert FallbackTTS(a, b).synth("x")[1] == 16000 and a.n == 1 and b.n == 1


def test_fallback_both_fail_raises_primary_message_no_loop():
    a, b = Boom(), Boom(TTSError("piper yok"))
    with pytest.raises(TTSError, match="çevrimdışı"):
        FallbackTTS(a, b).synth("x")
    assert a.n == 1 and b.n == 1
    with pytest.raises(TTSError, match="Seslendirme başarısız"):
        FallbackTTS(Boom(RuntimeError("x")), Boom()).synth("y")


def test_factory_providers_and_fallback_wiring(tmp_path):
    from reis.config.settings import models_dir
    from reis.core.tts.piper_tts import PiperTTS, voice_paths

    assert isinstance(make_tts(Settings()), EdgeTTS)  # yazgı kurulu değil -> Edge; Piper yüklü değil: yedek yok
    assert isinstance(make_tts(Settings(tts_provider="edge")), EdgeTTS)
    assert isinstance(make_tts(Settings(tts_provider="eski_bilinmeyen")), EdgeTTS)  # bilinmeyen ayar: yazgı -> Edge
    g = make_tts(Settings(tts_provider="gemini"))
    assert isinstance(g.inner, GeminiTTS)  # Gemini sesi disk önbelleğiyle sarılır
    assert isinstance(make_tts(Settings(tts_provider="gemini", tts_cache_enabled=False)), GeminiTTS)
    assert isinstance(make_tts(Settings(tts_provider="piper")), PiperTTS)
    m, c = voice_paths("tr_TR-dfki-medium", models_dir())
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_bytes(b"1")
    c.write_bytes(b"1")
    t = make_tts(Settings())
    assert isinstance(t, FallbackTTS) and isinstance(t.primary, EdgeTTS) and isinstance(t.fallback, PiperTTS)
    assert not isinstance(make_tts(Settings(tts_provider="piper")), FallbackTTS)


def test_tts_key_changes_with_every_tts_setting():
    base = tts_key(Settings())
    for kw in ({"tts_provider": "gemini"}, {"edge_voice": "tr-TR-AhmetNeural"}, {"gemini_tts_voice": "Puck"},
               {"gemini_tts_model": "x-tts"}, {"piper_voice": "v"},
               {"yazgi_voice": "female-01"}):
        assert tts_key(Settings(**kw)) != base


def test_settings_backward_compatible_missing_tts_keys():
    p = settings_path()
    p.write_text(json.dumps({"tts_provider": "piper", "piper_voice": "tr_TR-fettah-medium", "hotkey": "F2"}),
                 encoding="utf-8")
    s = load_settings()
    assert s.tts_provider == "piper" and s.hotkey == "F2"
    assert s.edge_voice == "tr-TR-EmelNeural" and s.gemini_tts_voice == "Kore" and s.gemini_tts_model
    p.write_text("{}", encoding="utf-8")
    s = load_settings()
    assert s.tts_provider == "yazgi" and s.yazgi_voice == "male-01" and not s.yazgi_notice_shown
    p.write_text(json.dumps({"tts_provider": "edge", "elevenlabs_voice_id": "x"}), encoding="utf-8")
    assert load_settings().tts_provider == "edge"  # açık seçim korunur; bilinmeyen anahtar yok sayılır


def test_unused_import_guard():
    assert edge_mod.VOICES.keys() >= {"tr-TR-EmelNeural", "tr-TR-AhmetNeural"}


def test_elevenlabs_provider_wiring_has_credit_guard_and_edge_fallback(monkeypatch, tmp_path):
    from reis.core.tts import FallbackTTS
    from reis.core.tts.elevenlabs_tts import CreditGuardTTS

    monkeypatch.setattr("reis.core.tts.yazgi_provider.is_installed", lambda: False)
    monkeypatch.setattr("reis.config.secrets.get_secret", lambda name: "KEY" if "eleven" in name else "")
    t = make_tts(Settings(tts_provider="elevenlabs", elevenlabs_voice_id="VID"))
    assert isinstance(t, FallbackTTS) and isinstance(t.primary, CreditGuardTTS)
    assert t.primary.eleven.voice_id == "VID" and t.primary.eleven.api_key == "KEY"
    assert isinstance(t.fallbacks[0], EdgeTTS)
