"""Çerçevesiz pencere kromu: kendi başlık çubuğu + küçült/büyüt/kapat düğmeleri (mavi HUD stili).

Windows'ta yerel davranış (Aero Snap, Win+Ok, sistem yeniden boyutlandırma imleçleri) WM_NCHITTEST /
WM_NCCALCSIZE / WM_GETMINMAXINFO ile sağlanır. Yerel katman kurulamazsa (başka platform, offscreen, ctypes
hatası) pencere yine çalışır: taşıma startSystemMove, boyutlandırma kenar tutamaçları ile yapılır.
"""
from __future__ import annotations

import logging
import sys
from typing import NamedTuple

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QApplication, QHBoxLayout, QLabel, QWidget

log = logging.getLogger("reis.frameless")

ORANGE = "#5BC0FF"
BORDER_PX = 6          # yeniden boyutlandırma şeridi (mantıksal px)
TITLEBAR_H = 34        # pano üst çubuğunun asgari yüksekliği
SLIM_BAR_H = 30        # brifing / büyük görüntüleyici
BTN_W = 46

# WM_NCHITTEST dönüş değerleri
HTCLIENT, HTCAPTION = 1, 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17
HT_CODES = {
    "client": HTCLIENT, "caption": HTCAPTION, "left": HTLEFT, "right": HTRIGHT, "top": HTTOP,
    "topleft": HTTOPLEFT, "topright": HTTOPRIGHT, "bottom": HTBOTTOM, "bottomleft": HTBOTTOMLEFT,
    "bottomright": HTBOTTOMRIGHT,
}
WM_GETMINMAXINFO, WM_NCCALCSIZE, WM_NCHITTEST = 0x0024, 0x0083, 0x0084


# ---------------------------------------------------------------- saf mantık (birim testli)
def hit_region(x: float, y: float, w: float, h: float, border: float = BORDER_PX,
               titlebar: tuple[float, float, float, float] | None = None, *, maximized: bool = False,
               resizable: bool = True, over_interactive: bool = False) -> str:
    """Pencere içi (mantıksal) noktadan bölge adı: kenar/köşe, 'caption', ya da 'client'.

    titlebar = (x, y, w, h) sürüklenebilir çubuk dikdörtgeni; üzerindeki düğme/giriş gibi etkileşimli
    çocuklarda over_interactive=True verilir (client döner). Kenarlar çubuğun düğmelerinden önceliklidir."""
    if not (0 <= x < w and 0 <= y < h):
        return "client"
    if resizable and not maximized:
        cz = border * 2  # köşe bölgesi kenar şeridinden biraz daha geniş
        nl, nr, nt, nb = x < border, x >= w - border, y < border, y >= h - border
        il, ir, it, ib = x < cz, x >= w - cz, y < cz, y >= h - cz
        if (nt and il) or (nl and it):
            return "topleft"
        if (nt and ir) or (nr and it):
            return "topright"
        if (nb and il) or (nl and ib):
            return "bottomleft"
        if (nb and ir) or (nr and ib):
            return "bottomright"
        top, bottom, left, right = nt, nb, nl, nr
        if top:
            return "top"
        if bottom:
            return "bottom"
        if left:
            return "left"
        if right:
            return "right"
    if titlebar is not None and not over_interactive:
        tx, ty, tw, th = titlebar
        if tx <= x < tx + tw and ty <= y < ty + th:
            return "caption"
    return "client"


class MinMax(NamedTuple):
    pos_x: int
    pos_y: int
    width: int
    height: int


def maximize_geometry(monitor: tuple[int, int, int, int], work: tuple[int, int, int, int]) -> MinMax:
    """WM_GETMINMAXINFO için: ptMaxPosition monitöre göredir, ptMaxSize çalışma alanıdır (görev çubuğu
    hariç). monitor/work = (sol, üst, sağ, alt) fiziksel px."""
    return MinMax(work[0] - monitor[0], work[1] - monitor[1], work[2] - work[0], work[3] - work[1])


def choose_screen_rect(window: tuple[int, int, int, int], avails: list[tuple[int, int, int, int]]):
    """Pencereyle en çok kesişen (yoksa ilk) çalışma alanı; (x, y, w, h) kümeleri."""
    if not avails:
        return None
    best, best_area = avails[0], -1
    wx, wy, ww, wh = window
    for a in avails:
        ix = max(0, min(wx + ww, a[0] + a[2]) - max(wx, a[0]))
        iy = max(0, min(wy + wh, a[1] + a[3]) - max(wy, a[1]))
        if ix * iy > best_area:
            best, best_area = a, ix * iy
    return best


