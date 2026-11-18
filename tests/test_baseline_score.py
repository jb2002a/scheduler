import json
from pathlib import Path

from db.validator import CatalogCourse
from experiments.baseline.generate_data import generate_all, load_catalog_csv
from experiments.baseline.score import parse_model_response, score_response, summarize_scores


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
NET = course(2, "네트워크", 3, "목567", [("THU", 5), ("THU", 6), ("THU", 7)])
WRITE = course(3, "글쓰기", 2, "금34", [("FRI", 3), ("FRI", 4)], category="교필", group="교양")
DS = course(4, "데이터구조", 3, "화567", [("TUE", 5), ("TUE", 6), ("TUE", 7)])
ENG = course(5, "영어회화", 3, "월56수6", [("MON", 5), ("MON", 6), ("WED", 6)], category="교기", group="교양")
CATALOG = [ALGO, NET, WRITE, DS, ENG]


def test_parse_strips_markdown_fence():
    payload, error = parse_model_response(
        '```json\n{"feasible": false, "courses": [], "total_credits": 0, "explanation": "x"}\n```'
    )
    assert error is None
    assert payload["feasible"] is False


def test_score_perfect_feasible_response():
    selected = [
        {"name": "알고리즘", "credits": 3, "time": "월12"},
        {"name": "네트워크", "credits": 3, "time": "목567"},
        {"name": "글쓰기", "credits": 2, "time": "금34"},
        {"name": "데이터구조", "credits": 3, "time": "화567"},
        {"name": "영어회화", "credits": 3, "time": "월56수6"},
    ]
    response = {
        "feasible": True,
        "courses": selected,
        "total_credits": 14,
        "explanation": "ok",
    }
    result = score_response(
        CATALOG,
        json.dumps(response, ensure_ascii=False),
        [{"id": "c1", "type": "credits", "params": {"min": 14, "max": 14}}],
        expected_feasible=True,
    )
    assert result.ok


def test_score_e8_e7_and_constraint_errors():
    bad_json = score_response(CATALOG, "not-json", [], expected_feasible=True)
    assert bad_json.codes == ["E8"]

    wrong_feasible = score_response(
        CATALOG,
        '{"feasible": true, "courses": [], "total_credits": 0, "explanation": "x"}',
        [{"id": "c1", "type": "credits", "params": {"min": 14, "max": 14}}],
        expected_feasible=False,
    )
    assert "E7" in wrong_feasible.codes

    hallucinated = score_response(
        CATALOG,
        '{"feasible": true, "courses": [{"name": "없는과목", "credits": 3, "time": "월1"}], "total_credits": 3, "explanation": "x"}',
        [{"id": "c1", "type": "credits", "params": {"min": 3, "max": 3}}],
        expected_feasible=True,
    )
    assert "E1" in hallucinated.codes


def test_whitespace_only_name_difference_is_not_an_error():
    response = {
        "feasible": True,
        "courses": [
            {"name": "알고 리즘", "credits": 3, "time": "월12"},
            {"name": "네트워크", "credits": 3, "time": "목567"},
            {"name": "글쓰기", "credits": 2, "time": "금34"},
            {"name": "데이터 구조", "credits": 3, "time": "화567"},
            {"name": "영어 회화", "credits": 3, "time": "월56수6"},
        ],
        "total_credits": 14,
        "explanation": "ok",
    }
    result = score_response(
        CATALOG,
        json.dumps(response, ensure_ascii=False),
        [{"id": "c1", "type": "credits", "params": {"min": 14, "max": 14}}],
        expected_feasible=True,
    )
    assert result.ok


def test_score_infeasible_true_negative():
    result = score_response(
        CATALOG,
        '{"feasible": false, "courses": [], "total_credits": 0, "explanation": "불가능"}',
        [
            {"id": "c1", "type": "credits", "params": {"min": 18, "max": 18}},
            {
                "id": "c2",
                "type": "free_slots",
                "params": {
                    "days": ["MON", "TUE", "WED", "THU", "FRI"],
                    "periods": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                },
            },
        ],
        expected_feasible=False,
    )
    assert result.ok


def test_summarize_scores():
    ok = score_response(
        CATALOG,
        '{"feasible": false, "courses": [], "total_credits": 0, "explanation": "x"}',
        [],
        expected_feasible=False,
    )
    bad = score_response(CATALOG, "nope", [], expected_feasible=True)
    summary = summarize_scores([ok, bad])
    assert summary["total"] == 2
    assert summary["perfect"] == 1
    assert summary["error_counts"]["E8"] == 1


def test_generate_data_sizes_and_required_names(tmp_path):
    out = tmp_path / "data"
    catalogs = generate_all(Path("data/courses.csv"), out, seed=42, sizes=(100, 300, 500))
    assert set(catalogs) == {100, 300, 500}
    for size, courses in catalogs.items():
        assert len(courses) == size
        names = {course.name for course in courses}
        assert "데이터구조" in names
        assert "영어회화" in names
        loaded = load_catalog_csv(out / f"courses_{size}.csv")
        assert len(loaded) == size
