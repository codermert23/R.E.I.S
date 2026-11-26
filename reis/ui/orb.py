"""Kenar çubuğunun ortasındaki, çok halkalı, duruma göre renklenen küre."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QConicalGradient, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core.state import AssistantState, StateBus
from .animgate import AnimationGate
from .hud_core import STATE_COLORS, blink_factor


def _a(c: QColor, alpha: float) -> QColor:
    out = QColor(c)
    out.setAlphaF(max(0.0, min(1.0, alpha)))
    return out


ACTIVE_MS = 33
IDLE_MS = 50  # hazır durumda yavaş dönüş/nabız için ~20 fps yeter


class OrbWidget(QWidget):
    def __init__(self, bus: StateBus, parent=None):
        super().__init__(parent)
        self.state = bus.state
        self.phase = 0.0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(200, 200)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._last = time.monotonic()
        self._gate = AnimationGate(self, self._tick, IDLE_MS if self.state is AssistantState.IDLE else ACTIVE_MS,
                                   on_resume=self._reset_clock)  # yalnızca görünürken
        bus.state_changed.connect(self.set_state)

    def _reset_clock(self) -> None:
        self._last = time.monotonic()

    def set_state(self, s: AssistantState) -> None:
        self.state = s
        self._gate.set_interval(IDLE_MS if s is AssistantState.IDLE else ACTIVE_MS)
        self.update()

    def _tick(self):
        now = time.monotonic()
        self.phase += min(now - self._last, 0.25)  # saniyede 1.0
        self._last = now
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        st, ph = self.state, self.phase
        c1, c2 = (QColor(x) for x in STATE_COLORS[st])
        p.setOpacity(blink_factor(st, ph))
        cx, cy = self.width() / 2, self.height() / 2
        R = min(self.width(), self.height()) / 2 - 4
        fast = {AssistantState.THINKING: 3.0, AssistantState.SPEAKING: 1.6,
                AssistantState.LISTENING: 1.3}.get(st, 0.6)

        # yumuşak dış ışıma
        glow = QRadialGradient(QPointF(cx, cy), R)
        glow.setColorAt(0.55, _a(c1, 0.0))
        glow.setColorAt(0.8, _a(c1, 0.10))
        glow.setColorAt(1.0, _a(c1, 0.0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), R, R)

        # DİNLİYOR: dışa yayılan dalgalar
        if st is AssistantState.LISTENING:
            for i in range(3):
                t = (ph * 0.7 + i / 3) % 1.0
                rr = R * (0.45 + 0.55 * t)
                p.setPen(QPen(_a(c1, 0.55 * (1 - t)), 2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(cx, cy), rr, rr)

        # 1) en dış ince halka + işaret çizgileri
        p.setPen(QPen(_a(c1, 0.55), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), R, R)
        p.save()
        p.translate(cx, cy)
        p.rotate(math.degrees(ph * 0.25 * fast))
        p.setPen(QPen(_a(c1, 0.8), 2))
        for i in range(60):
            L = 9 if i % 5 == 0 else 4
            p.drawLine(QPointF(R - 3, 0), QPointF(R - 3 - L, 0))
            p.rotate(6)
        p.restore()

        # 2) kesikli, ters dönen halka
        r2 = R * 0.84
        pen = QPen(_a(c1, 0.55), 2)
        pen.setStyle(Qt.CustomDashLine)
        pen.setDashPattern([6, 5])
        pen.setDashOffset(-ph * 14 * fast)
        p.setPen(pen)
        p.drawEllipse(QPointF(cx, cy), r2, r2)

        # 3) dönen kalın yaylar
        r3 = R * 0.72
        rect3 = QRectF(cx - r3, cy - r3, 2 * r3, 2 * r3)
        arc_pen = QPen(_a(c1, 0.95), 5)
        arc_pen.setCapStyle(Qt.RoundCap)
        p.setPen(arc_pen)
        base = -math.degrees(ph * 0.9 * fast)
        for i in range(3):
            p.drawArc(rect3, int((base + i * 120) * 16), int(55 * 16))
        arc_pen2 = QPen(_a(c2, 0.9), 3)
        arc_pen2.setCapStyle(Qt.RoundCap)
        p.setPen(arc_pen2)
        r3b = R * 0.64
        rect3b = QRectF(cx - r3b, cy - r3b, 2 * r3b, 2 * r3b)
        for i in range(2):
            p.drawArc(rect3b, int((-base * 1.4 + i * 180) * 16), int(80 * 16))

        # 4) degrade orta halka
        r4 = R * 0.54
        grad = QConicalGradient(cx, cy, math.degrees(ph * 0.5 * fast))
        grad.setColorAt(0.0, c1)
        grad.setColorAt(0.5, _a(c2, 0.35))
        grad.setColorAt(1.0, c1)
        p.setPen(QPen(grad, 4))
        p.drawEllipse(QPointF(cx, cy), r4, r4)

        # 5) çekirdek küre (nabız)
        if st is AssistantState.SPEAKING:
            pulse = 1 + 0.07 * math.sin(ph * 9) + 0.03 * math.sin(ph * 23)
        elif st is AssistantState.LISTENING:
            pulse = 1 + 0.09 * math.sin(ph * 4.5)
        elif st is AssistantState.THINKING:
            pulse = 1 + 0.03 * math.sin(ph * 6)
        else:
            pulse = 1 + 0.035 * math.sin(ph * 1.6)
        r5 = R * 0.42 * pulse
        core = QRadialGradient(QPointF(cx - r5 * 0.25, cy - r5 * 0.3), r5 * 1.2)
        core.setColorAt(0.0, _a(c1.lighter(150), 0.95))
        core.setColorAt(0.45, _a(c1, 0.55))
        core.setColorAt(1.0, _a(c2.darker(250), 0.9))
        p.setPen(QPen(_a(c1, 0.9), 2))
        p.setBrush(core)
        p.drawEllipse(QPointF(cx, cy), r5, r5)

        # 6) merkezde ses çubukları
        n, bw, gap = 7, R * 0.035, R * 0.03
        total = n * bw + (n - 1) * gap
        x0 = cx - total / 2
        amp = {AssistantState.SPEAKING: 1.0, AssistantState.LISTENING: 0.7,
               AssistantState.THINKING: 0.45}.get(st, 0.25)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 235))
        for i in range(n):
            h = R * 0.07 + R * 0.20 * amp * abs(math.sin(ph * (3 + fast * 2) + i * 0.9))
            p.drawRoundedRect(QRectF(x0 + i * (bw + gap), cy - h / 2, bw, h), bw / 2, bw / 2)