# ---------------------------------------------------------------- Windows yardımcıları (hepsi güvenli)
def set_app_user_model_id(app_id: str = "Reis.Assistant.1") -> bool:
    """Görev çubuğunun python.exe yerine bizim simgemizi göstermesi/gruplaması için. Hata verirse False."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(str(app_id))
        return True
    except Exception:
        log.debug("AppUserModelID ayarlanamadı", exc_info=True)
        return False


def _native_available() -> bool:
    try:
        return sys.platform == "win32" and QApplication.platformName() == "windows"
    except Exception:
        return False


def _win_setup(hwnd: int) -> bool:
    """Stil bitleri (yeniden boyutlandırma/küçült/büyüt/Snap), DWM köşe yuvarlama ve kenar rengi."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        GWL_STYLE = -16
        WS_CAPTION, WS_THICKFRAME, WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_SYSMENU = 0xC00000, 0x40000, 0x20000, 0x10000, 0x80000
        get = user32.GetWindowLongPtrW
        get.restype = ctypes.c_ssize_t
        get.argtypes = [wintypes.HWND, ctypes.c_int]
        setl = user32.SetWindowLongPtrW
        setl.restype = ctypes.c_ssize_t
        setl.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        h = wintypes.HWND(hwnd)
        style = get(h, GWL_STYLE)
        new = style | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU
        if new != style:
            setl(h, GWL_STYLE, new)
            # SWP_NOMOVE|NOSIZE|NOZORDER|NOACTIVATE|FRAMECHANGED
            user32.SetWindowPos(h, None, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0004 | 0x0010 | 0x0020)
    except Exception:
        log.debug("Pencere stili ayarlanamadı", exc_info=True)
        return False
    try:
        import ctypes

        dwm = ctypes.windll.dwmapi
        for attr, val in ((33, 2),                      # DWMWA_WINDOW_CORNER_PREFERENCE = ROUND (Win11)
                          (34, 0x00FFC05B),             # DWMWA_BORDER_COLOR (COLORREF 0x00BBGGRR) = açık mavi
                          (20, 1)):                     # DWMWA_USE_IMMERSIVE_DARK_MODE
            v = ctypes.c_int(val)
            dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attr, ctypes.byref(v), 4)  # eski Windows'ta sessizce hata
    except Exception:
        log.debug("DWM nitelikleri ayarlanamadı", exc_info=True)
    return True


