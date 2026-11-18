"""에이전트 시스템 프롬프트."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from agent.session import Session


def system_prompt(session: Session) -> str:
    constraints = session.constraints
    last_result = session.last_result
    return (
        "너는 원광대학교 시간표를 짜주는 도우미야.\n"
        "과목을 직접 고르지 말고, 사용자 요청을 조건으로 바꿔 도구로 처리해.\n"
        "시간표 조합은 schedule이 호출하는 솔버만 한다.\n"
        "강의 목록은 프롬프트에 없다. 과목이 필요하면 search_courses로 id를 확인한 뒤 "
        "course 조건에 course_id를 넣어.\n\n"
        "도구:\n"
        "- search_courses: 과목 검색. query/category/day 중 하나 이상 필요. "
        "사용자가 과목명만 말하면 query만으로 먼저 검색하고 day·category로 좁히지 마.\n"
        "- schedule: remove 다음 add로 조건을 바꾸고 후보를 계산. "
        "add·remove가 비면 현재 조건으로 다시 계산.\n"
        "- ask_user: 꼭 필요할 때만 되묻기. 호출하면 그 턴이 끝난다. "
        "schedule과 같은 응답에서 함께 쓰지 마. "
        "같은 이름 검색 결과가 둘 이상이면 하나를 골라 schedule하지 말고 ask_user로 물어.\n\n"
        "조건 종류:\n"
        '- free_slots: {"days"?: ["MON"..."SUN"], "periods"?: [0-10]}\n'
        '- course: {"course_id": number, "mode": "require"|"exclude"}\n'
        '- credits: {"category"?: "전공"|"교양"|구분코드, "min"?: number, "max"?: number}\n'
        '- limit: {"kind": "days"|"per_day"|"consecutive", "count": number}\n\n'
        "기본 해석 (묻지 말고 적용한 뒤 설명만 해):\n"
        "- 아침, 아침 수업, 아침 1교시 → periods [0, 1]. 1교시만 단독으로 말한 게 아니면 0과 1을 함께 넣어.\n"
        "- 점심 → periods [4]\n"
        "- 저녁 → periods [9, 10]\n"
        "- 수요일 공강처럼 요일만 비우면 free_slots days만 넣고 periods는 생략해.\n"
        '- "전공" → credits.category "전공"\n'
        "- 요일: 월=MON, 화=TUE, 수=WED, 목=THU, 금=FRI, 토=SAT, 일=SUN\n\n"
        "되묻는 경우:\n"
        "- search_courses 결과가 둘 이상이고 어떤 id를 넣을지 갈릴 때\n"
        "- 새 조건이 이미 고정한 과목과 충돌할 때\n"
        "- 기본 해석으로 정할 수 없을 때\n\n"
        f"현재 조건:\n{json.dumps(constraints, ensure_ascii=False)}\n\n"
        f"직전 후보:\n{_format_candidates(last_result)}\n"
    )


def _format_candidates(last_result: Mapping[str, Any] | None) -> str:
    if not last_result:
        return "없음"
    status = last_result.get("status")
    if status == "infeasible":
        return json.dumps(
            {
                "status": "infeasible",
                "relaxations": last_result.get("relaxations") or [],
            },
            ensure_ascii=False,
        )
    if status != "ok":
        return json.dumps(last_result, ensure_ascii=False)

    summaries: list[dict[str, Any]] = []
    for index, candidate in enumerate(last_result.get("candidates") or [], start=1):
        courses = candidate.get("courses") or []
        summaries.append(
            {
                "index": index,
                "total_credits": candidate.get("total_credits"),
                "days_used": candidate.get("days_used"),
                "courses": [
                    {
                        "id": course.get("id"),
                        "name": course.get("name"),
                        "credits": course.get("credits"),
                    }
                    for course in courses
                ],
            }
        )
    if not summaries:
        return "없음"
    return json.dumps(summaries, ensure_ascii=False)


def build_input_messages(
    session: Session,
    history: Sequence[Mapping[str, Any]],
    user_text: str,
) -> list[dict[str, Any]]:
    """Responses API input: 시스템 + 이전 대화 + 이번 사용자 메시지."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt(session)},
    ]
    for item in history:
        messages.append(dict(item))
    messages.append({"role": "user", "content": user_text})
    return messages
