import sqlite3
from pathlib import Path

import pytest

from db.loader import load_csv
from db.validator import CatalogCourse, load_catalog, validate
from solver import solve

ROOT = Path(__file__).resolve().parents[1]


def course(course_id, name, credits, raw_time, slots, category="기전", group="전공", semester=1):
    return CatalogCourse(
        id=course_id,
        semester=semester,
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
NET = course(3, "네트워크", 3, "목567", [("THU", 5), ("THU", 6), ("THU", 7)])
WRITE = course(4, "글쓰기", 2, "금34", [("FRI", 3), ("FRI", 4)], category="교필", group="교양")
THESIS = course(5, "졸업논문", 0, "-", [])
CATALOG = [ALGO, DB, NET, WRITE, THESIS]


def test_empty_constraints_prefer_empty_schedule():
    result = solve(CATALOG, [], n=1)
    assert result["status"] == "ok"
    assert result["candidates"][0]["courses"] == []
    assert result["candidates"][0]["score"] == 0


def test_unique_feasible_schedule():
    result = solve(
        CATALOG,
        [
            {"type": "free_slots", "params": {"days": ["WED"]}},
            {"type": "credits", "params": {"min": 5, "max": 5}},
            {"type": "course", "params": {"course_id": 1, "mode": "require"}},
            {"type": "course", "params": {"course_id": 2, "mode": "exclude"}},
        ],
        n=3,
    )
    assert result["status"] == "ok"
    assert [item["id"] for item in result["constraints"]] == ["c1", "c2", "c3", "c4"]
    assert len(result["candidates"]) == 1
    chosen = result["candidates"][0]
    assert {course["id"] for course in chosen["courses"]} == {1, 4}
    assert chosen["total_credits"] == 5
    assert chosen["days_used"] == ["MON", "FRI"]
    assert chosen["metrics"]["early_class_count"] == 1
    assert chosen["metrics"]["late_class_count"] == 0
    assert chosen["metrics"]["max_consecutive"] == 2
    validation = validate(
        CATALOG,
        [{"id": course["id"]} for course in chosen["courses"]],
        result["constraints"],
        total_credits=chosen["total_credits"],
        semester=1,
    )
    assert validation.ok


def test_infeasible_returns_single_drop_relaxations():
    result = solve(
        CATALOG,
        [
            {"id": "keep_algo", "type": "course", "params": {"course_id": 1, "mode": "require"}},
            {"id": "keep_db", "type": "course", "params": {"course_id": 2, "mode": "require"}},
        ],
        n=3,
    )
    assert result["status"] == "infeasible"
    assert result["candidates"] == []
    assert result["relaxations"] == [
        {"drop": ["keep_algo"]},
        {"drop": ["keep_db"]},
    ]


def test_consecutive_limit_excludes_network():
    result = solve(
        CATALOG,
        [
            {"type": "course", "params": {"course_id": 3, "mode": "require"}},
            {"type": "limit", "params": {"kind": "consecutive", "count": 2}},
        ],
        n=1,
    )
    assert result["status"] == "infeasible"
    assert {"drop": ["c1"]} in result["relaxations"]
    assert {"drop": ["c2"]} in result["relaxations"]

    allowed = solve(
        CATALOG,
        [
            {"type": "course", "params": {"course_id": 3, "mode": "require"}},
            {"type": "limit", "params": {"kind": "consecutive", "count": 3}},
        ],
        n=1,
    )
    assert allowed["status"] == "ok"
    assert [course["id"] for course in allowed["candidates"][0]["courses"]] == [3]


def test_invalid_constraints_raise():
    with pytest.raises(ValueError, match="알 수 없는 조건 타입"):
        solve(CATALOG, [{"type": "soft", "params": {}}])
    with pytest.raises(ValueError, match="course_id"):
        solve(CATALOG, [{"type": "course", "params": {"course_id": 5, "mode": "require"}}])
    with pytest.raises(ValueError, match="알 수 없는 요일"):
        solve(CATALOG, [{"type": "free_slots", "params": {"days": ["ABC"]}}])


def test_real_catalog_credits_and_wednesday_free(tmp_path):
    db_path = tmp_path / "courses.db"
    summary = load_csv(ROOT / "data" / "courses.csv", db_path)
    assert not summary["parse_failures"]
    connection = sqlite3.connect(db_path)
    try:
        catalog = load_catalog(connection)
    finally:
        connection.close()

    result = solve(
        catalog,
        [
            {"id": "credits", "type": "credits", "params": {"min": 18, "max": 18}},
            {"id": "wed", "type": "free_slots", "params": {"days": ["WED"]}},
        ],
        n=3,
        semester=1,
    )
    assert result["status"] == "ok"
    assert 1 <= len(result["candidates"]) <= 3

    seen = []
    scores = []
    for candidate in result["candidates"]:
        selected_ids = {course["id"] for course in candidate["courses"]}
        assert selected_ids not in seen
        seen.append(selected_ids)
        scores.append(candidate["score"])
        assert candidate["total_credits"] == 18
        assert "WED" not in candidate["days_used"]
        validation = validate(
            catalog,
            [{"id": course["id"]} for course in candidate["courses"]],
            result["constraints"],
            total_credits=candidate["total_credits"],
            semester=1,
        )
        assert validation.ok, validation.findings
    assert scores == sorted(scores)