# ---------------------------------------------------------------- düğmeler
class CaptionButton(QAbstractButton):
    """Elle çizilen başlık düğmesi: kind = min | max | restore | close."""

    def __init__(self, kind: str, height: int = TITLEBAR_H, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setCursor(Qt.ArrowCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(BTN_W, height)
        self.setToolTip({"min": "Küçült", "max": "Büyüt", "restore": "Önceki boyut", "close": "Kapat"}[kind])
        self._hover = False

    def set_kind(self, kind: str) -> None:
        if kind != self.kind:
            self.kind = kind
            self.setToolTip({"max": "Büyüt", "restore": "Önceki boyut"}.get(kind, self.toolTip()))
            self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        r = self.rect()
        close = self.kind == "close"
        if self.isDown() or self._hover:
            if close:
                p.fillRect(r, QColor("#D4103A" if self.isDown() else "#FF1744"))
            else:
                bg = QColor(ORANGE)
                bg.setAlpha(80 if self.isDown() else 46)
                p.fillRect(r, bg)
                glow = QColor(ORANGE)
                glow.setAlpha(110)
                p.fillRect(QRect(0, r.height() - 2, r.width(), 2), glow)  # alt kenarda mavi parıltı çizgisi
        col = QColor("#FFFFFF") if close and self._hover else QColor("#E3F2FF" if self._hover else "#B5CCE3")
        pen = QPen(col, 1.0)
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        cx, cy = r.width() // 2, r.height() // 2
        s = 5  # glif yarı boyutu
        if self.kind == "min":
            p.drawLine(cx - s, cy, cx + s, cy)
        elif self.kind == "max":
            p.drawRect(QRectF(cx - s + 0.5, cy - s + 0.5, 2 * s - 1, 2 * s - 1))
        elif self.kind == "restore":
            o = 2
            p.drawRect(QRectF(cx - s + 0.5, cy - s + o + 0.5, 2 * s - o - 1, 2 * s - o - 1))
            p.drawPolyline([QPoint(cx - s + o + 1, cy - s + o), QPoint(cx - s + o + 1, cy - s),
                            QPoint(cx + s - 1, cy - s), QPoint(cx + s - 1, cy + s - o - 1),
                            QPoint(cx + s - o - 1, cy + s - o - 1)])
        else:
            p.setRenderHint(QPainter.Antialiasing)
            p.drawLine(cx - s + 1, cy - s + 1, cx + s - 1, cy + s - 1)
            p.drawLine(cx - s + 1, cy + s - 1, cx + s - 1, cy - s + 1)


class WindowButtons(QWidget):
    """Küçült / büyüt-geri al / kapat üçlüsü."""

    minimize_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, height: int = TITLEBAR_H, parent=None):
        super().__init__(parent)
        self.setObjectName("Transparent")
        self.btn_minimize = CaptionButton("min", height)
        self.btn_maximize = CaptionButton("max", height)
        self.btn_close = CaptionButton("close", height)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        for b in (self.btn_minimize, self.btn_maximize, self.btn_close):
            lay.addWidget(b)
        self.btn_minimize.clicked.connect(self.minimize_clicked)
        self.btn_maximize.clicked.connect(self.maximize_clicked)
        self.btn_close.clicked.connect(self.close_clicked)

    def set_maximized(self, on: bool) -> None:
        self.btn_maximize.set_kind("restore" if on else "max")


class TitleBar(QWidget):
    """İnce başlık çubuğu: simge + başlık + düğmeler (brifing / büyük görüntüleyici)."""

    def __init__(self, title: str = "R.E.I.S", height: int = SLIM_BAR_H, parent=None):
        super().__init__(parent)
        self.setObjectName("ChromeBar")
        self.setFixedHeight(height)
        from .appicon import app_icon

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(16, 16)
        self.icon_label.setScaledContents(True)
        self.icon_label.setPixmap(app_icon().pixmap(32, 32))
        self.title_label = QLabel(title)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setBold(True)
        f.setPixelSize(12)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        self.title_label.setFont(f)
        self.title_label.setStyleSheet(f"color: {ORANGE}; background: transparent;")
        self.buttons = WindowButtons(height)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self.icon_label)
        lay.addWidget(self.title_label, 1)
        lay.addWidget(self.buttons)

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0B1626"))
        p.fillRect(QRect(0, self.height() - 1, self.width(), 1), QColor("#1A2B40"))


# ---------------------------------------------------------------- yeniden boyutlandırma tutamaçları (yedek)
class _Grip(QWidget):
    CURSORS = {"left": Qt.SizeHorCursor, "right": Qt.SizeHorCursor, "top": Qt.SizeVerCursor,
               "bottom": Qt.SizeVerCursor, "topleft": Qt.SizeFDiagCursor, "bottomright": Qt.SizeFDiagCursor,
               "topright": Qt.SizeBDiagCursor, "bottomleft": Qt.SizeBDiagCursor}
    EDGES = {"left": Qt.LeftEdge, "right": Qt.RightEdge, "top": Qt.TopEdge, "bottom": Qt.BottomEdge,
             "topleft": Qt.TopEdge | Qt.LeftEdge, "topright": Qt.TopEdge | Qt.RightEdge,
             "bottomleft": Qt.BottomEdge | Qt.LeftEdge, "bottomright": Qt.BottomEdge | Qt.RightEdge}

    def __init__(self, name: str, parent: QWidget):
        super().__init__(parent)
        self.name = name
        self.setCursor(self.CURSORS[name])
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            wh = self.window().windowHandle()
            if wh is not None:
                wh.startSystemResize(self.EDGES[self.name])


