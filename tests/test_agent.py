"""에이전트 세션·도구·루프 테스트. 실제 API는 호출하지 않는다."""

from __future__ import annotations

from typing import Any

import pytest

from agent.loop import TurnResult, run_turn
from agent.session import Session
from agent.tools import ask_user, execute_tool, schedule, search_courses
from db.validator import CatalogCourse


def course(
    course_id,
    name,
    credits,
    raw_time,
    slots,
    category="기전",
    group="전공",
    semester=1,
):
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
ENG = course(6, "영어회화", 3, "월56수6", [("MON", 5), ("MON", 6), ("WED", 6)], category="교기", group="교양")
POP = course(
    7,
    "팝송을통한영어회화",
    2,
    "수56",
    [("WED", 5), ("WED", 6)],
    category="교선",
    group="교양",
)
CATALOG = [ALGO, DB, NET, WRITE, ENG, POP]


class FakeResponse:
    _next_id = 1

    def __init__(self, output: list[Any]):
        self.output = output
        self.id = f"resp_fake_{FakeResponse._next_id}"
        FakeResponse._next_id += 1
        texts = []
        for item in output:
            if isinstance(item, dict) and item.get("type") == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("text"):
                        texts.append(part["text"])
        self.output_text = "\n".join(texts)


def function_call(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> dict:
    import json

    return {
        "type": "function_call",
        "name": name,
        "call_id": call_id,
        "arguments": json.dumps(arguments, ensure_ascii=False),
    }


def text_message(text: str) -> dict:
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def scripted_caller(scripts: list[list[Any]]):
    queue = list(scripts)

    def call_model(input_items, tools, *, previous_response_id=None):
        if not queue:
            raise AssertionError("예상보다 많은 모델 호출")
        return FakeResponse(queue.pop(0))

    return call_model


def test_invalid_schedule_keeps_constraints():
    session = Session()
    session.constraints = [
        {"id": "c1", "type": "credits", "params": {"min": 5, "max": 5}},
    ]
    before = session.snapshot_constraints()
    result = schedule(
        session,
        CATALOG,
        {"add": [{"type": "course", "params": {"course_id": 999, "mode": "require"}}]},
    )
    assert result["status"] == "error"
    assert session.constraints == before


def test_infeasible_keeps_added_constraints_and_relaxations():
    session = Session()
    result = schedule(
        session,
        CATALOG,
        {
            "add": [
                {
                    "id": "keep_algo",
                    "type": "course",
                    "params": {"course_id": 1, "mode": "require"},
                },
                {
                    "id": "keep_db",
                    "type": "course",
                    "params": {"course_id": 2, "mode": "require"},
                },
            ]
        },
    )
    assert result["status"] == "infeasible"
    assert {item["id"] for item in session.constraints} == {"keep_algo", "keep_db"}
    assert result["relaxations"]
    assert session.last_result["status"] == "infeasible"


def test_schedule_and_ask_user_same_response_rejected():
    session = Session()
    caller = scripted_caller(
        [
            [
                function_call(
                    "schedule",
                    {"add": [{"type": "credits", "params": {"min": 5, "max": 5}}]},
                    "c1",
                ),
                function_call(
                    "ask_user",
                    {"question": "어느 쪽?", "reason": "애매함"},
                    "c2",
                ),
            ],
            [
                function_call(
                    "schedule",
                    {"add": [{"type": "credits", "params": {"min": 5, "max": 5}}]},
                    "c3",
                ),
            ],
            [text_message("5학점으로 짰어요.")],
        ]
    )
    result = run_turn(session, CATALOG, "5학점", call_model=caller)
    assert result.ask is None
    assert any(item.get("rejected") for item in result.tool_trace)
    assert session.constraints
    assert session.constraints[0]["type"] == "credits"


def test_ask_user_third_consecutive_errors():
    session = Session()
    first = ask_user(session, {"question": "하나?", "reason": "r", "options": ["a", "b"]})
    second = ask_user(session, {"question": "둘?", "reason": "r"})
    third = ask_user(session, {"question": "셋?", "reason": "r"})
    assert first["status"] == "ask"
    assert second["status"] == "ask"
    assert third["status"] == "error"
    assert session.consecutive_asks == 2


def test_ask_user_options_over_limit():
    session = Session()
    result = ask_user(
        session,
        {
            "question": "고르세요",
            "reason": "많음",
            "options": ["1", "2", "3", "4", "5"],
        },
    )
    assert result["status"] == "error"
    assert session.consecutive_asks == 0


def test_search_english_conversation_partial_match():
    result = search_courses(CATALOG, {"query": "영어 회화"})
    assert result["status"] == "ok"
    names = {item["name"] for item in result["courses"]}
    assert names == {"영어회화", "팝송을통한영어회화"}


def test_search_exact_name_ignores_day_filter():
    intro_a = course(20, "컴퓨터개론", 3, "월12", [("MON", 1), ("MON", 2)])
    intro_b = course(21, "컴퓨터개론", 3, "화12", [("TUE", 1), ("TUE", 2)], category="교선", group="교양")
    catalog = [intro_a, intro_b, ENG]
    result = search_courses(catalog, {"query": "컴퓨터개론", "day": "MON"})
    assert result["status"] == "ok"
    assert {item["id"] for item in result["courses"]} == {20, 21}


def test_search_without_filters_errors():
    result = search_courses(CATALOG, {})
    assert result["status"] == "error"


def test_fake_model_accumulates_then_removes_constraint():
    session = Session()
    history: list[dict[str, Any]] = []

    turn1 = run_turn(
        session,
        CATALOG,
        "18학점, 수요일 공강",
        call_model=scripted_caller(
            [
                [
                    function_call(
                        "schedule",
                        {
                            "add": [
                                {
                                    "type": "credits",
                                    "params": {"min": 5, "max": 5},
                                    "source_text": "5학점",
                                },
                                {
                                    "type": "free_slots",
                                    "params": {"days": ["WED"]},
                                    "source_text": "수요일 공강",
                                },
                            ]
                        },
                    ),
                ],
                [text_message("수요일 공강으로 짰어요.")],
            ]
        ),
        history=history,
    )
    assert turn1.ask is None
    assert len(session.constraints) == 2
    wed_id = next(
        item["id"]
        for item in session.constraints
        if item["type"] == "free_slots" and item["params"].get("days") == ["WED"]
    )
    history.extend(
        [
            {"role": "user", "content": "18학점, 수요일 공강"},
            {"role": "assistant", "content": turn1.text},
        ]
    )

    turn2 = run_turn(
        session,
        CATALOG,
        "아침도 비워줘",
        call_model=scripted_caller(
            [
                [
                    function_call(
                        "schedule",
                        {
                            "add": [
                                {
                                    "type": "free_slots",
                                    "params": {"periods": [0, 1]},
                                    "source_text": "아침도 비워줘",
                                }
                            ]
                        },
                    ),
                ],
                [text_message("아침도 비웠어요.")],
            ]
        ),
        history=history,
    )
    assert len(session.constraints) == 3
    assert any(
        item["type"] == "free_slots" and item["params"].get("periods") == [0, 1]
        for item in session.constraints
    )
    history.extend(
        [
            {"role": "user", "content": "아침도 비워줘"},
            {"role": "assistant", "content": turn2.text},
        ]
    )

    turn3 = run_turn(
        session,
        CATALOG,
        "수요일 공강 취소",
        call_model=scripted_caller(
            [
                [function_call("schedule", {"remove": [wed_id]})],
                [text_message("수요일 공강을 취소했어요.")],
            ]
        ),
        history=history,
    )
    assert turn3.ask is None
    assert len(session.constraints) == 2
    assert all(
        not (
            item["type"] == "free_slots" and item["params"].get("days") == ["WED"]
        )
        for item in session.constraints
    )
    assert any(
        item["type"] == "free_slots" and item["params"].get("periods") == [0, 1]
        for item in session.constraints
    )


def test_ask_user_ends_turn_without_changing_constraints():
    session = Session()
    session.constraints = [
        {"id": "c1", "type": "credits", "params": {"min": 5, "max": 5}},
    ]
    before = session.snapshot_constraints()
    result = run_turn(
        session,
        CATALOG,
        "영어회화 빼줘",
        call_model=scripted_caller(
            [
                [
                    function_call("search_courses", {"query": "영어회화"}, "s1"),
                    function_call(
                        "ask_user",
                        {
                            "question": "영어회화가 두 개 있어요. 둘 다 뺄까요?",
                            "options": ["둘 다", "영어회화만", "팝송만"],
                            "reason": "과목명이 둘과 일치함",
                        },
                        "a1",
                    ),
                ]
            ]
        ),
    )
    assert result.ask is not None
    assert result.ask["status"] == "ask"
    assert session.constraints == before
    assert session.consecutive_asks == 1


def test_morning_default_without_ask():
    session = Session()
    result = run_turn(
        session,
        CATALOG,
        "아침 비워줘",
        call_model=scripted_caller(
            [
                [
                    function_call(
                        "schedule",
                        {
                            "add": [
                                {
                                    "type": "free_slots",
                                    "params": {"periods": [0, 1]},
                                    "source_text": "아침 비워줘",
                                }
                            ]
                        },
                    ),
                ],
                [text_message("1교시까지 비웠어요.")],
            ]
        ),
    )
    assert result.ask is None
    assert session.constraints[0]["params"]["periods"] == [0, 1]
    assert session.consecutive_asks == 0


def test_execute_tool_unknown_name():
    result = execute_tool("nope", {}, Session(), CATALOG)
    assert result["status"] == "error"
