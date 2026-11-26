from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    output_tokens: int = 0


class LLMProvider(Protocol):
    def complete_json(self, system: str, user: str, schema: dict) -> LLMResponse:
        """JSON şemasına uygun metin döndürür."""
        ...
