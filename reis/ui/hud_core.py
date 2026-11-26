from __future__ import annotations

import math

from ..core.state import AssistantState

# Duruma göre küre renkleri (açık mavi tema; düşünme sarı, konuşma yeşil, hata kırmızı).
STATE_COLORS = {
    AssistantState.IDLE: ("#5BC0FF", "#2E9BEA"),
    AssistantState.LISTENING: ("#5BC0FF", "#2E9BEA"),
    AssistantState.THINKING: ("#FFD400", "#FFD400"),
    AssistantState.SPEAKING: ("#00FF88", "#00FF88"),
    AssistantState.ERROR: ("#FF1744", "#FF1744"),
}


def blink_factor(state: AssistantState, phase: float) -> float:
    """HATA durumunda 0.25-1.0 arasında yanıp söner; diğerlerinde 1."""
    if state is AssistantState.ERROR:
        return 0.25 + 0.75 * (1.0 if math.sin(phase * 10) > 0 else 0.0)
    return 1.0
