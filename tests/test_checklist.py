from reis.core import checklist


def test_toggle_line_roundtrip():
    done = checklist.toggle_line("- [ ] Market")
    assert done == "- [x] ~~Market~~"
    assert checklist.toggle_line(done) == "- [ ] Market"


def test_filter_hides_done_and_ignores_code_fence():
    lines = ["# A", "- [ ] a", "- [x] ~~b~~", "```", "- [ ] kod", "```", "- [ ] c"]
    out, kept, tasks = checklist.filter_lines(lines, hide_done=True)
    assert "- [x] ~~b~~" not in out
    assert [out[i] for i in tasks] == ["- [ ] a", "- [ ] c"]
    assert [lines[k] for k in kept] == out
    out2, _, tasks2 = checklist.filter_lines(lines, hide_done=False)
    assert len(tasks2) == 3


def test_toggle_in_file_whole_file_crlf_bom(tmp_path):
    f = tmp_path / "n.md"
    f.write_bytes(b"\xef\xbb\xbf# T\r\n- [ ] bir\r\n- [ ] iki\r\n")
    md = f.read_text(encoding="utf-8-sig")
    assert checklist.toggle_in_file(f, md, 2, "- [ ] iki")
    assert f.read_bytes() == b"\xef\xbb\xbf# T\r\n- [ ] bir\r\n- [x] ~~iki~~\r\n"


def test_toggle_in_file_section_offset(tmp_path):
    f = tmp_path / "p.md"
    f.write_text("## 2026-11-24\n- [ ] x\n\n## 2026-11-25\n- [ ] x\n", encoding="utf-8")
    section = "## 2026-11-25\n- [ ] x\n"
    assert checklist.toggle_in_file(f, section, 1, "- [ ] x")
    assert f.read_text(encoding="utf-8") == "## 2026-11-24\n- [ ] x\n\n## 2026-11-25\n- [x] ~~x~~\n"


def test_toggle_in_file_refuses_when_file_changed(tmp_path):
    f = tmp_path / "n.md"
    f.write_text("- [ ] a\n", encoding="utf-8")
    assert not checklist.toggle_in_file(f, "- [ ] a\n", 0, "- [ ] FARKLI")
    assert f.read_text(encoding="utf-8") == "- [ ] a\n"
