from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QObject, QTimer, Signal


class AssistantState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


STATE_LABELS = {
    AssistantState.IDLE: "HAZIR",
    AssistantState.LISTENING: "DİNLİYORUM",
    AssistantState.THINKING: "DÜŞÜNÜYORUM",
    AssistantState.SPEAKING: "KONUŞUYORUM",
    AssistantState.ERROR: "HATA",
}

ERROR_RESET_MS = 4000


class StateBus(QObject):
    state_changed = Signal(object)
    subtitle_changed = Signal(str)
    error_raised = Signal(str)  # sohbet geçmişi için
    online_changed = Signal(bool)  # son LLM/ağ çağrısı başarılı mı

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = AssistantState.IDLE
        self._token = 0

    def set_state(self, state: AssistantState) -> None:
        self._token += 1
        self.state = state
        self.state_changed.emit(state)
        if state is AssistantState.ERROR:
            tok = self._token
            QTimer.singleShot(ERROR_RESET_MS, lambda: self._reset_error(tok))

    def _reset_error(self, tok: int) -> None:
        if tok == self._token and self.state is AssistantState.ERROR:
            self.set_state(AssistantState.IDLE)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_changed.emit(text)

    def error(self, message: str) -> None:
        self.error_raised.emit(message)
        self.set_subtitle(message)
        self.set_state(AssistantState.ERROR)
