from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QFileSystemWatcher, QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QTextBlockFormat
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from ..core import checklist
from ..core.documents import DocResult
from .frameless import FramelessWindow, TitleBar

BIG_FONT_PT = 16


class _Browser(QTextBrowser):
    """Görev satırına (☐ / ☑) tıklamayı yakalayan QTextBrowser."""

    task_clicked = Signal(int)  # sıradaki görev numarası

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if e.button() != Qt.LeftButton or self.textCursor().hasSelection() or self.anchorAt(e.pos()):
            return
        cur = self.cursorForPosition(e.pos())
        r = self.cursorRect(cur)
        if not (r.top() - 2 <= e.pos().y() <= r.bottom() + 2):
            return  # satırların altındaki boşluğa tıklandı
        block = cur.block()
        no_marker = QTextBlockFormat.MarkerType.NoMarker
        if not block.isValid() or block.blockFormat().marker() == no_marker:
            return
        n = 0
        b = self.document().begin()
        while b.isValid() and b.blockNumber() < block.blockNumber():
            if b.blockFormat().marker() != no_marker:
                n += 1
            b = b.next()
        self.task_clicked.emit(n)


class MarkdownViewer(QWidget):
    """Markdown görüntüleyici; kaynak dosya değişince otomatik yenilenir.

    - Görev satırına tıklayınca dosyada tamamlanır (üstü çizilir), tamamlananlar gizlenir.
    - "Büyüt" ile ekran ortasında büyük yazılı ayrı pencere açılır.
    """

    title_changed = Signal(str)  # yalnızca big=True iken belge başlığı değişince

    def __init__(self, parent=None, big: bool = False):
        super().__init__(parent)
        self._big = big
        self.browser = _Browser()
        self.browser.setOpenExternalLinks(True)
        self.browser.task_clicked.connect(self._on_task_clicked)
        if big:
            self.browser.setStyleSheet(f"QTextBrowser {{ font-size: {BIG_FONT_PT}pt; }}")

        self.show_done = QCheckBox("Bitenleri göster")
        self.show_done.toggled.connect(lambda _c: self._render())
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.show_done)
        row.addStretch(1)
        if not big:
            self.big_btn = QPushButton("⤢ Büyüt")
            self.big_btn.setToolTip("Ekranın ortasında büyük pencerede aç")
            self.big_btn.clicked.connect(self.open_big)
            row.addWidget(self.big_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(row)
        lay.addWidget(self.browser, 1)

        self._loader: Callable[[], DocResult] | None = None
        self._res: DocResult | None = None
        self._kept: list[int] = []
        self._tasks: list[int] = []
        self._big_win: ViewerWindow | None = None
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._changed)
        self._watcher.directoryChanged.connect(self._changed)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._debounced_reload)
        self._dirty = False  # görünmezken gelen dosya değişikliği: göründüğünde tek sefer yenilenir
        self._rendered: str | None = None  # son setMarkdown metni (aynıysa yeniden çizilmez)

    # ------------------------------------------------------------ yükleme
    def set_loader(self, loader: Callable[[], DocResult] | None) -> None:
        self._loader = loader
        self.reload()

    def clear(self, msg: str = "") -> None:
        self._loader = None
        self._res = None
        self._unwatch()
        self._rendered = msg
        self.browser.setMarkdown(msg)

    def _unwatch(self):
        for p in self._watcher.files() + self._watcher.directories():
            self._watcher.removePath(p)

    def _debounced_reload(self) -> None:
        if not self.isVisible():
            self._dirty = True  # gizli sekme/pencere: markdown çözümleme ve dosya okuma göründüğünde
            return
        self.reload()

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty:
            self._dirty = False
            self.reload()

    def reload(self) -> None:
        if not self._loader:
            return
        self._dirty = False
        self._res = self._loader()
        self._render()
        self._unwatch()
        paths = [p for p in self._res.watch_paths if p]
        if paths:
            self._watcher.addPaths(paths)
        if self._big:
            self.setWindowTitle(self._res.title or "Reis")
            self.title_changed.emit(self._res.title or "Reis")

    def _render(self) -> None:
        if self._res is None:
            return
        bar = self.browser.verticalScrollBar()
        pos = bar.value()
        lines = self._res.markdown.split("\n")
        out, self._kept, self._tasks = checklist.filter_lines(lines, hide_done=not self.show_done.isChecked())
        text = "\n".join(out)
        if text != self._rendered:  # aynı içerik (ör. dosya aynı baytlarla yeniden yazıldı): QTextDocument'i yeniden kurma
            self.browser.setMarkdown(text)
            self._rendered = text
            bar.setValue(pos)
            # Qt'nin gördüğü görev sayısı bizimkiyle uyuşmuyorsa tıklamayı kapat (yanlış satırı bozmasın).
            self._click_ok = self._count_markers() == len(self._tasks)

    def _count_markers(self) -> int:
        no_marker = QTextBlockFormat.MarkerType.NoMarker
        n = 0
        b = self.browser.document().begin()
        while b.isValid():
            if b.blockFormat().marker() != no_marker:
                n += 1
            b = b.next()
        return n

    def _changed(self, _path: str) -> None:
        self._debounce.start()  # kaydetme sırasında dosya yeniden yaratılabilir; yeniden izlenir

    # ------------------------------------------------------------ görev tıklama
    def _on_task_clicked(self, n: int) -> None:
        res = self._res
        if not getattr(self, "_click_ok", False) or res is None or res.source is None:
            return
        if not (0 <= n < len(self._tasks)):
            return
        disp_idx = self._tasks[n]
        src_idx = self._kept[disp_idx]
        raw = res.markdown.split("\n")[src_idx]
        checklist.toggle_in_file(res.source, res.markdown, src_idx, raw)
        self.reload()  # dosya değişmişse de değişmemişse de güncel hâli göster

    # ------------------------------------------------------------ büyük pencere
    def open_big(self) -> None:
        if not self._loader:
            return
        if self._big_win is None:
            win = ViewerWindow()
            win.viewer.show_done.setChecked(self.show_done.isChecked())
            self._big_win = win
        win = self._big_win
        win.viewer.set_loader(self._loader)
        screen = self.screen() or win.screen()
        geo = screen.availableGeometry()
        w, h = int(geo.width() * 0.55), int(geo.height() * 0.85)
        win.resize(w, h)
        win.move(geo.center().x() - w // 2, geo.center().y() - h // 2)
        win.show()
        win.raise_()
        win.activateWindow()


class ViewerWindow(FramelessWindow):
    """Büyük görüntüleyici penceresi: çerçevesiz, hep üstte, ince başlık çubuğunda belge başlığı, Esc kapatır."""

    def __init__(self, parent=None):
        super().__init__(parent, on_top=True)
        self.viewer = MarkdownViewer(big=True)
        self.title_bar = TitleBar("Reis")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        lay.addWidget(self.title_bar)
        body = QVBoxLayout()
        body.setContentsMargins(8, 6, 8, 8)
        body.addWidget(self.viewer)
        lay.addLayout(body, 1)
        self.attach_title_bar(self.title_bar)
        self.setWindowTitle("Reis")
        self.viewer.title_changed.connect(self.setWindowTitle)
        QShortcut(QKeySequence("Esc"), self, activated=self.close)