# ---------------------------------------------------------------- ana sınıf
class FramelessWindow(QWidget):
    """Çerçevesiz üst düzey pencere tabanı. Alt sınıf kendi düzenini kurar, sonra `attach_chrome` çağırır."""

    def __init__(self, parent=None, *, on_top: bool = False, resizable: bool = True):
        super().__init__(parent)
        flags = Qt.Window | Qt.FramelessWindowHint | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
        if on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        from .appicon import app_icon

        self.setWindowIcon(app_icon())
        self._resizable = resizable
        self._bar: QWidget | None = None
        self._buttons: WindowButtons | None = None
        self._title_bar: TitleBar | None = None
        self._native = _native_available()
        self._native_done = False
        self._grips: dict[str, _Grip] = {}
        if not self._native and resizable:
            for n in _Grip.CURSORS:
                self._grips[n] = _Grip(n, self)

    # ---- kurulum
    def attach_chrome(self, bar: QWidget, buttons: WindowButtons) -> None:
        """bar: sürüklenebilir başlık alanı (düğmeler dahil), buttons: üç düğme."""
        self._bar = bar
        self._buttons = buttons
        buttons.minimize_clicked.connect(self.showMinimized)
        buttons.maximize_clicked.connect(self.toggle_maximized)
        buttons.close_clicked.connect(self.close)
        bar.installEventFilter(self)
        self._sync_chrome()
        self._raise_grips()

    def attach_title_bar(self, title_bar: TitleBar) -> None:
        self._title_bar = title_bar
        self.attach_chrome(title_bar, title_bar.buttons)

    def setWindowTitle(self, t: str) -> None:  # noqa: N802
        super().setWindowTitle(t)
        if self._title_bar is not None:
            self._title_bar.set_title(t)

    # ---- durum
    def is_chrome_maximized(self) -> bool:
        return bool(self.windowState() & (Qt.WindowMaximized | Qt.WindowFullScreen))

    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _sync_chrome(self) -> None:
        mx = self.is_chrome_maximized()
        if self._buttons is not None:
            self._buttons.set_maximized(self.isMaximized())
        lay = self.layout()
        if lay is not None:
            m = 0 if mx else 1
            lay.setContentsMargins(m, m, m, m)
        for g in self._grips.values():
            g.setVisible(not mx)
        self.update()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.WindowStateChange:
            self._sync_chrome()

    def eventFilter(self, obj, e):
        # Yedek (yerel olmayan) mod: çubuk boşluğunda sürükle / çift tıkla büyüt
        if obj is self._bar and not self._native:
            t = e.type()
            if t == QEvent.Type.MouseButtonDblClick and e.button() == Qt.LeftButton and self._over_bar_empty(e.position().toPoint()):
                self.toggle_maximized()
                return True
            if t == QEvent.Type.MouseButtonPress and e.button() == Qt.LeftButton and self._over_bar_empty(e.position().toPoint()):
                wh = self.windowHandle()
                if wh is not None and not self.isMaximized():
                    wh.startSystemMove()
                return True
        return super().eventFilter(obj, e)

    def _over_bar_empty(self, pos_in_bar: QPoint) -> bool:
        w = self._bar.childAt(pos_in_bar) if self._bar else None
        return not self._interactive(w)

    @staticmethod
    def _interactive(w: QWidget | None) -> bool:
        from PySide6.QtWidgets import QAbstractScrollArea, QLineEdit

        while w is not None:
            if isinstance(w, (QAbstractButton, QLineEdit, QAbstractScrollArea)) or w.property("noDrag"):
                return True
            w = w.parentWidget()
        return False

    # ---- yerleşim / çizim
    def _raise_grips(self) -> None:
        for g in self._grips.values():
            g.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._grips:
            w, h, b = self.width(), self.height(), BORDER_PX
            c = b * 2
            geo = {"left": (0, c, b, h - 2 * c), "right": (w - b, c, b, h - 2 * c), "top": (c, 0, w - 2 * c, b),
                   "bottom": (c, h - b, w - 2 * c, b), "topleft": (0, 0, c, c), "topright": (w - c, 0, c, c),
                   "bottomleft": (0, h - c, c, c), "bottomright": (w - c, h - c, c, c)}
            for n, g in self._grips.items():
                g.setGeometry(*geo[n])
            self._raise_grips()

    def paint_chrome(self, p: QPainter) -> None:
        """Turuncu 1 px kenarlık + hafif iç parıltı (büyütülmüşken yok)."""
        if self.is_chrome_maximized():
            return
        w, h = self.width(), self.height()
        for i, a in ((2, 10), (1, 22)):  # iç parıltı
            c = QColor(ORANGE)
            c.setAlpha(a)
            p.setPen(QPen(c, 1))
            p.drawRect(QRect(i, i, w - 1 - 2 * i, h - 1 - 2 * i))
        c = QColor(ORANGE)
        c.setAlpha(170)
        p.setPen(QPen(c, 1))
        p.drawRect(QRect(0, 0, w - 1, h - 1))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#07101C"))
        self.paint_chrome(p)

    # ---- yerel katman
    def showEvent(self, e):
        super().showEvent(e)
        if self._native and not self._native_done:
            try:
                ok = _win_setup(int(self.winId()))
            except Exception:
                ok = False
            self._native_done = True
            if not ok:
                self._native = False
                log.warning("Yerel pencere kromu kurulamadı; yedek moda geçildi")

    def _caption_rect(self) -> tuple[float, float, float, float] | None:
        if self._bar is None or not self._bar.isVisible():
            return None
        tl = self._bar.mapTo(self, QPoint(0, 0))
        return (tl.x(), tl.y(), self._bar.width(), self._bar.height())

    def hit_test_local(self, x: float, y: float) -> str:
        """Pencere içi mantıksal nokta için bölge adı (nativeEvent ve testler kullanır)."""
        over = False
        if self._bar is not None:
            w = self.childAt(int(x), int(y))
            over = self._interactive(w)
        return hit_region(x, y, self.width(), self.height(), BORDER_PX, self._caption_rect(),
                          maximized=self.is_chrome_maximized(), resizable=self._resizable and self.maximumSize() != self.minimumSize(),
                          over_interactive=over)

    def nativeEvent(self, eventType, message):  # noqa: N802
        if not (self._native and self._native_done):
            return super().nativeEvent(eventType, message)
        try:
            import ctypes
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_NCCALCSIZE and msg.wParam:
                # İstemci alanı = tüm pencere (sistem çerçevesi çizilmez). Büyütülmüşken Windows pencereyi
                # çerçeve kalınlığı kadar ekran dışına taşır; istemci alanı çalışma alanına (görev çubuğu
                # hariç) sabitlenir.
                if ctypes.windll.user32.IsZoomed(wintypes.HWND(msg.hWnd)):
                    work = self._monitor_work_rect(msg.hWnd)
                    if work is not None:
                        class _NCP(ctypes.Structure):
                            _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]

                        ncp = _NCP.from_address(int(msg.lParam))
                        r = ncp.rgrc[0]
                        r.left, r.top, r.right, r.bottom = work
                return True, 0
            if msg.message == WM_NCHITTEST:
                lp = msg.lParam
                sx, sy = ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value
                rc = wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(wintypes.HWND(msg.hWnd), ctypes.byref(rc))
                dpr = self.devicePixelRatioF() or 1.0
                region = self.hit_test_local((sx - rc.left) / dpr, (sy - rc.top) / dpr)
                return True, HT_CODES[region]
            if msg.message == WM_GETMINMAXINFO:
                return self._on_minmax(msg)
        except Exception:
            log.debug("nativeEvent hatası", exc_info=True)
        return super().nativeEvent(eventType, message)

    @staticmethod
    def _monitor_info(hwnd):
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]

        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        hmon = user32.MonitorFromWindow(wintypes.HWND(hwnd), 2)  # MONITOR_DEFAULTTONEAREST
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not hmon or not user32.GetMonitorInfoW(wintypes.HANDLE(hmon), ctypes.byref(mi)):
            return None
        return ((mi.rcMonitor.left, mi.rcMonitor.top, mi.rcMonitor.right, mi.rcMonitor.bottom),
                (mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom))

    def _monitor_work_rect(self, hwnd):
        info = self._monitor_info(hwnd)
        return info[1] if info else None

    def _on_minmax(self, msg):
        import ctypes
        from ctypes import wintypes

        class MINMAXINFO(ctypes.Structure):
            _fields_ = [("ptReserved", wintypes.POINT), ("ptMaxSize", wintypes.POINT),
                        ("ptMaxPosition", wintypes.POINT), ("ptMinTrackSize", wintypes.POINT),
                        ("ptMaxTrackSize", wintypes.POINT)]

        mon = self._monitor_info(msg.hWnd)
        if mon is None:
            return False, 0
        g = maximize_geometry(*mon)
        info = MINMAXINFO.from_address(int(msg.lParam))
        info.ptMaxPosition.x, info.ptMaxPosition.y = g.pos_x, g.pos_y
        info.ptMaxSize.x, info.ptMaxSize.y = g.width, g.height
        dpr = self.devicePixelRatioF() or 1.0
        mn = self.minimumSize()
        info.ptMinTrackSize.x, info.ptMinTrackSize.y = int(mn.width() * dpr), int(mn.height() * dpr)
        mx = self.maximumSize()
        if mx.width() < 16777215 and mx.height() < 16777215:
            info.ptMaxTrackSize.x, info.ptMaxTrackSize.y = int(mx.width() * dpr), int(mx.height() * dpr)
        return True, 0
