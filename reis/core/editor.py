"""Günlük plan / program dosyasına madde ekleme ve silme (Markdown satırı olarak).

Günlük plan: `- [ ] metin` eklenir. Program: gün başlığının (## Çarşamba) altına `- metin` eklenir.
Dosyanın geri kalanına, BOM'a ve satır sonlarına dokunulmaz.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import documents as docs
from .textfile import read_lines, write_lines

_HEAD = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s*)?(?P<body>.+)$")


@dataclass(frozen=True)
class Item:
    line: int
    text: str  # ekranda gösterilecek metin
    raw: str   # dosyadaki satır (silmeden önce doğrulanır)


def _sections(lines: list[str]) -> list[tuple[int, int, str]]:
    """[(başlık_satırı, bölüm_sonu_satırı(hariç), başlık_metni)]; kod bloklarındaki '#' yok sayılır."""
    heads: list[tuple[int, int, str]] = []
    fence = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("```") or s.startswith("~~~"):
            fence = not fence
        elif not fence:
            m = _HEAD.match(line)
            if m:
                heads.append((i, len(m.group(1)), m.group(2)))
    out = []
    for k, (i, lvl, title) in enumerate(heads):
        end = len(lines)
        for j, l2, _t in heads[k + 1:]:
            if l2 <= lvl:
                end = j
                break
        out.append((i, end, title))
    return out


def _find(lines: list[str], pred: Callable[[str], bool] | None) -> tuple[int, int] | None:
    if pred is None:
        return None
    for start, end, title in _sections(lines):
        if pred(title):
            return start, end
    return None


def day_pred(day: str) -> Callable[[str], bool]:
    rx = docs._day_regex(day)
    return lambda t: bool(rx.search(docs.fold(t)))


def date_pred(d: dt.date) -> Callable[[str], bool]:
    return lambda t: docs.contains_date(t, d)


def add_item(path: str | Path, text: str, *, task: bool, pred: Callable[[str], bool] | None = None,
             new_heading: str = "") -> bool:
    """Bölümün (pred) sonuna, bölüm yoksa dosyanın sonuna ekler. new_heading verilmişse ve bölüm yoksa
    önce o başlık açılır. Dosya yoksa oluşturulur."""
    text = " ".join((text or "").split())
    if not text:
        return False
    line = f"- [ ] {text}" if task else f"- {text}"
    p = Path(path)
    got = read_lines(p) if p.exists() else ([""], False, True)
    if got is None:
        return False
    lines, bom, crlf = got
    sec = _find(lines, pred)
    if sec is None and new_heading:
        while lines and lines[-1].strip() == "":
            lines.pop()
        lines += ["", f"## {new_heading}", line, ""]
        return write_lines(p, lines, bom, crlf)
    lo, hi = sec if sec else (0, len(lines))
    at = hi
    while at > lo and lines[at - 1].strip() == "":
        at -= 1
    lines.insert(at, line)
    if not any(l.strip() for l in lines[at + 1:]) and lines[-1].strip():
        lines.append("")
    return write_lines(p, lines, bom, crlf)


def list_items(path: str | Path, pred: Callable[[str], bool] | None = None) -> list[Item]:
    got = read_lines(Path(path))
    if got is None:
        return []
    lines = got[0]
    lo, hi = _find(lines, pred) or (0, len(lines))
    out = []
    fence = False
    for i in range(lo, hi):
        s = lines[i].strip()
        if s.startswith("```") or s.startswith("~~~"):
            fence = not fence
            continue
        m = None if fence else _ITEM.match(lines[i])
        if m:
            out.append(Item(i, m.group("body").strip(), lines[i]))
    return out


def delete_item(path: str | Path, item: Item) -> bool:
    p = Path(path)
    got = read_lines(p)
    if got is None:
        return False
    lines, bom, crlf = got
    if not (0 <= item.line < len(lines)) or lines[item.line] != item.raw:
        return False
    del lines[item.line]
    return write_lines(p, lines, bom, crlf)


def daily_target(path: str, today: dt.date | None = None) -> tuple[Path | None, Callable[[str], bool] | None]:
    """Günlük plana eklenecek dosya ve bölüm seçici. Klasörde bugünün dosyası yoksa `YYYY-AA-GG.md` olur."""
    today = today or dt.date.today()
    if not path:
        return None, None
    p = Path(path)
    if p.is_dir():
        res = docs.load_daily(path, today)
        return (res.source if res.source else p / f"{today.isoformat()}.md"), None
    return p, date_pred(today)
