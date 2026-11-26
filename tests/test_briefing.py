import datetime as dt

import pytest

from reis.config.settings import Settings, load_settings, save_settings
from reis.core import briefing as b
from reis.core.services.cache import TTLCache
from reis.core.services.weather import Weather


def W(city="İstanbul", temp=7.4, desc="Parçalı bulutlu"):
    return Weather(city=city, temp=temp, feels_like=5, humidity=50, wind_kmh=10, code=2, description=desc, icon="partly")


def at(h, m=0):
    return dt.datetime(2026, 11, 25, h, m)


DAILY = """# Günlük Plan
- [ ] 14:00 Ders: Veri Yapıları (B204)
- [ ] 17:00 **Reis** denemesi
- [x] 10:00 ~~bitti~~
- [ ] ~~09:30 iptal~~
- [ ] Market
- [ ] 30 dk kitap oku
"""


@pytest.mark.parametrize("line,t,label", [
    ("- [ ] 14:00 Ders", dt.time(14, 0), "Ders"),
    ("- 14.30 Toplantı", dt.time(14, 30), "Toplantı"),
    ("14:00 - Doktor", dt.time(14, 0), "Doktor"),
    ("* 09:00–11:00 Matematik (A101)", dt.time(9, 0), "Matematik"),
    ("- [ ] **9:05** _Spor_", dt.time(9, 5), "Spor"),
    ("1. 08:15: Kahvaltı", dt.time(8, 15), "Kahvaltı"),
    ("- [ ] 09:00-10:00 Fizik", dt.time(9, 0), "Fizik"),
])
def test_extract_time_variants(line, t, label):
    items, untimed = b.extract_items(line)
    assert [(i.start, i.label) for i in items] == [(t, label)] and untimed == 0


def test_extract_ignores_done_strike_invalid_and_counts_untimed():
    items, untimed = b.extract_items(DAILY)
    assert [(i.start.strftime("%H:%M"), i.label) for i in items] == [
        ("14:00", "Ders: Veri Yapıları"), ("17:00", "Reis denemesi")]
    assert untimed == 2
    assert b.extract_items("- [ ] 25:99 saçma\n- 3.5 elma\nAkşam 20:00'den sonra boşum")[0] == []


def test_upcoming_before_after_none():
    items, _ = b.extract_items(DAILY)
    assert [i.start.hour for i in b.upcoming(items, at(7, 30))] == [14, 17]
    assert [i.start.hour for i in b.upcoming(items, at(14, 5))] == [14, 17]  # 5 dk önce başladı: hâlâ yaklaşan
    assert [i.start.hour for i in b.upcoming(items, at(14, 11))] == [17]
    assert b.upcoming(items, at(18, 0)) == []


def test_upcoming_dedups_same_start_and_sorts_across_sources():
    d, _ = b.extract_items("- [ ] 14:00 Ders: Veri Yapıları\n- [ ] 17:00 X")
    s, _ = b.extract_items("- 14:00–16:00 Veri Yapıları (B204)\n- 18:00 Antrenman")
    got = b.upcoming(d + s, at(8), limit=3)
    assert [(i.start.hour, i.label) for i in got] == [(14, "Ders: Veri Yapıları"), (17, "X"), (18, "Antrenman")]


@pytest.mark.parametrize("h,g", [(5, "Merhaba"), (6, "Günaydın"), (10, "Günaydın"), (11, "İyi günler"),
                                 (17, "İyi günler"), (18, "İyi akşamlar"), (22, "İyi akşamlar"),
                                 (23, "Merhaba"), (0, "Merhaba")])
def test_greeting_by_hour(h, g):
    assert b.greeting(at(h, 0)) == g


@pytest.mark.parametrize("city,out", [("İstanbul", "İstanbul'da"), ("İzmir", "İzmir'de"), ("Ankara", "Ankara'da"),
                                      ("Bursa", "Bursa'da"), ("Antalya", "Antalya'da"), ("Eskişehir", "Eskişehir'de"),
                                      ("Kocaeli", "Kocaeli'de"), ("Adıyaman", "Adıyaman'da")])
def test_locative(city, out):
    assert b.locative(city) == out


def test_full_sentence_matches_target_style():
    inp = b.BriefingInput(now=at(7, 30), weather=W(), daily_md="- [ ] 14:00 Veri Yapıları\n- [ ] 17:00 Reis denemesi",
                          has_notes=True)
    assert b.build_briefing(inp).speech == (
        "Günaydın. İstanbul'da 7 derece, parçalı bulutlu. Saat 14:00, Veri Yapıları. "
        "Saat 17:00, Reis denemesi. Proje notlarını açıyorum.")


def test_negative_temperature_and_no_weather_fallback():
    assert b.weather_sentence(W(temp=-2.6)) == "İstanbul'da eksi 3 derece, parçalı bulutlu."
    sp = b.build_briefing(b.BriefingInput(now=at(8), weather=None, daily_md="- [ ] 14:00 Ders", has_notes=False)).speech
    assert sp == "Günaydın. Saat 14:00, Ders."


