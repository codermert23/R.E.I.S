"""Markdown görev listesi (`- [ ]` / `- [x]`) işlemleri.

Görüntüleyicide bir göreve tıklanınca dosyada `[x]` yapılır ve metnin üstü çizilir (~~metin~~);
tamamlananlar arayüzde varsayılan olarak gizlenir. Bu modül Qt'den bağımsızdır.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_TASK = re.compile(r"^(?P<pre>\s*(?:[-*+]|\d+[.)])\s+)\[(?P<mark>[ xX])\](?P<sp>[ \t]*)(?P<body>.*)$")
_STRIKE = re.compile(r"^~~(?P<inner>.*)~~$")


def _is_fence(line: str) -> bool:
    s = line.strip()
    return s.startswith("```") or s.startswith("~~~")


def parse_task(line: str):
    return _TASK.match(line)


def is_done(line: str) -> bool:
    m = parse_task(line)
    return bool(m) and m.group("mark") in "xX"


def toggle_line(line: str) -> str:
    """Tamamlanmamış görevi tamamla (+üstünü çiz), tamamlanmışı geri al."""
    m = parse_task(line)
    if not m:
        return line
    pre, body = m.group("pre"), m.group("body").rstrip()
    sp = m.group("sp") or " "
    if m.group("mark") == " ":
        if body and not _STRIKE.match(body):
            body = f"~~{body}~~"
        return f"{pre}[x]{sp}{body}"
    s = _STRIKE.match(body)
    if s:
        body = s.group("inner")
    return f"{pre}[ ]{sp}{body}"


def filter_lines(lines: list[str], hide_done: bool):
    """(gösterilecek_satırlar, kaynak_indeksleri, görev_satırı_konumları) döndürür.

    Kod bloklarının içindeki görev benzeri satırlar görev sayılmaz.
    görev_satırı_konumları: gösterilecek_satırlar içindeki görev satırlarının indeksleri (sırayla).
    """
    out: list[str] = []
    kept: list[int] = []
    tasks: list[int] = []
    in_fence = False
    for i, line in enumerate(lines):
        if _is_fence(line):
            in_fence = not in_fence
            out.append(line)
            kept.append(i)
            continue
        if not in_fence and parse_task(line):
            if hide_done and is_done(line):
                continue
            tasks.append(len(out))
        out.append(line)
        kept.append(i)
    return out, kept, tasks


def toggle_in_file(path: str | Path, section_md: str, section_line: int, expected_line: str) -> bool:
    """`section_md` dosyanın (ya da bir bölümünün) gösterilen metni; `section_line` onun içindeki satır.

    Dosyadaki satır `expected_line` ile birebir aynı değilse (dosya arada değişmiş) hiçbir şey yazmaz
    ve False döner. Satır sonu (CRLF) ve BOM korunur; UTF-8 olmayan dosyalara dokunulmaz.
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError:
        return False
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")

    idx = text.find(section_md)
    if idx < 0:
        idx = text.find(section_md.strip("\n"))
        if idx < 0:
            return False
    file_lines = text.split("\n")
    target = text[:idx].count("\n") + section_line
    if not (0 <= target < len(file_lines)) or file_lines[target] != expected_line:
        return False
    new_line = toggle_line(expected_line)
    if new_line == expected_line:
        return False
    file_lines[target] = new_line
    out = "\n".join(file_lines)
    if crlf:
        out = out.replace("\n", "\r\n")
    data = out.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data

    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".reis-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    return True
