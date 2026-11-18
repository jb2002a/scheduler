"""주간 그리드 칸·블록 변환 테스트."""

from __future__ import annotations

from app.grid import WEEKDAY_CODES, build_grid, day_label
from db.validator import CatalogCourse


def course(course_id, name, credits, raw_time, slots, category="기전", group="전공"):
    return CatalogCourse(
        id=course_id,
        semester=1,
        course_code=f"C{course_id}",
        category=category,
        category_group=group,
        name=name,
        name_en=None,
        credits=credits,
        raw_time=raw_time,
        schedulable=bool(slots),
        slots=tuple(slots),
    )


ALGO = course(1, "알고리즘", 3, "월12", [("MON", 1), ("MON", 2)])
DB = course(2, "데이터베이스", 3, "월2수2", [("MON", 2), ("WED", 2)])
SAT = course(3, "토요특강", 2, "토12", [("SAT", 1), ("SAT", 2)])


def test_empty_candidate_shows_weekdays_only():
    view = build_grid(None, [ALGO], period_labels={0: "08:50", 1: "09:00"})
    assert view.days == WEEKDAY_CODES
    assert view.blocks == ()
    assert view.cells == {}
    assert view.summary is None


def test_slots_fill_cells_from_catalog_by_id():
    candidate = {
        "courses": [{"id": 1, "name": "알고리즘", "credits": 3, "raw_time": "월12"}],
        "total_credits": 3,
        "days_used": ["MON"],
        "metrics": {
            "free_periods_by_day": {},
            "max_consecutive": 2,
            "early_class_count": 0,
            "late_class_count": 0,
        },
    }
    view = build_grid(candidate, [ALGO, DB], period_labels={1: "09:00", 2: "10:00"})
    assert view.days == WEEKDAY_CODES
    assert ("MON", 1) in view.cells
    assert ("MON", 2) in view.cells
    assert view.cells[("MON", 1)].name == "알고리즘"
    assert view.cells[("MON", 1)].course_id == 1
    assert view.cells[("MON", 1)] is view.cells[("MON", 2)]
    assert len(view.blocks) == 1
    assert view.blocks[0].row_span == 2
    assert view.summary is not None
    assert view.summary.total_credits == 3
    assert view.summary.days_used == ("MON",)


def test_missing_catalog_id_is_skipped():
    candidate = {
        "courses": [{"id": 99, "name": "없는과목", "credits": 3, "raw_time": "월1"}],
        "total_credits": 3,
        "days_used": [],
        "metrics": {},
    }
    view = build_grid(candidate, [ALGO], period_labels={})
    assert view.blocks == ()
    assert view.cells == {}
    assert view.missing_ids == (99,)


def test_saturday_column_only_when_used():
    candidate = {
        "courses": [
            {"id": 1, "name": "알고리즘", "credits": 3, "raw_time": "월12"},
            {"id": 3, "name": "토요특강", "credits": 2, "raw_time": "토12"},
        ],
        "total_credits": 5,
        "days_used": ["MON", "SAT"],
        "metrics": {},
    }
    view = build_grid(candidate, [ALGO, SAT], period_labels={})
    assert view.days == WEEKDAY_CODES + ("SAT",)
    assert ("SAT", 1) in view.cells
    assert day_label("SAT") == "토"


def test_noncontiguous_same_day_makes_separate_blocks():
    split = course(4, "분할", 3, "월13", [("MON", 1), ("MON", 3)])
    candidate = {
        "courses": [{"id": 4, "name": "분할", "credits": 3, "raw_time": "월13"}],
        "total_credits": 3,
        "days_used": ["MON"],
        "metrics": {},
    }
    view = build_grid(candidate, [split], period_labels={})
    assert len(view.blocks) == 2
    assert view.blocks[0].row_span == 1
    assert view.blocks[1].start_period == 3
