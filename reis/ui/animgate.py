"""Animasyon zamanlayıcılarını pencere görünür değilken durdurur (tepsideyken CPU/uyanma israfı olmasın).

`AnimationGate(widget, callback, interval_ms)`: widget gösterilince zamanlayıcı başlar; gizlenince, küçültülünce
(üst pencere minimize) durur. Qt Windows'ta başka pencerenin ÖRTTÜĞÜ (occluded) pencere için sinyal vermez; o durum
yalnızca gizli/küçültülmüş olarak ele alınır.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QWidget


class AnimationGate(QObject):
    def __init__(self, widget: QWidget, callback, interval_ms: int, on_resume=None):
        super().__init__(widget)
        self._w = widget
        self._interval = int(interval_ms)
        self._on_resume = on_resume
        self.timer = QTimer(self)
        self.timer.timeout.connect(callback)
        self._win: QWidget | None = None
        self.resumes = 0  # test/teşhis
        widget.installEventFilter(self)
        if widget.isVisible():
            self._set(True)

    @property
    def active(self) -> bool:
        return self.timer.isActive()

    def set_interval(self, ms: int) -> None:
        self._interval = int(ms)
        if self.timer.isActive():
            self.timer.setInterval(self._interval)

    def _watch_window(self) -> None:
        win = self._w.window()
        if win is not self._win:
            if self._win is not None:
                self._win.removeEventFilter(self)
            self._win = win
            if win is not self._w:
                win.installEventFilter(self)

    def _set(self, on: bool) -> None:
        if on == self.timer.isActive():
            return
        if on:
            self.resumes += 1
            if self._on_resume:
                self._on_resume()
            self.timer.start(self._interval)
        else:
            self.timer.stop()

    def eventFilter(self, obj, ev):
        t = ev.type()
        if obj is self._w:
            if t == QEvent.Show:
                self._watch_window()
                self._set(not self._w.window().isMinimized())
            elif t == QEvent.Hide:
                self._set(False)
        if t == QEvent.WindowStateChange and obj is self._w.window():
            self._set(self._w.isVisible() and not obj.isMinimized())
        return False
