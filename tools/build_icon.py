"""reis/assets/icons altına reis.png (256) ve çok boyutlu reis.ico üretir.

Kullanim: python tools/build_icon.py (venv ile)
Saf kod (QPainter); dış varlık gerekmez. ICO, PNG sıkıştırmalı girdilerle elle yazılır.
"""
from __future__ import annotations

import os
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402

from reis.ui.appicon import ICON_SIZES, render_icon  # noqa: E402


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def build_ico(images: dict[int, bytes]) -> bytes:
    sizes = sorted(images)
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, blobs = b"", b""
    for sz in sizes:
        data = images[sz]
        entries += struct.pack("<BBBBHHII", sz % 256, sz % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return head + entries + blobs


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reis", "assets", "icons")
    os.makedirs(out, exist_ok=True)
    pngs = {sz: png_bytes(render_icon(sz)) for sz in ICON_SIZES}
    with open(os.path.join(out, "reis.png"), "wb") as f:
        f.write(pngs[256])
    with open(os.path.join(out, "reis.ico"), "wb") as f:
        f.write(build_ico(pngs))
    print("yazildi:", os.path.normpath(out), {k: len(v) for k, v in pngs.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
