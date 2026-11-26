"""Notlar penceresi: projeler, günlük plan ve program ayrı sekmeler olarak büyük yazıyla açılır.

Günlük Plan ve Program sekmelerinde alttan madde ekleme / silme vardır (dosyayı elle bulmaya gerek kalmaz).
"""
from __future__ import annotations

import datetime as dt

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QPushButton, QTabWidget, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt

from ..config.settings import Settings
from ..core import editor
from ..core.documents import TR_DAYS, load_daily, load_project, load_schedule, weekday_name
from .frameless import FramelessWindow, TitleBar
from .viewer import MarkdownViewer

DAILY, SCHEDULE = "Günlük Plan", "Program"


class PlanTab(QWidget):
    """Görüntüleyici + alt çubukta 'ekle' ve 'sil'. kind: 'daily' | 'schedule'."""

    def __init__(self, kind: str, settings: Settings, loader, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.settings = settings
        self.viewer = MarkdownViewer(big=True)
        self.viewer.set_loader(loader)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Yeni görev (örn. 14:00 Ders)" if kind == "daily"
                                     else "Yeni madde (örn. 14:00–16:00 Fizik)")
        self.edit.returnPressed.connect(self.add)
        self.day = QComboBox()
        self.day.addItems(TR_DAYS)
        self.day.setCurrentText(weekday_name(dt.date.today()))
        add = QPushButton("Ekle")
        add.clicked.connect(self.add)
        rm = QPushButton("Sil…")
        rm.clicked.connect(self.remove)
        self.status = QLabel("")
        self.status.setObjectName("PathLabel")
        bar = QHBoxLayout()
        if kind == "schedule":
            bar.addWidget(self.day)
        bar.addWidget(self.edit, 1)
        bar.addWidget(add)
        bar.addWidget(rm)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.viewer, 1)
        lay.addLayout(bar)
        lay.addWidget(self.status)

    # (dosya, bölüm seçici, yeni başlık) — ayarlar her seferinde güncel okunur
    def _target(self):
        if self.kind == "daily":
            path, pred = editor.daily_target(self.settings.daily_path)
            return path, pred, ""
        if not self.settings.schedule_path:
            return None, None, ""
        day = self.day.currentText()
        return self.settings.schedule_path, editor.day_pred(day), day

    def add(self) -> None:
        path, pred, heading = self._target()
        if path is None:
            self.status.setText("Önce Ayarlar > Notlar'dan dosya yolunu ayarla.")
            return
        text = self.edit.text().strip()
        if not text:
            return
        if editor.add_item(path, text, task=self.kind == "daily", pred=pred, new_heading=heading):
            self.edit.clear()
            self.status.setText("Eklendi.")
            self.viewer.reload()
        else:
            self.status.setText("Eklenemedi (dosya yazılabilir mi?).")

    def remove(self) -> None:
        path, pred, _h = self._target()
        if path is None:
            self.status.setText("Önce Ayarlar > Notlar'dan dosya yolunu ayarla.")
            return
        items = editor.list_items(path, pred)
        if not items:
            self.status.setText("Silinecek madde yok.")
            return
        dlg = QDialog(self)
        dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dlg.setWindowTitle("Madde sil")
        dlg.setMinimumWidth(420)
        lst = QListWidget()
        for it in items:
            lst.addItem(it.text)
        lst.setCurrentRow(0)
        bb = QDialogButtonBox()
        bb.addButton("Seçileni sil", QDialogButtonBox.AcceptRole)
        bb.addButton("Vazgeç", QDialogButtonBox.RejectRole)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay = QVBoxLayout(dlg)
        lay.addWidget(lst)
        lay.addWidget(bb)
        if dlg.exec() == QDialog.Accepted and lst.currentRow() >= 0:
            ok = editor.delete_item(path, items[lst.currentRow()])
            self.status.setText("Silindi." if ok else "Silinemedi (dosya değişmiş olabilir).")
            self.viewer.reload()


class DocsWindow(FramelessWindow):
    """Ortada, ~%55x%85, 16 pt, hep üstte; Esc kapatır. Kapanınca dosya izleyicileri bırakılır."""

    def __init__(self, parent=None):
        super().__init__(parent, on_top=True)
        self.tabs = QTabWidget()
        self.title_bar = TitleBar("Notlar")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        lay.addWidget(self.title_bar)
        body = QVBoxLayout()
        body.setContentsMargins(6, 6, 6, 6)
        body.addWidget(self.tabs)
        lay.addLayout(body, 1)
        self.attach_title_bar(self.title_bar)
        self.setWindowTitle("Notlar")
        QShortcut(QKeySequence("Esc"), self, activated=self.close)

    def set_sources(self, settings: Settings, projects: list[dict] | None = None) -> None:
        self.tabs.clear()
        today = dt.date.today
        for p in (settings.projects if projects is None else projects):
            v = MarkdownViewer(big=True)
            v.set_loader(lambda p=p: load_project(p["name"], p["path"], settings.default_filename))
            self.tabs.addTab(v, p["name"])
        self.tabs.addTab(PlanTab("daily", settings, lambda: load_daily(settings.daily_path, today())), DAILY)
        self.tabs.addTab(PlanTab("schedule", settings, lambda: load_schedule(settings.schedule_path, today())),
                         SCHEDULE)

    def focus_tab(self, title: str | None) -> None:
        for i in range(self.tabs.count()):
            if title and self.tabs.tabText(i).casefold() == title.casefold():
                self.tabs.setCurrentIndex(i)
                return

    def open_centered(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        w, h = int(geo.width() * 0.55), int(geo.height() * 0.85)
        self.resize(w, h)
        self.move(geo.center().x() - w // 2, geo.center().y() - h // 2)
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        self.tabs.clear()
        super().closeEvent(e)
