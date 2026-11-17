"""강의시간 문자열을 (요일 코드, 교시) 목록으로 변환한다."""

from __future__ import annotations

import json
from pathlib import Path

DAY_TO_CODE = {
    "월": "MON",
    "화": "TUE",
    "수": "WED",
    "목": "THU",
    "금": "FRI",
    "토": "SAT",
    "일": "SUN",
}

DAY_CODES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")

Slot = tuple[str, int]


def parse_time(raw: str) -> tuple[list[Slot], str | None]:
    """시간 문자열을 파싱한다.

    성공하면 (슬롯 목록, None)을 반환한다.
    `-`와 빈 문자열은 슬롯이 없는 성공이다.
    실패하면 ([], 이유)를 반환하고 예외를 던지지 않는다.
    """
    text = raw.strip()
    if text == "" or text == "-":
        return [], None

    slots: list[Slot] = []
    day: str | None = None
    periods: list[int] = []

    def close_day() -> str | None:
        nonlocal periods
        if day is None:
            return None
        if not periods:
            return f"요일에 교시가 없음: {text}"
        for prev, cur in zip(periods, periods[1:]):
            if cur <= prev:
                return f"요일 안 교시가 오름차순이 아님: {text}"
        slots.extend((day, period) for period in periods)
        periods = []
        return None

    index = 0
    while index < len(text):
        char = text[index]
        if char in DAY_TO_CODE:
            error = close_day()
            if error:
                return [], error
            day = DAY_TO_CODE[char]
            index += 1
            continue
        if char.isdigit():
            if day is None:
                return [], f"요일 없이 교시가 나옴: {text}"
            if char == "1" and index + 1 < len(text) and text[index + 1] == "0":
                period = 10
                index += 2
            else:
                period = int(char)
                index += 1
            periods.append(period)
            continue
        return [], f"허용되지 않는 문자: {text}"

    error = close_day()
    if error:
        return [], error
    return slots, None


def load_periods(path: Path | None = None) -> dict:
    """교시-시각 매핑을 읽는다."""
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config" / "periods.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    periods = data.get("periods")
    if not isinstance(periods, dict):
        raise ValueError("periods.json에 periods 객체가 필요합니다.")
    missing = [str(period) for period in range(11) if str(period) not in periods]
    if missing:
        raise ValueError(f"periods.json에 교시가 없습니다: {', '.join(missing)}")
    if data.get("period_minutes") != 50:
        raise ValueError("period_minutes는 50이어야 합니다.")
    return data
