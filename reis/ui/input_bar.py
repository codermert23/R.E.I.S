from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget


class InputBar(QWidget):
    text_submitted = Signal(str)
    mic_clicked = Signal()
    settings_clicked = Signal()
    briefing_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mic = QPushButton("🎤")
        self.mic.setToolTip("Dinle / durdur (F1)")
        self.mic.clicked.connect(self.mic_clicked)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Reis'e yaz…")
        self.edit.returnPressed.connect(self._send)
        send = QPushButton("➤")
        send.setToolTip("Gönder")
        send.clicked.connect(self._send)
        gear = QPushButton("⚙")
        gear.setToolTip("Ayarlar")
        gear.clicked.connect(self.settings_clicked)
        brief = QPushButton("☀")
        brief.setToolTip("Sabah brifingi (F2)")
        brief.clicked.connect(self.briefing_clicked)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        for w in (self.mic, self.edit, send, brief, gear):
            lay.addWidget(w)
        lay.setStretch(1, 1)

    def _send(self):
        t = self.edit.text().strip()
        if t:
            self.edit.clear()
            self.text_submitted.emit(t)
