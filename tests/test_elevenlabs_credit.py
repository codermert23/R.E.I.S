import datetime as dt
import json
import os
import time

import httpx
import numpy as np
import pytest

from reis.config.settings import Settings, load_settings, settings_path
from reis.core.tts import FallbackTTS, make_tts, tts_key
from reis.core.tts import elevenlabs_api as api
from reis.core.tts.base import TTSError
from reis.core.tts.cache import AudioCache, CachedTTS, cache_key
from reis.core.tts.elevenlabs_tts import CreditGuardTTS, ElevenLabsTTS


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- modeller / maliyet
@pytest.mark.parametrize("model,mult", [("eleven_flash_v2_5", 0.5), ("eleven_multilingual_v2", 1.0),
                                        ("eleven_turbo_v2_5", 0.5), ("bilinmeyen_model", 1.0)])
def test_credit_multiplier_table(model, mult):
    assert api.credit_multiplier(model) == mult
    assert api.estimate_cost("x" * 100, model) == 100 * mult


# ---------------------------------------------------------------- ses listesi
VOICES = {"voices": [
    {"voice_id": "lib1", "name": "Kütüphane Ses", "category": "professional", "labels": {"language": "tr"}},
    {"voice_id": "p2", "name": "Sarah", "category": "premade", "labels": {"gender": "female", "accent": "american"}},
    {"voice_id": "p1", "name": "Adam", "category": "premade", "labels": {"gender": "male"}},
    {"voice_id": "gen", "name": "Benimki", "category": "generated"},
    {"name": "kimliksiz"},
], "has_more": False}


def test_parse_voices_premade_first_and_free_flags():
    vs = api.parse_voices(VOICES)
    assert [v.voice_id for v in vs] == ["p1", "gen", "p2", "lib1"]  # ücretsizler önce, ada göre
    by = {v.voice_id: v for v in vs}
    assert by["p1"].free_ok and by["gen"].free_ok and not by["lib1"].free_ok
    assert "erkek" in by["p1"].display() and "ücretsiz" in by["p1"].display()
    assert "ücretli plan" in by["lib1"].display()
    assert by["lib1"].speaks_turkish()


def test_preferred_voice_prefers_premade_but_keeps_current():
    vs = api.parse_voices(VOICES)
    assert api.preferred_voice(vs).voice_id == "p1"
    assert api.preferred_voice(vs, "lib1").voice_id == "lib1"
    assert api.preferred_voice([]) is None
    only_lib = [v for v in vs if v.voice_id == "lib1"]
    assert api.preferred_voice(only_lib).voice_id == "lib1"


def test_fetch_voices_ok_pagination_and_header():
    seen = []

    def h(req: httpx.Request):
        seen.append((req.url.path, req.headers.get("xi-api-key"), dict(req.url.params)))
        if "next_page_token" not in req.url.params:
            return httpx.Response(200, json={"voices": VOICES["voices"][:2], "has_more": True, "next_page_token": "T"})
        return httpx.Response(200, json={"voices": VOICES["voices"][2:], "has_more": False})

    vs = api.fetch_voices("KEY", _client(h))
    assert len(vs) == 4 and len(seen) == 2 and seen[0][1] == "KEY" and seen[1][2]["next_page_token"] == "T"


def test_fetch_voices_falls_back_to_v1():
    paths = []

    def h(req):
        paths.append(req.url.path)
        return httpx.Response(404) if "/v2/" in req.url.path else httpx.Response(200, json=VOICES)

    assert len(api.fetch_voices("K", _client(h))) == 4 and paths == ["/v2/voices", "/v1/voices"]


@pytest.mark.parametrize("resp,code,text", [
    (httpx.Response(401, json={"detail": {"status": "invalid_api_key"}}), "auth", "geçersiz"),
    (httpx.Response(429), "rate", "429"),
    (httpx.Response(402, json={"detail": {"status": "paid_plan_required"}}), "paid_plan", "ücretsiz planda"),
    (httpx.Response(401, json={"detail": {"status": "quota_exceeded"}}), "quota", "kredin bitti"),
    (httpx.Response(500), "http", "500"),
])
def test_error_classification_turkish(resp, code, text):
    with pytest.raises(api.ElevenLabsError, match=text) as ei:
        api.fetch_voices("K", _client(lambda r: resp))
    assert ei.value.code == code


