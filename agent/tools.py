"""세션에 붙는 schedule / search_courses / ask_user 실행기."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping, Sequence

from db.time_parser import DAY_CODES
from db.validator import CatalogCourse
from agent.session import Session
from solver.solve import solve

WHITESPACE_RE = re.compile(r"\s+")
SEARCH_LIMIT = 20
MAX_ASK_OPTIONS = 4
MAX_CONSECUTIVE_ASKS = 2

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "search_courses",
        "description": (
            "과목을 검색한다. query·category·day 중 하나 이상이 필요하다. "
            "course 조건을 넣기 전에 id를 확인할 때 쓴다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "과목명 부분 검색어"},
                "category": {
                    "type": "string",
                    "description": "전공, 교양, 또는 원본 구분 코드",
                },
                "day": {
                    "type": "string",
                    "description": "해당 요일에 수업이 있는 과목. MON…SUN",
                    "enum": list(DAY_CODES),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "schedule",
        "description": (
            "조건을 수정하고 솔버로 시간표 후보를 계산한다. "
            "remove 다음 add 순서로 적용한다. course 조건은 course_id만 받는다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "add": {
                    "type": "array",
                    "description": "추가할 조건",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "type": {
                                "type": "string",
                                "enum": ["free_slots", "course", "credits", "limit"],
                            },
                            "params": {"type": "object"},
                            "source_text": {"type": "string"},
                        },
                        "required": ["type", "params"],
                        "additionalProperties": False,
                    },
                },
                "remove": {
                    "type": "array",
                    "description": "삭제할 조건 id",
                    "items": {"type": "string"},
                },
                "n": {
                    "type": "integer",
                    "description": "후보 개수, 기본 3",
                    "minimum": 1,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "ask_user",
        "description": (
            "사용자에게 되묻는다. 호출하면 이 턴이 끝난다. "
            "같은 이름이 둘 이상이거나, 고정 과목과 충돌하거나, "
            "기본 해석이 없을 때만 쓴다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": MAX_ASK_OPTIONS,
                },
                "reason": {"type": "string"},
            },
            "required": ["question", "reason"],
            "additionalProperties": False,
        },
    },
]


def compact_name(name: str) -> str:
    return WHITESPACE_RE.sub("", name)


def execute_tool(
    name: str,
    arguments: Mapping[str, Any] | None,
    session: Session,
    catalog: Sequence[CatalogCourse],
    *,
    semester: int = 1,
) -> dict[str, Any]:
    payload = dict(arguments or {})
    if name == "search_courses":
        return search_courses(catalog, payload, semester=semester)
    if name == "schedule":
        return schedule(session, catalog, payload, semester=semester)
    if name == "ask_user":
        return ask_user(session, payload)
    return {"status": "error", "message": f"알 수 없는 도구: {name}"}


def search_courses(
    catalog: Sequence[CatalogCourse],
    arguments: Mapping[str, Any],
    *,
    semester: int = 1,
) -> dict[str, Any]:
    query = arguments.get("query")
    category = arguments.get("category")
    day = arguments.get("day")
    has_query = query is not None and str(query).strip() != ""
    has_category = category is not None and str(category).strip() != ""
    has_day = day is not None and str(day).strip() != ""
    if not (has_query or has_category or has_day):
        return {
            "status": "error",
            "message": "query, category, day 중 하나 이상이 필요합니다.",
        }

    if has_day:
        day_code = str(day).strip().upper()
        if day_code not in DAY_CODES:
            return {"status": "error", "message": f"알 수 없는 요일: {day}"}
    else:
        day_code = None

    needle = compact_name(str(query)) if has_query else ""
    category_value = str(category).strip() if has_category else ""

    matches: list[dict[str, Any]] = []
    for course in catalog:
        if course.semester != semester or not course.schedulable:
            continue
        name_key = compact_name(course.name)
        exact_name = bool(needle) and name_key == needle
        if needle and needle not in name_key:
            continue
        if category_value:
            if category_value in {"전공", "교양"}:
                if course.category_group != category_value:
                    continue
            elif course.category != category_value:
                continue
        # 이름이 정확히 같으면 day 필터로 한쪽만 버리지 않는다.
        if (
            day_code is not None
            and not exact_name
            and not any(slot[0] == day_code for slot in course.slots)
        ):
            continue
        matches.append(_course_payload(course))

    truncated = len(matches) > SEARCH_LIMIT
    return {
        "status": "ok",
        "courses": matches[:SEARCH_LIMIT],
        "truncated": truncated,
        "total": len(matches),
    }


def schedule(
    session: Session,
    catalog: Sequence[CatalogCourse],
    arguments: Mapping[str, Any],
    *,
    semester: int = 1,
) -> dict[str, Any]:
    add_raw = arguments.get("add") or []
    remove_raw = arguments.get("remove") or []
    n = arguments.get("n", 3)

    if not isinstance(add_raw, list):
        return {"status": "error", "message": "add는 배열이어야 합니다."}
    if not isinstance(remove_raw, list):
        return {"status": "error", "message": "remove는 배열이어야 합니다."}
    try:
        candidate_count = 3 if n is None else int(n)
    except (TypeError, ValueError):
        return {"status": "error", "message": f"n이 올바르지 않습니다: {n}"}
    if candidate_count < 1:
        return {"status": "error", "message": f"n은 1 이상이어야 합니다: {candidate_count}"}

    remove_ids = [str(item) for item in remove_raw]
    working = session.snapshot_constraints()
    by_id = {str(item["id"]): item for item in working if "id" in item}
    for constraint_id in remove_ids:
        if constraint_id not in by_id:
            return {
                "status": "error",
                "message": f"존재하지 않는 조건 id: {constraint_id}",
            }

    remaining = [item for item in working if str(item.get("id")) not in set(remove_ids)]
    for raw in add_raw:
        if not isinstance(raw, Mapping):
            return {"status": "error", "message": "add 항목이 객체가 아닙니다."}
        if "type" not in raw:
            return {"status": "error", "message": "add 항목에 type이 없습니다."}
        item = {
            "type": raw["type"],
            "params": dict(raw.get("params") or {}),
        }
        if raw.get("id") is not None:
            item["id"] = str(raw["id"])
        if raw.get("source_text") is not None:
            item["source_text"] = str(raw["source_text"])
        remaining.append(item)

    try:
        result = solve(catalog, remaining, n=candidate_count, semester=semester)
    except ValueError as error:
        return {"status": "error", "message": str(error)}

    session.constraints = [dict(item) for item in result["constraints"]]
    session.last_result = copy.deepcopy(result)
    return copy.deepcopy(result)


def ask_user(session: Session, arguments: Mapping[str, Any]) -> dict[str, Any]:
    question = str(arguments.get("question") or "").strip()
    reason = str(arguments.get("reason") or "").strip()
    if not question:
        return {"status": "error", "message": "question이 비어 있습니다."}
    if not reason:
        return {"status": "error", "message": "reason이 비어 있습니다."}

    options_raw = arguments.get("options")
    options: list[str] | None
    if options_raw is None:
        options = None
    elif not isinstance(options_raw, list):
        return {"status": "error", "message": "options는 배열이어야 합니다."}
    else:
        if len(options_raw) > MAX_ASK_OPTIONS:
            return {
                "status": "error",
                "message": f"options는 최대 {MAX_ASK_OPTIONS}개입니다.",
            }
        options = [str(item) for item in options_raw]

    if session.consecutive_asks >= MAX_CONSECUTIVE_ASKS:
        return {
            "status": "error",
            "message": (
                "연속 되묻기 상한을 넘었습니다. "
                "기본 해석으로 schedule을 진행하세요."
            ),
        }

    session.consecutive_asks += 1
    payload: dict[str, Any] = {
        "status": "ask",
        "question": question,
        "reason": reason,
    }
    if options is not None:
        payload["options"] = options
    return payload


def _course_payload(course: CatalogCourse) -> dict[str, Any]:
    return {
        "id": course.id,
        "category": course.category,
        "name": course.name,
        "credits": course.credits,
        "raw_time": course.raw_time,
        "slots": [{"day": day, "period": period} for day, period in course.slots],
    }
