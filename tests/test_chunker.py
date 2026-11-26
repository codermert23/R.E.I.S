"""Konuşma cümle bölücü: tablo güdümlü (ondalık, saat, kısaltma, üç nokta, uzun cümle, Türkçe tırnak)."""
import pytest

from reis.core.tts.chunker import MAX_CHARS, pause_after, split_chunks


@pytest.mark.parametrize("text,expected", [
    ("", []),
    ("   ", []),
    ("Merhaba efendim", ["Merhaba efendim"]),
    # iki cümle, her biri >=3 kelime
    ("Bugün hava çok güzel. Yarın yağmur bekleniyor.", ["Bugün hava çok güzel.", "Yarın yağmur bekleniyor."]),
    # ! ? ; :
    ("Harika bir gün! Sence de öyle değil mi? Evet kesinlikle.",
     ["Harika bir gün!", "Sence de öyle değil mi? Evet kesinlikle."]),  # "Evet kesinlikle." 2 kelime: öncekine katılır
    ("Önce notları aç; sonra planı oku; ardından toplantıya git.",
     ["Önce notları aç;", "sonra planı oku;", "ardından toplantıya git."]),
    ("Dikkat et: yarın sınav var ve hazırlık gerek.", ["Dikkat et: yarın sınav var ve hazırlık gerek."]),  # "Dikkat et:" 2 kelime
    ("Önemli bir not var: yarın sabah erkenden kalkmamız gerekiyor.",
     ["Önemli bir not var:", "yarın sabah erkenden kalkmamız gerekiyor."]),
    # ondalık ve saat bozulmaz (boşluksuz nokta/iki nokta)
    ("Sıcaklık 3.5 derece olacak. Saat 14:30 toplantı var.", ["Sıcaklık 3.5 derece olacak.", "Saat 14:30 toplantı var."]),
    ("Fiyat 12,50 lira ve kdv dahil. Teşekkürler efendim.", ["Fiyat 12,50 lira ve kdv dahil. Teşekkürler efendim."]),
    # kısaltmalar cümleyi bitirmez
    ("Dr. Ahmet yarın toplantıya gelecek. Prof. Yılmaz da katılacak.",
     ["Dr. Ahmet yarın toplantıya gelecek.", "Prof. Yılmaz da katılacak."]),
    ("Elma, armut vb. meyveleri aldım. Sonra eve döndüm.", ["Elma, armut vb. meyveleri aldım.", "Sonra eve döndüm."]),
    # sıra sayısı ve baş harf
    ("Bugün 3. gün ve proje devam ediyor.", ["Bugün 3. gün ve proje devam ediyor."]),
    ("M. Kemal Atatürk büyük bir liderdi. Çok sevildi.", ["M. Kemal Atatürk büyük bir liderdi. Çok sevildi."]),
    # üç nokta
    ("Bekle bir saniye... sonra devam ederiz. Tamam mı?", ["Bekle bir saniye...", "sonra devam ederiz. Tamam mı?"]),
    ("Bilmiyorum… ama araştırırım. Söz veriyorum.", ["Bilmiyorum… ama araştırırım. Söz veriyorum."]),  # kısa parçalar katılır
    ("Bilmiyorum ama bunu sonra araştırırım… Söz veriyorum sana.", ["Bilmiyorum ama bunu sonra araştırırım…", "Söz veriyorum sana."]),
    # Türkçe / düz tırnaklar noktalamayı yutar, bölünme tırnaktan sonra olur
    ('Ahmet "yarın gelirim." dedi ve gitti. Sonra eve döndü.', ['Ahmet "yarın gelirim." dedi ve gitti.', "Sonra eve döndü."]),
    ("Bana »hazırım!« dedi ve çıktı. Hemen eve gittik.", ["Bana »hazırım!« dedi ve çıktı.", "Hemen eve gittik."]),
    ("O sordu: “Nereye gidiyorsun?” Ben cevap verdim.", ["O sordu: “Nereye gidiyorsun?”", "Ben cevap verdim."]),
    # <3 kelime parçalar komşuya katılır
    ("Günaydın. İstanbul'da on dört derece, parçalı bulutlu.", ["Günaydın. İstanbul'da on dört derece, parçalı bulutlu."]),
    ("Tamam. Hemen yapıyorum efendim. Bitti.", ["Tamam. Hemen yapıyorum efendim. Bitti."]),
])
def test_split_table(text, expected):
    assert split_chunks(text) == expected


def test_last_short_piece_joins_previous():
    assert split_chunks("Bugün plan hazır ve her şey tamam. Bitti.") == ["Bugün plan hazır ve her şey tamam. Bitti."]


def test_real_briefing_text_chunks():
    text = ("Günaydın. İstanbul'da on dört derece, parçalı bulutlu. Saat dokuz otuz, Matematik dersi, B204 salonunda. "
            "Saat on dört, Proje toplantısı ve rapor teslimi. Ve üç açık görevin var. Proje notlarını açıyorum.")
    got = split_chunks(text)
    assert got[0] == "Günaydın. İstanbul'da on dört derece, parçalı bulutlu."
    assert len(got) >= 4 and " ".join(got) == text
    assert all(len(c) <= MAX_CHARS for c in got)
    assert "B204" in got[1]  # harf+rakam korunur


def test_long_sentence_splits_on_commas_under_cap():
    clauses = [f"birinci bölüm numara {i} için ayrıntılı bir açıklama" for i in range(12)]
    text = ", ".join(clauses) + "."
    assert len(text) > 3 * MAX_CHARS
    got = split_chunks(text)
    assert len(got) >= 3 and all(len(c) <= MAX_CHARS for c in got)
    assert " ".join(got).replace(",", "").replace(".", "") == text.replace(",", "").replace(".", "")
    assert all(c.endswith(",") or c.endswith(".") for c in got)  # virgül sınırından bölündü, kelime ortasından değil


def test_very_long_sentence_without_commas_splits_on_words():
    text = " ".join(f"kelime{i}" for i in range(80))
    got = split_chunks(text)
    assert len(got) > 1 and all(len(c) <= MAX_CHARS for c in got)
    assert " ".join(got) == text


def test_single_huge_token_is_hard_cut():
    got = split_chunks("x" * 500)
    assert all(len(c) <= MAX_CHARS for c in got) and "".join(got) == "x" * 500


def test_custom_cap_and_min_words():
    got = split_chunks("Bir iki üç dört beş altı yedi sekiz. Dokuz on.", max_chars=30, min_words=3)
    assert all(len(c) <= 30 for c in got)
    assert split_chunks("Evet. Hayır. Belki.", min_words=1) == ["Evet.", "Hayır.", "Belki."]


@pytest.mark.parametrize("text,expected", [
    ("Bitti.", 0.28), ("Gerçekten mi?", 0.28), ("Bekle...", 0.30), ("Önce bu;", 0.16), ("Şöyle:", 0.16),
    ("devam ediyorum", 0.10), ('dedi."', 0.28),
])
def test_pause_after(text, expected):
    assert pause_after(text) == expected
