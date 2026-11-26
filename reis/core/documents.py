"""Proje / günlük plan / program belgelerini çözümleme. Yalnızca okur."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

TR_DAYS = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
TR_MONTHS = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
]
MD_SUFFIXES = {".md", ".markdown", ".txt"}
SUMMARY_MAX_CHARS = 1500

_FOLD = str.maketrans("ıöüçşğâîû", "ioucsgaiu")


def fold(s: str) -> str:
    """Türkçe uyumlu küçük harfe çevir ve aksanları sadeleştir."""
    s = s.replace("İ", "i").replace("I", "ı").lower()
    return s.translate(_FOLD)


def weekday_name(d: dt.date) -> str:
    return TR_DAYS[d.weekday()]


@dataclass
class DocResult:
    markdown: str
    title: str = ""
    source: Path | None = None
    found: bool = True
    watch_paths: list = field(default_factory=list)


def read_text(path: Path) -> str | None:
    """UTF-8 (BOM toleranslı) oku. Yoksa None."""
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError:
        return Path(path).read_text(encoding="cp1254", errors="replace")
    except OSError:
        return None


def _missing(title: str, what: str, watch: list | None = None) -> DocResult:
    return DocResult(f"# {title}\n\n_{what}_", title=title, found=False, watch_paths=watch or [])


# ---------------------------------------------------------------- başlıklar
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")


@dataclass
class Heading:
    level: int
    title: str
    start: int  # metindeki karakter ofseti


def parse_headings(text: str) -> list[Heading]:
    out: list[Heading] = []
    pos = 0
    in_fence = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
        elif not in_fence:
            m = _HEADING.match(line.rstrip("\r\n"))
            if m:
                out.append(Heading(len(m.group(1)), m.group(2), pos))
        pos += len(line)
    return out


def extract_section(text: str, predicate) -> str | None:
    """predicate(başlık_metni) doğru olan ilk başlığın bölümü (alt başlıklar dahil)."""
    heads = parse_headings(text)
    for i, h in enumerate(heads):
        if predicate(h.title):
            end = len(text)
            for nxt in heads[i + 1:]:
                if nxt.level <= h.level:
                    end = nxt.start
                    break
            return text[h.start:end].strip() + "\n"
    return None


def summary_section(text: str, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """İlk başlıktan ikinci başlığa kadar olan kısım (en fazla max_chars).

    İlk bölümün gövdesi boşsa (yalnızca başlık) bir sonraki bölüm de dahil edilir.
    """
    heads = parse_headings(text)
    if not heads:
        return text.strip()[:max_chars]
    start = heads[0].start
    if len(heads) == 1:
        end = len(text)
    else:
        end = heads[1].start
        first_body = text[heads[0].start:heads[1].start].split("\n", 1)
        body = first_body[1].strip() if len(first_body) > 1 else ""
        if not body:
            end = heads[2].start if len(heads) > 2 else len(text)
    return text[start:end].strip()[:max_chars]


# ---------------------------------------------------------------- projeler
def resolve_project_file(path: str | Path, default_filename: str = "asistannote.md") -> Path | None:
    p = Path(path)
    if p.is_file():
        return p
    if p.is_dir():
        cand = p / default_filename
        if cand.is_file():
            return cand
    return None


def load_project(name: str, path: str, default_filename: str = "asistannote.md") -> DocResult:
    f = resolve_project_file(path, default_filename)
    watch = [str(path)] if path else []
    if f is None:
        return _missing(name, f"Rapor dosyası bulunamadı: {path} ({default_filename})", watch)
    text = read_text(f)
    if text is None:
        return _missing(name, f"Dosya okunamadı: {f}", watch)
    return DocResult(text, title=name, source=f, watch_paths=[str(f), str(f.parent)])


def project_summary(path: str, default_filename: str = "asistannote.md") -> str:
    f = resolve_project_file(path, default_filename)
    if f is None:
        return ""
    text = read_text(f)
    return summary_section(text) if text else ""


# ---------------------------------------------------------------- tarih
def date_tokens(d: dt.date) -> list[str]:
    y, m, day = d.year, d.month, d.day
    toks: list[str] = []
    for sep in ("-", ".", "/", "_"):
        toks.append(f"{y:04d}{sep}{m:02d}{sep}{day:02d}")
        toks.append(f"{day:02d}{sep}{m:02d}{sep}{y:04d}")
        toks.append(f"{day}{sep}{m}{sep}{y}")
    toks.append(f"{y:04d}{m:02d}{day:02d}")
    toks.append(f"{day:02d}{m:02d}{y:04d}")
    mon = fold(TR_MONTHS[m - 1])
    toks.append(f"{day} {mon} {y}")
    toks.append(f"{day:02d} {mon} {y}")
    return sorted(set(toks), key=len, reverse=True)


def contains_date(text: str, d: dt.date) -> bool:
    ft = fold(text)
    for tok in date_tokens(d):
        if re.search(rf"(?<!\d){re.escape(fold(tok))}(?!\d)", ft):
            return True
    return False


def load_daily(path: str, today: dt.date | None = None) -> DocResult:
    today = today or dt.date.today()
    title = "Günlük Plan"
    if not path:
        return _missing(title, "Günlük plan yolu ayarlanmadı.")
    p = Path(path)
    if p.is_dir():
        matches = [
            f for f in p.iterdir()
            if f.is_file() and f.suffix.lower() in MD_SUFFIXES and contains_date(f.stem, today)
        ]
        if not matches:
            return _missing(title, f"Bugünün ({today.isoformat()}) planı bulunamadı: {p}", [str(p)])
        f = max(matches, key=lambda x: x.stat().st_mtime)
        text = read_text(f)
        if text is None:
            return _missing(title, f"Dosya okunamadı: {f}", [str(p)])
        return DocResult(text, title=title, source=f, watch_paths=[str(p), str(f)])
    text = read_text(p)
    if text is None:
        return _missing(title, f"Dosya bulunamadı: {p}", [str(p.parent)] if p.parent.exists() else [])
    section = extract_section(text, lambda t: contains_date(t, today))
    return DocResult(section if section is not None else text, title=title, source=p,
                     watch_paths=[str(p), str(p.parent)])


def _day_regex(day: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(fold(day))}(?!\w)")


def load_schedule(path: str, today: dt.date | None = None) -> DocResult:
    today = today or dt.date.today()
    title = "Program"
    if not path:
        return _missing(title, "Program dosyası ayarlanmadı.")
    p = Path(path)
    text = read_text(p)
    if text is None:
        return _missing(title, f"Dosya bulunamadı: {p}", [str(p.parent)] if p.parent.exists() else [])
    rx = _day_regex(weekday_name(today))
    section = extract_section(text, lambda t: bool(rx.search(fold(t))))
    return DocResult(section if section is not None else text, title=title, source=p,
                     watch_paths=[str(p), str(p.parent)])
