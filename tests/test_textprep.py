import pytest

from reis.core.tts.textprep import clock_to_words, number_to_words, prepare_speech


@pytest.mark.parametrize("n,words", [
    (0, "sıfır"), (1, "bir"), (7, "yedi"), (10, "on"), (14, "on dört"), (21, "yirmi bir"), (71, "yetmiş bir"),
    (99, "doksan dokuz"), (100, "yüz"), (101, "yüz bir"), (200, "iki yüz"), (999, "dokuz yüz doksan dokuz"),
    (1000, "bin"), (1001, "bin bir"), (2000, "iki bin"), (2026, "iki bin yirmi altı"),
    (12500, "on iki bin beş yüz"), (99999, "doksan dokuz bin dokuz yüz doksan dokuz"),
    (100000, "100000"),
])
def test_number_to_words(n, words):
    assert number_to_words(n) == words


@pytest.mark.parametrize("h,m,words", [
    (14, 0, "on dört"), (14, 30, "on dört otuz"), (9, 5, "dokuz sıfır beş"), (0, 0, "sıfır"),
    (7, 30, "yedi otuz"), (13, 50, "on üç elli"), (23, 59, "yirmi üç elli dokuz"),
])
def test_clock_to_words(h, m, words):
    assert clock_to_words(h, m) == words


@pytest.mark.parametrize("src,out", [
    ("Saat 14:00, Ders.", "Saat on dört, Ders."),
    ("Saat 14:30, Toplantı.", "Saat on dört otuz, Toplantı."),
    ("Saat 09:05", "Saat dokuz sıfır beş"),
    ("7 derece", "yedi derece"),
    ("İstanbul'da 7°C, açık.", "İstanbul'da yedi derece, açık."),
    ("Hava 21 °C", "Hava yirmi bir derece"),
    ("eksi 3 derece", "eksi üç derece"),
    ("-3 derece", "eksi üç derece"),
    ("%71 dolu", "yüzde yetmiş bir dolu"),
    ("71% dolu", "yüzde yetmiş bir dolu"),
    ("25.11.2026 Çarşamba", "yirmi beş Kasım iki bin yirmi altı Çarşamba"),
    ("3,5 saat", "üç virgül beş saat"),
    ("12.500 TL", "on iki bin beş yüz TL"),
    ("1234 kişi", "bin iki yüz otuz dört kişi"),
    ("Saat 25:99 hatalı", "Saat yirmi beş:doksan dokuz hatalı"),  # geçersiz saat dokunulmadan sayılara bölünür
    ("Ders (B204).", "Ders (B204)."),  # harf+rakam kodu korunur
    ("**kalın** ve _vurgu_ ~x~", "kalın ve vurgu x"),
    ("# Başlık\n- madde bir\n- madde iki", "Başlık madde bir madde iki"),
    ("[bağlantı](http://x.y) var", "bağlantı var"),
    ("Merhaba 😀 efendim 🎉", "Merhaba efendim"),
    ("a   b \n c", "a b c"),
    ("Merhaba efendim", "Merhaba efendim"),
    ("", ""),
])
def test_prepare_speech_table(src, out):
    assert prepare_speech(src) == out


def test_prepare_is_deterministic_and_idempotent():
    s = "Günaydın. İstanbul'da 7 derece, açık. Saat 07:30, Ders. Proje notlarını açıyorum."
    once = prepare_speech(s)
    assert once == prepare_speech(s) == prepare_speech(once)
    assert "07:30" not in once and "yedi otuz" in once


def test_briefing_sentences_read_naturally():
    import datetime as dt

    from reis.core import briefing

    now = dt.datetime(2026, 11, 26, 7, 30)
    items = [briefing.TimedItem(dt.time(13, 50), "Ders")]
    text = briefing.compose(now, None, items, 0, True)
    assert "Saat 13:50, Ders." in text
    assert prepare_speech(text) == "Günaydın. Saat on üç elli, Ders. Proje notlarını açıyorum."
