"""Şifre dosyası: her satır `Ad: değer` (ör. `E-Devlet: xxxxxx`). Boş satırlar ve `#` ile başlayanlar yok sayılır.

Dosya düz metin olabilir ya da Windows DPAPI ile şifrelenmiş olabilir (ilk satır `REIS-DPAPI1`; yalnızca bu Windows
hesabı çözer). Okuma/yazma ikisini de otomatik ele alır. Qt'den bağımsızdır; şifre değerleri asla loglanmaz.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from . import dpapi
from .dpapi import DpapiError
from .textfile import read_lines, write_lines

MAGIC = "REIS-DPAPI1"


@dataclass(frozen=True)
class Entry:
    name: str
    value: str
    line: int  # dosyadaki (çözülmüş) satır numarası, 0'dan


def parse_line(line: str) -> tuple[str, str] | None:
    s = line.strip()
    if not s or s.startswith("#") or ":" not in s:
        return None
    name, value = s.split(":", 1)  # ilk ':' ayırır; şifrenin içinde ':' olabilir
    name, value = name.strip(), value.strip()
    if not name or not value:
        return None
    return name, value


# ---------------------------------------------------------------- okuma/yazma (düz ya da şifreli)
def is_encrypted(path: str | Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)).decode("ascii", "ignore") == MAGIC
    except OSError:
        return False


def _read(path: Path):
    """(satırlar, bom, crlf, şifreli) | None (dosya yok/okunamıyor). Şifreli ama çözülemezse DpapiError."""
    if is_encrypted(path):
        got = read_lines(path)
        if got is None or len(got[0]) < 2:
            raise DpapiError("Şifreli dosya bozuk.")
        try:
            blob = base64.b64decode("".join(got[0][1:]).strip())
        except ValueError as e:
            raise DpapiError("Şifreli dosya bozuk.") from e
        text = dpapi.unprotect(blob).decode("utf-8")
        return text.split("\n"), False, False, True
    got = read_lines(path)
    return None if got is None else (*got, False)


def _write(path: Path, lines: list[str], bom: bool, crlf: bool, enc: bool) -> bool:
    if not enc:
        return write_lines(path, lines, bom, crlf)
    blob = dpapi.protect("\n".join(lines).encode("utf-8"))
    return write_lines(path, [MAGIC, base64.b64encode(blob).decode("ascii"), ""], False, False)


# ---------------------------------------------------------------- liste işlemleri
def load(path: str | Path) -> list[Entry]:
    if not path or not Path(path).exists():
        return []
    got = _read(Path(path))
    if got is None:
        return []
    out = []
    for i, line in enumerate(got[0]):
        pv = parse_line(line)
        if pv:
            out.append(Entry(pv[0], pv[1], i))
    return out


def _clean(s: str) -> str:
    return " ".join((s or "").replace("\r", " ").replace("\n", " ").split())


def add(path: str | Path, name: str, value: str) -> bool:
    """Dosyanın sonuna `Ad: değer` ekler (yoksa düz metin olarak oluşturur)."""
    name, value = _clean(name).replace(":", ""), (value or "").replace("\r", "").replace("\n", "").strip()
    if not name or not value:
        return False
    p = Path(path)
    got = _read(p) if p.exists() else ([""], False, True, False)
    if got is None:
        return False
    lines, bom, crlf, enc = got
    while lines and lines[-1].strip() == "":
        lines.pop()
    lines += [f"{name}: {value}", ""]
    return _write(p, lines, bom, crlf, enc)


def delete(path: str | Path, entry: Entry) -> bool:
    """Satır hâlâ aynı girdiyse siler (dosya arada değişmişse dokunmaz)."""
    p = Path(path)
    got = _read(p)
    if got is None:
        return False
    lines, bom, crlf, enc = got
    if not (0 <= entry.line < len(lines)) or parse_line(lines[entry.line]) != (entry.name, entry.value):
        return False
    del lines[entry.line]
    return _write(p, lines, bom, crlf, enc)


# ---------------------------------------------------------------- şifreleme / yedek
def encrypt_file(path: str | Path) -> bool:
    """Düz metin dosyayı Windows hesabına bağlı şifreli biçime çevirir. Zaten şifreliyse True."""
    p = Path(path)
    if is_encrypted(p):
        return True
    got = _read(p)
    if got is None:
        return False
    lines, _bom, _crlf, _ = got
    if not _write(p, lines, False, False, True):
        return False
    try:  # yazdığımızı geri çözebildiğimizi doğrula; olmuyorsa düz hâli geri koy
        _read(p)
    except DpapiError:
        write_lines(p, lines, _bom, _crlf)
        return False
    return True


def export_plain(path: str | Path, dest: str | Path) -> bool:
    """Yedek: içeriği düz metin olarak `dest`'e yazar (kullanıcı güvenli bir yere koymalı)."""
    got = _read(Path(path))
    if got is None:
        return False
    return write_lines(Path(dest), got[0], got[1], got[2])
