import datetime as dt

from reis.core import documents as D

TODAY = dt.date(2026, 11, 25)  # Çarşamba


def test_daily_folder_picks_dated_file(tmp_path):
    (tmp_path / "plan-24.11.2026.md").write_text("dün", encoding="utf-8")
    (tmp_path / "plan-2026-11-25.md").write_text("bugün", encoding="utf-8")
    r = D.load_daily(str(tmp_path), TODAY)
    assert r.found and r.markdown == "bugün"
    (tmp_path / "plan-2026-11-25.md").unlink()
    (tmp_path / "x-25.11.2026.md").write_text("alt", encoding="utf-8")
    assert D.load_daily(str(tmp_path), TODAY).markdown == "alt"


def test_daily_folder_missing_day(tmp_path):
    (tmp_path / "2026-11-20.md").write_text("x", encoding="utf-8")
    r = D.load_daily(str(tmp_path), TODAY)
    assert not r.found


def test_daily_single_file_section_and_fallback(tmp_path):
    f = tmp_path / "plan.md"
    f.write_text("# Plan\n\n## 24.11.2026\ndün\n\n## 25.11.2026\nbugün işi\n### alt\nx\n\n## 26.11.2026\nyarın\n",
                 encoding="utf-8")
    r = D.load_daily(str(f), TODAY)
    assert "bugün işi" in r.markdown and "x" in r.markdown
    assert "dün" not in r.markdown and "yarın" not in r.markdown
    f.write_text("# Plan\nhiç tarih yok\n", encoding="utf-8")
    assert "hiç tarih yok" in D.load_daily(str(f), TODAY).markdown


def test_schedule_day_section_not_confused_with_pazartesi(tmp_path):
    f = tmp_path / "prog.md"
    f.write_text("## Pazartesi\nA\n## Salı\nB\n## Çarşamba\nC\n## Pazar\nD\n", encoding="utf-8")
    r = D.load_schedule(str(f), TODAY)
    assert r.markdown.strip().endswith("C") and "B" not in r.markdown
    sunday = D.load_schedule(str(f), dt.date(2026, 11, 29))
    assert "D" in sunday.markdown and "A" not in sunday.markdown


def test_schedule_fallback_whole_file(tmp_path):
    f = tmp_path / "prog.md"
    f.write_text("# Haftalık\nhepsi\n", encoding="utf-8")
    assert "hepsi" in D.load_schedule(str(f), TODAY).markdown


def test_summary_section_truncation():
    text = "# Başlık\nözet satırı\n" + "a" * 3000 + "\n## İkinci\ngizli"
    s = D.summary_section(text)
    assert "gizli" not in s and len(s) <= 1500 and s.startswith("# Başlık")


def test_summary_skips_empty_first_section():
    text = "# Proje\n## Özet\nburada özet\n## Detay\ngizli"
    s = D.summary_section(text)
    assert "burada özet" in s and "gizli" not in s


def test_bom_and_missing(tmp_path):
    f = tmp_path / "asistannote.md"
    f.write_bytes("﻿# Merhaba".encode("utf-8"))
    r = D.load_project("P", str(tmp_path))
    assert r.found and r.markdown == "# Merhaba"
    assert not D.load_project("P", str(tmp_path / "yok")).found
    assert not D.load_project("P", str(tmp_path / "bos")).found
    assert D.load_daily("", TODAY).found is False
    assert D.project_summary(str(tmp_path / "yok")) == ""


def test_project_file_path_and_custom_filename(tmp_path):
    (tmp_path / "x.md").write_text("# X\nm", encoding="utf-8")
    assert D.load_project("P", str(tmp_path / "x.md")).found
    assert D.load_project("P", str(tmp_path), "x.md").found
