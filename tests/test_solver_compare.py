import json

from db.validator import CatalogCourse
from experiments.solver_compare.run import (
    ResolveError,
    apply_schedule,
    build_messages,
    candidate_to_score_payload,
    e8_result,
    find_unique_named_course,
    resolve_named_constraints,
    score_run,
    solver_result_to_response_text,
)
from experiments.baseline.score import score_response
from solver.solve import solve


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
DS_SPACE = course(6, "데이터 구조", 3, "수12", [("WED", 1), ("WED", 2)])
CATALOG = [ALGO, NET, WRITE, DS, ENG]
CATALOG_DUP = [ALGO, NET, WRITE, DS, ENG, DS_SPACE]


def test_find_unique_named_course_compact_match():
    found = find_unique_named_course(CATALOG, "데이터 구조")
    assert found.id == DS.id


def test_find_unique_named_course_missing_and_ambiguous():
    try:
        find_unique_named_course(CATALOG, "없는과목")
        assert False, "expected ResolveError"
    except ResolveError as error:
        assert "없는 과목명" in str(error)

    try:
        find_unique_named_course(CATALOG_DUP, "데이터구조")
        assert False, "expected ResolveError"
    except ResolveError as error:
        assert "둘 이상" in str(error)


def test_resolve_named_constraints_course_to_id():
    resolved = resolve_named_constraints(
        CATALOG,
        [
            {"id": "c1", "type": "credits", "params": {"min": 14, "max": 14}},
            {
                "id": "c2",
                "type": "course",
                "params": {"name": "데이터 구조", "mode": "require"},
            },
            {
                "id": "c3",
                "type": "course",
                "params": {"name": "영어회화", "mode": "exclude"},
            },
        ],
    )
    assert resolved[1]["params"] == {"course_id": DS.id, "mode": "require"}
    assert resolved[2]["params"] == {"course_id": ENG.id, "mode": "exclude"}


def test_candidate_to_score_payload_uses_time():
    candidate = {
        "courses": [
            {"id": 1, "name": "알고리즘", "credits": 3, "raw_time": "월12"},
        ],
        "total_credits": 3,
    }
    payload = candidate_to_score_payload(candidate)
    assert payload["feasible"] is True
    assert payload["courses"][0]["time"] == "월12"
    assert "raw_time" not in payload["courses"][0]


def test_solver_candidate_scores_perfect_with_score_response():
    constraints = [
        {"id": "c1", "type": "credits", "params": {"min": 11, "max": 11}},
        {"id": "c2", "type": "course", "params": {"name": "데이터구조", "mode": "require"}},
        {"id": "c3", "type": "course", "params": {"name": "영어회화", "mode": "exclude"}},
    ]
    response_text, resolved, result = apply_schedule(CATALOG, constraints, n=1)
    assert result["status"] == "ok"
    assert resolved[1]["params"]["course_id"] == DS.id

    gold = [
        {"id": "c1", "type": "credits", "params": {"min": 11, "max": 11}},
        {"id": "c2", "type": "course", "params": {"name": "데이터구조", "mode": "require"}},
        {"id": "c3", "type": "course", "params": {"name": "영어회화", "mode": "exclude"}},
    ]
    score = score_response(CATALOG, response_text, gold, expected_feasible=True)
    assert score.ok, score.findings


def test_solver_infeasible_becomes_feasible_false():
    constraints = [
        {"id": "c1", "type": "credits", "params": {"min": 18, "max": 18}},
        {
            "id": "c2",
            "type": "free_slots",
            "params": {
                "days": ["MON", "TUE", "WED", "THU", "FRI"],
                "periods": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            },
        },
    ]
    response_text, _resolved, result = apply_schedule(CATALOG, constraints, n=1)
    assert result["status"] == "infeasible"
    payload = json.loads(response_text)
    assert payload["feasible"] is False

    score = score_response(
        CATALOG,
        response_text,
        constraints,
        expected_feasible=False,
    )
    assert score.ok


def test_build_messages_keeps_only_prior_constraints():
    prior = [
        [{"id": "c1", "type": "credits", "params": {"min": 18, "max": 18}}],
    ]
    messages = build_messages(
        [
            "18학점으로 짜줘. 수요일은 공강이었으면 좋겠어.",
            "아침도 비워줘.",
        ],
        prior,
    )
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"
    assert json.loads(messages[2]["content"])["constraints"][0]["id"] == "c1"
    assert messages[3]["role"] == "user"
    assert messages[3]["content"] == "아침도 비워줘."
    assert len(messages) == 4
    joined = json.dumps(messages, ensure_ascii=False)
    assert "알고리즘" not in joined
    assert "courses" not in joined


def test_score_run_last_turn_only_and_e8_on_error():
    scenario = {
        "id": "S9",
        "expected_feasible": True,
        "constraints": [
            {"id": "c1", "type": "credits", "params": {"min": 8, "max": 8}},
            {"id": "c2", "type": "free_slots", "params": {"days": ["WED"]}},
            {"id": "c3", "type": "free_slots", "params": {"periods": [0, 1]}},
        ],
    }
    solver = solve(
        CATALOG,
        [
            {"id": "c1", "type": "credits", "params": {"min": 8, "max": 8}},
            {"id": "c2", "type": "free_slots", "params": {"days": ["WED"]}},
            {"id": "c3", "type": "free_slots", "params": {"periods": [0, 1]}},
        ],
        n=1,
    )
    response_text = solver_result_to_response_text(solver)
    run_result = {
        "response_text": response_text,
        "error": None,
        "scored_turn": 1,
        "turn_trace": [
            {"turn": 1, "model_constraints": [{"id": "c1"}]},
            {"turn": 2, "model_constraints": scenario["constraints"]},
        ],
    }
    score = score_run(CATALOG, scenario, run_result)
    assert score.ok, score.findings

    failed = score_run(
        CATALOG,
        scenario,
        {
            "response_text": None,
            "error": "과목명이 둘 이상입니다: 데이터구조",
            "scored_turn": 0,
        },
    )
    assert failed.codes == ["E8"]
    assert "둘 이상" in failed.findings[0].message


def test_e8_result_shape():
    result = e8_result("schedule 도구를 호출하지 않았습니다.")
    assert result.codes == ["E8"]
    assert result.parsed is None
