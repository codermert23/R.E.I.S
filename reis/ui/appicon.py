"""Reis uygulama simgesi: dosyadan (reis.ico / reis.png) yüklenir, dosya yoksa kodla çizilir.

Aynı çizim tools/build_icon.py ile reis/assets/icons altındaki PNG/ICO dosyalarına yazılır.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QRadialGradient, QLinearGradient, QIcon, QPixmap

from ..assets import asset_path

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
ORANGE = "#5BC0FF"
DEEP = "#2E9BEA"
_cache: QIcon | None = None


def _c(hexcolor: str, alpha: int = 255) -> QColor:
    c = QColor(hexcolor)
    c.setAlpha(alpha)
    return c


def render_icon(size: int) -> QImage:
    """Koyu yuvarlak kare, turuncu çok halkalı küre ve parlayan çekirdek. Küçük boyutlarda sadeleşir."""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    s = float(size)
    small = size <= 24
    mid = size <= 48

    # --- zemin
    m = 0.03 * s if not small else 0.0
    body = QRectF(m, m, s - 2 * m, s - 2 * m)
    radius = 0.23 * s if not small else 0.20 * s
    bg = QLinearGradient(0, 0, 0, s)
    bg.setColorAt(0, QColor("#12263F"))
    bg.setColorAt(1, QColor("#040A12"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(bg))
    p.drawRoundedRect(body, radius, radius)
    clip = QPainterPath()
    clip.addRoundedRect(body, radius, radius)
    p.setClipPath(clip)

    # --- arkadan gelen parıltı
    cx = cy = s / 2
    glow = QRadialGradient(QPointF(cx, cy), 0.62 * s)
    glow.setColorAt(0.0, _c(ORANGE, 150))
    glow.setColorAt(0.55, _c(DEEP, 46))
    glow.setColorAt(1.0, _c(DEEP, 0))
    p.setBrush(QBrush(glow))
    p.drawRect(QRectF(0, 0, s, s))

    # --- halkalar
    def ring(r: float, w: float, color: QColor, start: float = 0.0, span: float = 360.0):
        pen = QPen(color, w)
        pen.setCapStyle(Qt.RoundCap if span < 360 else Qt.FlatCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        if span >= 360:
            p.drawEllipse(rect)
        else:
            p.drawArc(rect, int(start * 16), int(span * 16))

    if small:
        ring(0.33 * s, max(1.6, 0.12 * s), _c(ORANGE))
        core_r = 0.15 * s
    elif mid:
        ring(0.39 * s, 0.07 * s, _c(ORANGE))
        ring(0.27 * s, 0.05 * s, _c(DEEP, 235))
        core_r = 0.14 * s
    else:
        ring(0.395 * s, 0.020 * s, _c(ORANGE, 120))                    # ince dış halka
        ring(0.355 * s, 0.050 * s, _c(ORANGE), 40, 280)                # kalın yay
        ring(0.275 * s, 0.040 * s, _c(DEEP, 240), 200, 290)            # orta yay
        ring(0.200 * s, 0.022 * s, _c(ORANGE, 170))                    # iç halka
        core_r = 0.125 * s

    # --- çekirdek (parlayan)
    halo = QRadialGradient(QPointF(cx, cy), core_r * 2.6)
    halo.setColorAt(0.0, _c("#A9DCFF", 190))
    halo.setColorAt(0.45, _c(ORANGE, 70))
    halo.setColorAt(1.0, _c(DEEP, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(halo))
    p.drawEllipse(QPointF(cx, cy), core_r * 2.6, core_r * 2.6)
    core = QRadialGradient(QPointF(cx - core_r * 0.2, cy - core_r * 0.25), core_r * 1.15)
    core.setColorAt(0.0, QColor("#EAF6FF"))
    core.setColorAt(0.45, QColor("#8ED2FF"))
    core.setColorAt(1.0, QColor(DEEP))
    p.setBrush(QBrush(core))
    p.drawEllipse(QPointF(cx, cy), core_r, core_r)

    # --- kenar çizgisi
    p.setClipping(False)
    if not small:
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_c(ORANGE, 120 if mid else 105), max(1.0, s / 96)))
        inset = max(0.5, s / 192)
        p.drawRoundedRect(body.adjusted(inset, inset, -inset, -inset), radius, radius)
    p.end()
    return img


def icon_files() -> tuple[str, str]:
    return asset_path("icons", "reis.ico"), asset_path("icons", "reis.png")


def app_icon() -> QIcon:
    """Çok boyutlu uygulama simgesi (önbellekli). Dosya yoksa/yüklenemezse kodla üretir."""
    global _cache
    if _cache is not None and not _cache.isNull():
        return _cache
    ico, png = icon_files()
    icon = QIcon()
    if os.path.exists(ico):
        icon = QIcon(ico)
    if icon.isNull() and os.path.exists(png):
        icon = QIcon(png)
    if icon.isNull():
        for sz in ICON_SIZES:
            icon.addPixmap(QPixmap.fromImage(render_icon(sz)))
    _cache = icon
    return icon


def icon_pixmap(size: int) -> QPixmap:
    """Başlık çubuğu vb. için verilen boyutta (DPI'ya duyarlı değil; çağıran ölçekler)."""
    return app_icon().pixmap(size, size)