def test_offline_and_missing_key():
    def boom(req):
        raise httpx.ConnectError("down")

    with pytest.raises(api.ElevenLabsError, match="ulaşılamadı") as ei:
        api.fetch_voices("K", _client(boom))
    assert ei.value.code == "offline"
    with pytest.raises(api.ElevenLabsError, match="anahtarını gir"):
        api.fetch_voices("")


# ---------------------------------------------------------------- kullanım / biçim
SUB = {"tier": "free", "character_count": 2400, "character_limit": 10000,
       "next_character_count_reset_unix": int(dt.datetime(2026, 12, 12, 12, 0).timestamp())}


def test_parse_and_format_usage_turkish_months():
    u = api.parse_subscription(SUB)
    assert (u.used, u.limit, u.tier, u.remaining) == (2400, 10000, "free", 7600)
    assert api.format_usage(u) == "ElevenLabs: 2.400 / 10.000 karakter · yenilenme: 12 Aralık"
    assert api.format_usage(api.Usage(0, 10000)) == "ElevenLabs: 0 / 10.000 karakter"
    jan = api.Usage(10, 100, int(dt.datetime(2027, 1, 3, 12).timestamp()))
    assert api.format_usage(jan).endswith("yenilenme: 3 Ocak")


def test_fetch_subscription_and_errors():
    u = api.fetch_subscription("K", _client(lambda r: httpx.Response(200, json=SUB)))
    assert u.limit == 10000
    with pytest.raises(api.ElevenLabsError, match="geçersiz"):
        api.fetch_subscription("K", _client(lambda r: httpx.Response(401)))


def test_meter_consume_stale_and_refresh_limits():
    now = [1000.0]
    m = api.UsageMeter(clock=lambda: now[0])
    assert m.remaining() is None and m.stale() and m.can_afford(10**6, 300)  # bilinmiyor: yeterli say
    m.set(api.Usage(2400, 10000))
    m.consume(100)
    assert m.remaining() == 7500 and not m.stale()
    now[0] += 601
    assert m.stale()
    calls = []
    m.refresh_async(lambda: calls.append(1) or api.Usage(0, 10), force=True)
    deadline = time.time() + 2
    while not calls and time.time() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)
    assert calls == [1] and m.remaining() == 10 and not m.refresh_async(lambda: calls.append(2))  # taze: tekrar yok


# ---------------------------------------------------------------- kırpma
def test_trim_for_cost():
    assert api.trim_for_cost("Kısa.", 250) == "Kısa."
    s = "Birinci cümle burada. İkinci cümle biraz daha uzun olsun. Üçüncü cümle de var."
    assert api.trim_for_cost(s, 50) == "Birinci cümle burada."
    long_words = "kelime " * 100
    t = api.trim_for_cost(long_words, 60)
    assert len(t) <= 61 and t.endswith("kelime.")
    assert len(api.trim_for_cost("x" * 500, 100)) <= 101  # boşluksuz: sert kesim
    assert api.trim_for_cost("a" * 400) != "a" * 400


# ---------------------------------------------------------------- önbellek
def _pcm(n=100, v=1):
    return np.full(n, v, np.int16)


def test_cache_hit_miss_roundtrip(tmp_path):
    c = AudioCache(tmp_path)
    k = cache_key("elevenlabs", "v", "m", "Proje  notlarını açıyorum.")
    assert c.get(k) is None
    c.put(k, _pcm(50, 7), 24000)
    pcm, sr = c.get(k)
    assert sr == 24000 and len(pcm) == 50 and pcm[0] == 7 and pcm.dtype == np.int16
    # boşluk normalizasyonu aynı anahtar; sağlayıcı/ses/model/metin farkı farklı anahtar
    assert k == cache_key("elevenlabs", "v", "m", " Proje notlarını   açıyorum. ")
    assert len({k, cache_key("gemini", "v", "m", "Proje notlarını açıyorum."),
                cache_key("elevenlabs", "v2", "m", "Proje notlarını açıyorum."),
                cache_key("elevenlabs", "v", "m2", "Proje notlarını açıyorum."),
                cache_key("elevenlabs", "v", "m", "Başka.")}) == 5


