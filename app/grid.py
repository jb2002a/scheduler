"""솔버 후보와 카탈로그로 주간 시간표 칸·블록을 만든다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from db.time_parser import DAY_CODES
from db.validator import CatalogCourse

WEEKDAY_CODES = DAY_CODES[:5]
WEEKEND_CODES = DAY_CODES[5:]
ALL_PERIODS = tuple(range(11))
DAY_LABELS = {
    "MON": "월",
    "TUE": "화",
    "WED": "수",
    "THU": "목",
    "FRI": "금",
    "SAT": "토",
    "SUN": "일",
}

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PERIODS_PATH = ROOT / "config" / "periods.json"


@dataclass(frozen=True)
class GridBlock:
    day: str
    start_period: int
    end_period: int
    course_id: int
    name: str
    credits: int
    raw_time: str

    @property
    def row_span(self) -> int:
        return self.end_period - self.start_period + 1


@dataclass(frozen=True)
class GridSummary:
    total_credits: int
    days_used: tuple[str, ...]
    early_class_count: int
    late_class_count: int
    free_periods_by_day: Mapping[str, int]
    max_consecutive: int


@dataclass(frozen=True)
class GridView:
    days: tuple[str, ...]
    periods: tuple[int, ...]
    period_labels: Mapping[int, str]
    blocks: tuple[GridBlock, ...]
    cells: Mapping[tuple[str, int], GridBlock]
    summary: GridSummary | None
    missing_ids: tuple[int, ...]


def load_period_labels(path: Path | None = None) -> dict[int, str]:
    target = path or DEFAULT_PERIODS_PATH
    payload = json.loads(target.read_text(encoding="utf-8"))
    raw = payload.get("periods") or {}
    return {int(key): str(value) for key, value in raw.items()}


def build_grid(
    candidate: Mapping[str, Any] | None,
    catalog: Sequence[CatalogCourse],
    *,
    period_labels: Mapping[int, str] | None = None,
) -> GridView:
    labels = dict(period_labels) if period_labels is not None else load_period_labels()
    by_id = {course.id: course for course in catalog}

    if not candidate:
        return GridView(
            days=WEEKDAY_CODES,
            periods=ALL_PERIODS,
            period_labels=labels,
            blocks=(),
            cells={},
            summary=None,
            missing_ids=(),
        )

    courses_raw = candidate.get("courses") or []
    blocks: list[GridBlock] = []
    cells: dict[tuple[str, int], GridBlock] = {}
    missing: list[int] = []
    used_days: set[str] = set()

    for item in courses_raw:
        if not isinstance(item, Mapping):
            continue
        try:
            course_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        course = by_id.get(course_id)
        if course is None:
            missing.append(course_id)
            continue
        name = str(item.get("name") or course.name)
        credits = int(item.get("credits") if item.get("credits") is not None else course.credits)
        raw_time = str(item.get("raw_time") or course.raw_time)
        for day, period_group in _group_contiguous(course.slots):
            used_days.add(day)
            start = period_group[0]
            end = period_group[-1]
            block = GridBlock(
                day=day,
                start_period=start,
                end_period=end,
                course_id=course_id,
                name=name,
                credits=credits,
                raw_time=raw_time,
            )
            blocks.append(block)
            for period in period_group:
                cells[(day, period)] = block

    days = _visible_days(used_days)
    metrics = candidate.get("metrics") or {}
    days_used_raw = candidate.get("days_used") or []
    summary = GridSummary(
        total_credits=int(candidate.get("total_credits") or 0),
        days_used=tuple(str(day) for day in days_used_raw),
        early_class_count=int(metrics.get("early_class_count") or 0),
        late_class_count=int(metrics.get("late_class_count") or 0),
        free_periods_by_day={
            str(day): int(count)
            for day, count in (metrics.get("free_periods_by_day") or {}).items()
        },
        max_consecutive=int(metrics.get("max_consecutive") or 0),
    )
    return GridView(
        days=days,
        periods=ALL_PERIODS,
        period_labels=labels,
        blocks=tuple(blocks),
        cells=cells,
        summary=summary,
        missing_ids=tuple(missing),
    )


def day_label(day: str) -> str:
    return DAY_LABELS.get(day, day)


def _visible_days(used_days: set[str]) -> tuple[str, ...]:
    days = list(WEEKDAY_CODES)
    for day in WEEKEND_CODES:
        if day in used_days:
            days.append(day)
    return tuple(days)


def _group_contiguous(slots: Sequence[tuple[str, int]]) -> list[tuple[str, list[int]]]:
    by_day: dict[str, list[int]] = {}
    for day, period in slots:
        by_day.setdefault(day, []).append(period)

    groups: list[tuple[str, list[int]]] = []
    for day in DAY_CODES:
        periods = sorted(set(by_day.get(day, [])))
        if not periods:
            continue
        current = [periods[0]]
        for period in periods[1:]:
            if period == current[-1] + 1:
                current.append(period)
            else:
                groups.append((day, current))
                current = [period]
        groups.append((day, current))
    return groups
