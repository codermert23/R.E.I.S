import datetime as dt

from reis.core import editor, passwords


def test_parse_passwords_template_format(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("# yorum\nE-Devlet: xxxxxx\n\nLoL : yyy:zzz\nbozuk satır\nBoşDeğer:\n", encoding="utf-8")
    es = passwords.load(f)
    assert [(e.name, e.value) for e in es] == [("E-Devlet", "xxxxxx"), ("LoL", "yyy:zzz")]  # ilk ':' ayırır
    assert passwords.load("") == [] and passwords.load(tmp_path / "yok.txt") == []


def test_passwords_add_delete_keep_file_intact(tmp_path):
    f = tmp_path / "s.txt"
    f.write_bytes("﻿# not\r\nE-Devlet: a1\r\n".encode("utf-8"))
    assert passwords.add(f, "Steam", "p:w d")
    raw = f.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw and b"# not" in raw
    es = passwords.load(f)
    assert [(e.name, e.value) for e in es] == [("E-Devlet", "a1"), ("Steam", "p:w d")]
    assert passwords.delete(f, es[0])
    assert [e.name for e in passwords.load(f)] == ["Steam"]
    assert not passwords.delete(f, es[0])  # satır artık başka: dokunma
    assert not passwords.add(f, "", "x") and not passwords.add(f, "x", "")
    assert passwords.add(tmp_path / "yeni" / "s.txt", "A", "b")  # yoksa oluşturulur


def test_editor_daily_add_into_date_section_and_delete(tmp_path):
    f = tmp_path / "plan.md"
    f.write_text("# 25 Kasım 2026\n- [ ] eski\n\n# 26 Kasım 2026\n- [ ] bir\n\n## Not\nx\n", encoding="utf-8")
    d = dt.date(2026, 11, 25)
    path, pred = editor.daily_target(str(f), d)
    assert editor.add_item(path, "yeni  iş", task=True, pred=pred)
    t = f.read_text(encoding="utf-8").split("\n")
    assert t.index("- [ ] yeni iş") == t.index("- [ ] eski") + 1  # doğru günün bölümünün sonuna
    items = editor.list_items(path, pred)
    assert [i.text for i in items] == ["eski", "yeni iş"]
    assert editor.delete_item(path, items[1])
    assert "yeni iş" not in f.read_text(encoding="utf-8") and "- [ ] bir" in f.read_text(encoding="utf-8")
    assert not editor.delete_item(path, items[1])


def test_editor_daily_folder_creates_todays_file(tmp_path):
    d = dt.date(2026, 11, 25)
    path, pred = editor.daily_target(str(tmp_path), d)
    assert path.name == "2026-11-25.md" and pred is None
    assert editor.add_item(path, "ilk", task=True)
    assert path.read_text(encoding="utf-8").strip() == "- [ ] ilk"


def test_editor_schedule_adds_under_day_or_creates_heading(tmp_path):
    f = tmp_path / "p.md"
    f.write_text("# Program\n\n## Pazartesi\n- 09:00 Mat\n\n## Salı\n- 10:00 Fizik\n", encoding="utf-8")
    assert editor.add_item(f, "13:00 İş", task=False, pred=editor.day_pred("Pazartesi"), new_heading="Pazartesi")
    lines = f.read_text(encoding="utf-8").split("\n")
    assert lines.index("- 13:00 İş") == lines.index("- 09:00 Mat") + 1
    assert editor.add_item(f, "Dinlen", task=False, pred=editor.day_pred("Pazar"), new_heading="Pazar")
    assert "## Pazar" in f.read_text(encoding="utf-8") and f.read_text(encoding="utf-8").rstrip().endswith("- Dinlen")
    assert [i.text for i in editor.list_items(f, editor.day_pred("Salı"))] == ["10:00 Fizik"]


def test_passwords_dpapi_encrypt_roundtrip_and_export(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("# n\nE-Devlet: gizli123\n", encoding="utf-8")
    assert not passwords.is_encrypted(f)
    assert passwords.encrypt_file(f) and passwords.is_encrypted(f)
    assert b"gizli123" not in f.read_bytes() and f.read_text(encoding="utf-8").startswith("REIS-DPAPI1")
    assert [(e.name, e.value) for e in passwords.load(f)] == [("E-Devlet", "gizli123")]
    assert passwords.add(f, "Steam", "p2")  # şifreli kalır
    assert passwords.is_encrypted(f) and b"p2" not in f.read_bytes()
    es = passwords.load(f)
    assert passwords.delete(f, es[0]) and [e.name for e in passwords.load(f)] == ["Steam"]
    out = tmp_path / "yedek.txt"
    assert passwords.export_plain(f, out) and "Steam: p2" in out.read_text(encoding="utf-8")
    assert passwords.encrypt_file(f)  # zaten şifreli: sorun yok


def test_passwords_corrupt_encrypted_file_raises_and_is_untouched(tmp_path):
    import pytest

    from reis.core.dpapi import DpapiError

    f = tmp_path / "s.txt"
    f.write_text("REIS-DPAPI1\nbozuk_veri==\n", encoding="utf-8")
    before = f.read_bytes()
    with pytest.raises(DpapiError):
        passwords.load(f)
    with pytest.raises(DpapiError):
        passwords.add(f, "A", "b")  # çözemediğini ezmez
    assert f.read_bytes() == before