def test_cache_disabled_never_stores(tmp_path):
    c = AudioCache(tmp_path, enabled=False)
    c.put("k", _pcm(), 1)
    assert c.get("k") is None and not list(tmp_path.glob("*.wav"))


def test_cache_evicts_oldest_first(tmp_path):
    one = len(_pcm(1000).tobytes())
    c = AudioCache(tmp_path, max_bytes=int(one * 2.5))
    for i, k in enumerate(("a", "b", "c")):
        c.put(k, _pcm(1000, i + 1), 16000)
        os.utime(tmp_path / f"{k}.wav", (1000 + i, 1000 + i))
    c.put("d", _pcm(1000, 4), 16000)
    assert c.get("a") is None and c.get("b") is None  # en eskiler gitti
    assert c.get("c") is not None and c.get("d") is not None
    assert c.size_bytes() <= c.max_bytes


class Counting:
    def __init__(self):
        self.n = 0
        self.texts = []

    def synth(self, text):
        self.n += 1
        self.texts.append(text)
        return _pcm(80), 24000


def test_cached_tts_second_call_costs_nothing(tmp_path):
    inner = Counting()
    t = CachedTTS(inner, AudioCache(tmp_path), "elevenlabs", lambda: ("v", "m"))
    t.synth("Merhaba.")
    t.synth("Merhaba.")
    assert inner.n == 1 and t.last_hit


# ---------------------------------------------------------------- kredi güvenliği
def _guard(tmp_path, meter, remaining=None, reserve=300, max_chars=250, model="eleven_flash_v2_5", handler=None):
    calls = []

    def h(req):
        calls.append(json.loads(req.content)["text"])
        return httpx.Response(200, content=np.zeros(200, "<i2").tobytes())

    eleven = ElevenLabsTTS("K", "V", model, client=_client(handler or h))
    cached = CachedTTS(eleven, AudioCache(tmp_path), "elevenlabs", lambda: (eleven.voice_id, eleven.model))
    if remaining is not None:
        meter.set(api.Usage(10000 - remaining, 10000))
    return CreditGuardTTS(eleven, cached, meter, max_chars, reserve), calls


@pytest.mark.parametrize("remaining,text,model,allowed", [
    (None, "x" * 100, "eleven_flash_v2_5", True),      # bilinmiyor -> yeterli say
    (5000, "x" * 100, "eleven_flash_v2_5", True),
    (299, "x" * 10, "eleven_flash_v2_5", False),       # yedeğin altında
    (300, "x" * 10, "eleven_flash_v2_5", True),
    (320, "x" * 100, "eleven_multilingual_v2", True),  # 100 kredi maliyet, 320 >= 300
    (350, "x" * 240, "eleven_multilingual_v2", True),   # 350 >= maliyet 240 ve >= yedek 300
])
def test_credit_decisions(tmp_path, remaining, text, model, allowed):
    meter = api.UsageMeter()
    g, calls = _guard(tmp_path, meter, remaining, model=model)
    if allowed:
        g.synth(text)
        assert calls == [text]
    else:
        with pytest.raises(api.ElevenLabsError) as ei:
            g.synth(text)
        assert ei.value.code == "quota" and "Edge sesine geçtim" in ei.value.notice and calls == []


def test_cost_exceeding_balance_blocks_even_above_reserve(tmp_path):
    meter = api.UsageMeter()
    g, calls = _guard(tmp_path, meter, remaining=310, model="eleven_multilingual_v2", max_chars=400)
    with pytest.raises(api.ElevenLabsError):
        g.synth("x" * 320)  # maliyet 320 > kalan 310
    assert calls == []


