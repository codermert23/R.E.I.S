"""Metin dosyasını satır satır güvenli düzenleme: BOM ve satır sonu (CRLF) korunur, yazma atomiktir."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def read_lines(path: Path) -> tuple[list[str], bool, bool] | None:
    """(satırlar, bom_var, crlf_var). Dosya yoksa/okunamazsa/UTF-8 değilse None."""
    try:
        raw = Path(path).read_bytes()
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    bom = raw.startswith(b"\xef\xbb\xbf")
    crlf = "\r\n" in text
    return text.replace("\r\n", "\n").split("\n"), bom, crlf


def write_lines(path: Path, lines: list[str], bom: bool = False, crlf: bool = False) -> bool:
    p = Path(path)
    out = "\n".join(lines)
    if crlf:
        out = out.replace("\n", "\r\n")
    data = out.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".reis-", suffix=".tmp")
    except OSError:
        return False
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
