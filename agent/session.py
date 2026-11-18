"""대화 중 조건과 직전 솔버 결과를 보관한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Session:
    constraints: list[dict[str, Any]] = field(default_factory=list)
    last_result: dict[str, Any] | None = None
    consecutive_asks: int = 0

    def snapshot_constraints(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.constraints]