def test_success_consumes_credits_locally_and_cache_hit_does_not(tmp_path):
    meter = api.UsageMeter()
    meter.set(api.Usage(0, 10000))
    g, calls = _guard(tmp_path, meter)
    g.synth("a" * 100)
    assert meter.remaining() == 10000 - 50  # flash: yarı kredi
    g.synth("a" * 100)  # önbellek isabeti
    assert len(calls) == 1 and meter.remaining() == 10000 - 50


def test_guard_trims_long_text_before_sending(tmp_path):
    meter = api.UsageMeter()
    g, calls = _guard(tmp_path, meter, max_chars=40)
    g.synth("Birinci cümle burada. İkinci cümle biraz daha uzun olsun. Üçüncü.")
    assert calls == ["Birinci cümle burada."] and len(calls[0]) <= 40


def test_cache_hit_works_even_when_credits_gone(tmp_path):
    meter = api.UsageMeter()
    g, calls = _guard(tmp_path, meter)
    g.synth("Proje notlarını açıyorum.")
    meter.set(api.Usage(10000, 10000))
    assert len(g.synth("Proje notlarını açıyorum.")[0]) == 200
    assert len(calls) == 1


def test_quota_error_from_server_zeroes_meter(tmp_path):
    meter = api.UsageMeter()
    meter.set(api.Usage(0, 10000))
    g, _ = _guard(tmp_path, meter, handler=lambda r: httpx.Response(401, json={"detail": {"status": "quota_exceeded"}}))
    with pytest.raises(api.ElevenLabsError):
        g.synth("selam")
    assert meter.remaining() == 0


# ---------------------------------------------------------------- yedek zinciri
class Boom:
    def __init__(self, exc):
        self.exc, self.n = exc, 0

    def synth(self, text):
        self.n += 1
        raise self.exc


class Ok:
    def __init__(self):
        self.n = 0

    def synth(self, text):
        self.n += 1
        return _pcm(10), 16000


def test_chain_edge_then_piper_no_loops_and_one_notice():
    notices = []
    err = api.ElevenLabsError("kredi", "quota", "ElevenLabs kredisi bitti, Edge sesine geçtim.")
    a, edge, piper = Boom(err), Boom(TTSError("edge çevrimdışı")), Ok()
    f = FallbackTTS(a, edge, piper, notify=notices.append)
    assert f.synth("x")[1] == 16000
    f.synth("y")
    assert (a.n, edge.n, piper.n) == (2, 2, 2)
    assert notices == ["ElevenLabs kredisi bitti, Piper sesine geçtim."]  # tek bildirim


def test_chain_notice_once_with_edge_success():
    notices = []
    err = api.ElevenLabsError("kredi", "quota", "ElevenLabs kredisi bitti, Edge sesine geçtim.")
    f = FallbackTTS(Boom(err), Ok(), Ok(), notify=notices.append)
    f.synth("a")
    f.synth("b")
    assert notices == ["ElevenLabs kredisi bitti, Edge sesine geçtim."]


def test_chain_all_fail_raises_primary_error():
    err = api.ElevenLabsError("ilk hata", "http")
    with pytest.raises(TTSError, match="ilk hata"):
        FallbackTTS(Boom(err), Boom(TTSError("b")), Boom(TTSError("c"))).synth("x")


def test_no_notice_without_notice_attr():
    notices = []
    FallbackTTS(Boom(TTSError("x")), Ok(), notify=notices.append).synth("a")
    assert notices == []


def test_paid_plan_error_has_friendly_notice():
    r = httpx.Response(402, json={"detail": {"status": "paid_plan_required"}})
    t = ElevenLabsTTS("K", "V", client=_client(lambda req: r))
    with pytest.raises(api.ElevenLabsError, match="Voice Library") as ei:
        t.synth("x")
    assert ei.value.code == "paid_plan" and "Edge" in ei.value.notice


# ---------------------------------------------------------------- ayarlar
