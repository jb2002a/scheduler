from db.validator import CatalogCourse, validate


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


def codes(result):
    return [finding.code for finding in result.findings]


def test_valid_schedule_passes():
    result = validate(
        CATALOG,
        [{"name": "알고리즘", "credits": 3, "time": "월12"}, {"id": 4}],
        [
            {"id": "c1", "type": "free_slots", "params": {"days": ["WED"]}},
            {"id": "c2", "type": "credits", "params": {"min": 5, "max": 5}},
            {"id": "c3", "type": "credits", "params": {"category": "전공", "min": 3}},
            {"id": "c4", "type": "limit", "params": {"kind": "days", "count": 2}},
            {"id": "c5", "type": "limit", "params": {"kind": "per_day", "count": 1}},
            {"id": "c6", "type": "limit", "params": {"kind": "consecutive", "count": 2}},
            {"id": "c7", "type": "course", "params": {"course_id": 1, "mode": "require"}},
            {"id": "c8", "type": "course", "params": {"course_id": 2, "mode": "exclude"}},
        ],
        total_credits=5,
        semester=1,
    )
    assert result.ok


def test_unknown_course_wrong_attribute_duplicate_and_conflict():
    missing = validate(CATALOG, [{"name": "없는과목", "credits": 3, "time": "월1"}], semester=1)
    assert codes(missing) == ["E1"]

    wrong = validate(
        CATALOG,
        [{"name": "알고리즘", "credits": 2, "time": "화34"}],
        semester=1,
    )
    assert codes(wrong) == ["E2", "E2"]

    duplicate = validate(CATALOG, [{"id": 1}, {"id": 1}], semester=1)
    assert codes(duplicate) == ["E4"]

    conflict = validate(
        CATALOG,
        [{"name": "알고리즘", "credits": 3, "time": "월12"}, {"name": "데이터베이스", "credits": 3, "time": "월2수2"}],
        semester=1,
    )
    assert codes(conflict) == ["E3"]
    assert "MON 2교시" in conflict.findings[0].message


def test_credit_sum_and_constraint_violations():
    selected = [
        {"name": "알고리즘", "credits": 3, "time": "월12"},
        {"name": "네트워크", "credits": 3, "time": "목567"},
        {"name": "글쓰기", "credits": 2, "time": "금34"},
    ]
    result = validate(
        CATALOG,
        selected,
        [
            {"id": "free", "type": "free_slots", "params": {"periods": [1]}},
            {"id": "major", "type": "credits", "params": {"category": "전공", "min": 12}},
            {"id": "days", "type": "limit", "params": {"kind": "days", "count": 2}},
            {"id": "per", "type": "limit", "params": {"kind": "per_day", "count": 0}},
            {"id": "run", "type": "limit", "params": {"kind": "consecutive", "count": 2}},
            {"id": "keep", "type": "course", "params": {"course_id": 5, "mode": "require"}},
            {"id": "drop", "type": "course", "params": {"course_id": 4, "mode": "exclude"}},
        ],
        total_credits=9,
        semester=1,
    )
    assert codes(result) == ["E5", "E5", "E5", "E5", "E5", "E5", "E5", "E6"]

    gap = validate(
        CATALOG,
        [{"id": 2}],
        [{"type": "limit", "params": {"kind": "consecutive", "count": 1}}],
        semester=1,
    )
    assert gap.ok

    same_day_two_periods = validate(
        CATALOG,
        [{"id": 1}],
        [{"type": "limit", "params": {"kind": "per_day", "count": 1}}],
        semester=1,
    )
    assert same_day_two_periods.ok


def test_free_whole_day_and_other_semester_is_missing():
    blocked = validate(
        CATALOG,
        [{"id": 2}],
        [{"type": "free_slots", "params": {"days": ["WED"]}}],
        semester=1,
    )
    assert codes(blocked) == ["E5"]

    other = validate(CATALOG, [{"id": 1}], semester=2)
    assert codes(other) == ["E1"]


def test_same_name_is_chosen_by_time():
    morning = course(10, "영어회화", 3, "월12", [("MON", 1), ("MON", 2)])
    afternoon = course(11, "영어회화", 3, "목56", [("THU", 5), ("THU", 6)])
    catalog = [morning, afternoon]
    chosen = validate(
        catalog,
        [{"name": "영어회화", "credits": 3, "time": "목56"}],
        [{"type": "course", "params": {"course_id": 11, "mode": "require"}}],
        semester=1,
    )
    assert chosen.ok

    ambiguous = validate(catalog, [{"name": "영어회화"}], semester=1)
    assert codes(ambiguous) == ["E2"]