def test_nothing_upcoming_and_no_projects():
    sp = b.build_briefing(b.BriefingInput(now=at(22), weather=None, daily_md="- [x] 10:00 ~~bitti~~")).speech
    assert sp == "İyi akşamlar. Bugün yaklaşan bir şeyin yok."
    sp = b.build_briefing(b.BriefingInput(now=at(22), weather=W(), daily_md="", has_notes=True)).speech
    assert sp.endswith("Bugün yaklaşan bir şeyin yok. Proje notlarını açıyorum.")


def test_untimed_count_only_when_positive():
    sp = b.build_briefing(b.BriefingInput(now=at(8), daily_md="- [ ] 14:00 Ders\n- [ ] a\n- [ ] b\n- [ ] c")).speech
    assert "Ve 3 açık görevin var." in sp
    assert "açık görev" not in b.build_briefing(b.BriefingInput(now=at(8), daily_md="- [ ] 14:00 Ders")).speech


def test_word_budget_drops_extras_first():
    long1 = "çok uzun bir toplantı başlığı burada sürüp gidiyor ve bitmiyor"
    daily = f"- [ ] 14:00 {long1}\n- [ ] 15:00 {long1}\n- [ ] 16:00 x\n- [ ] a\n- [ ] b"
    inp = b.BriefingInput(now=at(8), weather=W(), daily_md=daily, has_notes=True)
    sp = b.build_briefing(inp, max_items=3).speech
    assert len(sp.split()) <= 40
    items, _ = b.extract_items(daily)
    full = b.compose(at(8), W(), items, 2, True, max_items=3, max_words=60)
    assert "Ve 2 açık görevin var." in full and "parçalı bulutlu" in full
    # bütçe daralınca sırayla: saatsiz sayı, hava açıklaması, fazla maddeler, hava
    no_untimed = b.compose(at(8), W(), items, 2, True, max_items=3, max_words=len(full.split()) - 1)
    assert "açık görev" not in no_untimed and "parçalı bulutlu" in no_untimed
    no_desc = b.compose(at(8), W(), items, 2, True, max_items=3, max_words=len(no_untimed.split()) - 1)
    assert "parçalı" not in no_desc and "İstanbul'da 7 derece." in no_desc and "Saat 16:00" in no_desc
    fewer = b.compose(at(8), W(), items, 2, True, max_items=3, max_words=len(no_desc.split()) - 1)
    assert "Saat 16:00" not in fewer and "Saat 14:00" in fewer
    huge = " ".join(["kelime"] * 80)
    sp2 = b.build_briefing(b.BriefingInput(now=at(8), weather=W(), daily_md=f"- [ ] 14:00 {huge}",
                                           has_notes=True)).speech
    assert len(sp2.split()) <= 40


def test_weather_cache_and_failure():
    cache, calls = TTLCache(900), []

    def fetch(city):
        calls.append(city)
        return W(city)

    assert b.get_weather("İstanbul", cache, fetch).city == "İstanbul"
    assert b.get_weather("İstanbul", cache, fetch).city == "İstanbul"
    assert len(calls) == 1  # ikinci çağrı önbellekten

    def boom(city):
        raise RuntimeError("net yok")

    assert b.get_weather("Ankara", TTLCache(900), boom) is None
    assert b.get_weather("", TTLCache(900), boom) is None


def test_gather_reads_files_and_survives_weather_failure(tmp_path):
    daily = tmp_path / "2026-11-25.md"
    daily.write_text("- [ ] 14:00 Ders\n", encoding="utf-8")
    sched = tmp_path / "p.md"
    sched.write_text("## Çarşamba\n- 18:00 Antrenman\n## Perşembe\n- 09:00 Başka\n", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "asistannote.md").write_text("# P", encoding="utf-8")
    s = Settings(daily_path=str(tmp_path), schedule_path=str(sched), projects=[{"name": "P", "path": str(proj)}])
    res = b.gather(s, now=at(8), weather_fn=lambda c: None)
    assert res.speech == "Günaydın. Saat 14:00, Ders. Saat 18:00, Antrenman. Proje notlarını açıyorum."
    assert res.open_notes


def test_briefing_projects_selection():
    s = Settings(projects=[{"name": "A", "path": "x"}, {"name": "B", "path": "y"}])
    assert [p["name"] for p in b.briefing_projects(s)] == ["A", "B"]
    s.briefing_projects = ["b", "yok"]
    assert [p["name"] for p in b.briefing_projects(s)] == ["B"]


def test_settings_backcompat_old_json_and_roundtrip(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"city": "Ankara", "tts_provider": "edge"}', encoding="utf-8")
    s = load_settings(p)
    assert s.briefing_projects == [] and s.city == "Ankara"
    s.briefing_projects = ["A"]
    save_settings(s, p)
    assert load_settings(p).briefing_projects == ["A"]
