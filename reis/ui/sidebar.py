from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

from ..config.settings import Settings
from ..core.services import sysinfo
from ..core.services import weather as weather_svc
from ..core.state import STATE_LABELS, AssistantState, StateBus
from .animgate import AnimationGate
from .docs_window import DAILY, SCHEDULE
from .hud_core import STATE_COLORS
from .input_bar import InputBar
from .orb import OrbWidget
from .workers import run_async

WIDTH = 300
SQUARE = 52          # daraltılmış hâl: ekranın sağında küçük kare tıklama alanı
SYS_MS = 3000
WEATHER_MS = 15 * 60 * 1000


class Sidebar(QWidget):
    collapsed_changed = Signal(bool)
    docs_requested = Signal(str)  # sekme başlığı; "" = projeler
    passwords_requested = Signal()

    def __init__(self, settings: Settings, bus: StateBus):
        super().__init__()
        self.settings = settings
        self.bus = bus
        self.collapsed = settings.collapsed
        self._busy = False
        self.setWindowTitle("Reis")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)

        # daraltılmış kare
        self.square = QToolButton()
        self.square.setObjectName("Square")
        self.square.setText("R")
        self.square.setToolTip("Reis'i aç")
        self.square.clicked.connect(self.toggle_collapsed)

        # üst: hava durumu + sistem kullanımı + daralt
        self.weather = QLabel("")
        self.weather.setObjectName("Weather")
        self.sys = QLabel("")
        self.sys.setObjectName("SysLine")
        fold = QToolButton()
        fold.setObjectName("Fold")
        fold.setText("»")
        fold.setToolTip("Daralt")
        fold.clicked.connect(self.toggle_collapsed)
        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(self.weather)
        info.addWidget(self.sys)
        top = QHBoxLayout()
        top.addLayout(info, 1)
        top.addWidget(fold, 0, Qt.AlignTop)

        self.orb = OrbWidget(bus)
        self.state_label = QLabel(STATE_LABELS[bus.state])
        self.state_label.setObjectName("StateLabel")
        self.state_label.setAlignment(Qt.AlignCenter)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("Subtitle")
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setWordWrap(True)
        self.subtitle.setMinimumHeight(36)

        docs = QGridLayout()
        docs.setSpacing(6)
        for n, (text, key) in enumerate((("PROJELER", ""), ("GÜNLÜK PLAN", DAILY), ("PROGRAM", SCHEDULE),
                                         ("ŞİFRELER", None))):
            b = QPushButton(text)
            b.setObjectName("DocBtn")
            if key is None:
                b.clicked.connect(self.passwords_requested)
            else:
                b.clicked.connect(lambda _c=False, k=key: self.docs_requested.emit(k))
            docs.addWidget(b, n // 2, n % 2)

        self.input_bar = InputBar()
        self.input_frame = QFrame()
        self.input_frame.setObjectName("InputFrame")
        il = QVBoxLayout(self.input_frame)
        il.setContentsMargins(0, 6, 0, 0)
        il.addWidget(self.input_bar)

        self.content = QWidget()
        self.content.setObjectName("Content")
        cl = QVBoxLayout(self.content)
        cl.setContentsMargins(12, 10, 12, 12)
        cl.addLayout(top)
        cl.addWidget(self.orb, 1)
        cl.addWidget(self.state_label)
        cl.addWidget(self.subtitle)
        cl.addLayout(docs)
        cl.addWidget(self.input_frame)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.square)
        root.addWidget(self.content, 1)

        bus.state_changed.connect(self._on_state)
        bus.subtitle_changed.connect(self.subtitle.setText)
        self._on_state(bus.state)

        # Sistem kullanımı yalnızca görünürken (genişletilmiş) güncellenir; hava durumu 15 dk'da bir.
        try:
            sysinfo.snapshot()  # cpu_percent'in ilk çağrısı anlamsız; ısıt
        except Exception:
            pass
        self._sys_gate = AnimationGate(self.content, self._sys_tick, SYS_MS, on_resume=self._sys_tick)
        self._wx_timer = QTimer(self)
        self._wx_timer.timeout.connect(self.refresh_weather)
        self._wx_timer.start(WEATHER_MS)
        QTimer.singleShot(1500, self.refresh_weather)
        self.apply_collapsed()

    # ---- durum
    def _on_state(self, s: AssistantState):
        self.state_label.setText(STATE_LABELS[s])
        for w in (self.state_label, self.input_frame):
            w.setProperty("state", s.value)
            w.style().unpolish(w)
            w.style().polish(w)
        self.update()

    # ---- üst bilgi
    def _sys_tick(self):
        try:
            s = sysinfo.snapshot()
            self.sys.setText(f"CPU %{s.cpu:.0f}  ·  RAM %{s.ram:.0f}  ·  Disk %{s.disk:.0f}")
        except Exception:
            self.sys.setText("")

    def refresh_weather(self):
        city = (self.settings.city or "").strip()
        if not city:
            self.weather.setText("")
            return
        cached = weather_svc.SHARED_CACHE.get(city.casefold())
        if cached is not None:
            self._set_weather(cached)
            return
        if self._busy:
            return
        self._busy = True

        def done(w):
            self._busy = False
            weather_svc.SHARED_CACHE.set(city.casefold(), w)
            self._set_weather(w)

        def failed(_m):
            self._busy = False
            self.weather.setText(f"{city} · çevrimdışı")

        run_async(lambda: weather_svc.fetch_weather(city), done, failed)

    def _set_weather(self, w):
        self.weather.setText(f"{w.city} {w.temp:.0f}°C · {w.description}")

    # ---- yerleşim
    def apply_collapsed(self):
        self.content.setVisible(not self.collapsed)
        self.square.setVisible(self.collapsed)
        self.dock()

    def toggle_collapsed(self):
        self.collapsed = not self.collapsed
        self.settings.collapsed = self.collapsed
        self.apply_collapsed()
        self.collapsed_changed.emit(self.collapsed)

    def dock(self):
        screen = self.screen() or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        if self.collapsed:
            self.setFixedSize(SQUARE, SQUARE)
            self.setGeometry(geo.right() - SQUARE + 1, geo.center().y() - SQUARE // 2, SQUARE, SQUARE)
        else:
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.setFixedWidth(WIDTH)
            self.setGeometry(geo.right() - WIDTH + 1, geo.top(), WIDTH, geo.height())

    def show_item(self, item) -> None:
        if item.type == "project" and item.name:
            self.docs_requested.emit(item.name)
        elif item.type == "daily_plan":
            self.docs_requested.emit(DAILY)
        elif item.type == "schedule":
            self.docs_requested.emit(SCHEDULE)

    # ---- çizim: arka plan + köşe braketleri
    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#07101C"))
        col = QColor(STATE_COLORS[self.bus.state][0])
        p.setPen(QPen(col, 2))
        w, h, L = self.width() - 1, self.height() - 1, 12 if self.collapsed else 16
        for x, y, dx, dy in ((0, 0, 1, 1), (w, 0, -1, 1), (0, h, 1, -1), (w, h, -1, -1)):
            p.drawLine(x, y, x + dx * L, y)
            p.drawLine(x, y, x, y + dy * L)
        edge = QColor(col)
        edge.setAlpha(70)
        p.setPen(QPen(edge, 1))
        p.drawLine(0, 0, 0, h)

    def closeEvent(self, e):
        e.ignore()
        self.hide()
