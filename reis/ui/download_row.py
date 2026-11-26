"""İndir butonu + ilerleme çubuğu; ayarlar ve ilk kurulum sihirbazı ortak kullanır."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QWidget

from ..core.downloads import DownloadTask


class DownloadRow(QWidget):
    """`make_fn()` -> fn(progress, cancelled) döndürür (indirilecek şeyi çağrı anında seçer).
    `is_ready()` -> bool: dosyalar zaten var mı."""
    state_changed = Signal()

    def __init__(self, make_fn: Callable, is_ready: Callable[[], bool], parent=None, *,
                 install_label: str = "İndir", confirm: Callable[[], bool] | None = None):
        super().__init__(parent)
        self._install_label = install_label
        self._confirm = confirm  # büyük indirmeler için onay (False dönerse başlamaz)
        self._make_fn = make_fn
        self._is_ready = is_ready
        self._task: DownloadTask | None = None
        self.button = QPushButton("İndir")
        self.button.clicked.connect(self.start)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setVisible(False)
        self.bar.setTextVisible(True)
        self.status = QLabel("")
        self.status.setObjectName("PathLabel")
        self.status.setWordWrap(True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.button)
        lay.addWidget(self.bar, 1)
        lay.addWidget(self.status)
        self.refresh()

    def busy(self) -> bool:
        return self._task is not None

    def refresh(self) -> None:
        if self._task is not None:
            return
        ready = self._is_ready()
        self.status.setText("kurulu ✓" if ready else "kurulu değil")
        self.button.setText("Yeniden kontrol et" if ready else self._install_label)
        self.button.setEnabled(True)

    def start(self) -> None:
        if self._task is not None:
            return
        if self._confirm is not None and not self._confirm():
            return
        try:
            fn = self._make_fn()
        except Exception as e:
            self.status.setText(str(e))
            return
        self._task = DownloadTask(fn, self)
        self._task.progress.connect(self._on_progress)
        self._task.succeeded.connect(self._on_ok)
        self._task.failed.connect(self._finish)
        self.button.setEnabled(False)
        self.bar.setValue(0)
        self.bar.setVisible(True)
        self.status.setText("indiriliyor…")
        self.state_changed.emit()
        self._task.start()

    def _on_ok(self) -> None:
        self._finish("tamam")

    def _on_progress(self, pct: int, label: str) -> None:
        self.bar.setValue(pct)
        self.status.setText(label)

    def _finish(self, msg: str) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.wait(2000)
            task.deleteLater()
        self.bar.setVisible(False)
        self.refresh()
        if msg != "tamam":
            self.status.setText(msg)
        self.state_changed.emit()

    def cancel_and_wait(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task.wait(5000)
