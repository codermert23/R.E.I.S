"""Ağ işleri için küçük worker yardımcıları: arayüz thread'i asla bloklanmaz."""
from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal

log = logging.getLogger(__name__)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Job(QRunnable):
    def __init__(self, fn: Callable, signals: _Signals):
        super().__init__()
        self.fn = fn
        self.signals = signals
        self.setAutoDelete(True)

    def run(self):
        try:
            res = self.fn()
        except Exception as e:  # ağ/ayrıştırma: arayüze "çevrimdışı" olarak yansır
            log.info("Arka plan işi başarısız: %s: %s", type(e).__name__, e)
            self.signals.failed.emit(f"{type(e).__name__}: {e}")
            return
        self.signals.done.emit(res)


_alive: set = set()


def run_async(fn: Callable, on_done: Callable, on_fail: Callable) -> None:
    """fn worker thread'de çalışır; on_done/on_fail ana thread'de (kuyruklu) çağrılır."""
    sig = _Signals()  # ana thread'de yaratılır -> sinyaller ana thread'e kuyruklanır
    _alive.add(sig)

    def finish(cb):
        def inner(arg):
            _alive.discard(sig)
            cb(arg)
        return inner

    sig.done.connect(finish(on_done), Qt.QueuedConnection)
    sig.failed.connect(finish(on_fail), Qt.QueuedConnection)
    QThreadPool.globalInstance().start(_Job(fn, sig))
