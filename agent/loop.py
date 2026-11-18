"""도구 호출 루프. ask_user면 턴을 끝내고, schedule과 같이 오면 거부한다."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from agent.prompt import build_input_messages
from agent.session import Session
from agent.tools import TOOL_DEFINITIONS, execute_tool
from db.validator import CatalogCourse

MAX_TOOL_ROUNDS = 6
ModelCaller = Callable[..., Any]


@dataclass
class TurnResult:
    text: str
    ask: dict[str, Any] | None = None
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 0


def run_turn(
    session: Session,
    catalog: Sequence[CatalogCourse],
    user_text: str,
    *,
    call_model: ModelCaller,
    history: Sequence[Mapping[str, Any]] | None = None,
    max_rounds: int = MAX_TOOL_ROUNDS,
    semester: int = 1,
) -> TurnResult:
    pending_input: list[dict[str, Any]] = build_input_messages(
        session, history or [], user_text
    )
    previous_response_id: str | None = None
    trace: list[dict[str, Any]] = []
    final_text = ""
    ask_payload: dict[str, Any] | None = None

    for round_index in range(max_rounds):
        response = call_model(
            pending_input,
            TOOL_DEFINITIONS,
            previous_response_id=previous_response_id,
        )
        calls = extract_function_calls(response)
        text = extract_assistant_text(response)
        if text:
            final_text = text

        if not calls:
            break

        names = {call["name"] for call in calls}
        tool_outputs: list[dict[str, Any]] = []
        if "schedule" in names and "ask_user" in names:
            for call in calls:
                error = {
                    "status": "error",
                    "message": "schedule과 ask_user는 같은 응답에서 함께 쓸 수 없습니다.",
                }
                tool_outputs.append(_function_call_output(call, error))
                trace.append(
                    {
                        "round": round_index + 1,
                        "name": call["name"],
                        "arguments": call["arguments"],
                        "result": error,
                        "rejected": True,
                    }
                )
            previous_response_id = _response_id(response)
            pending_input = tool_outputs
            continue

        ordered = _order_calls(calls)
        ended_with_ask = False
        for call in ordered:
            arguments = call["arguments"]
            if "_parse_error" in arguments:
                result = {"status": "error", "message": arguments["_parse_error"]}
            else:
                result = execute_tool(
                    call["name"],
                    arguments,
                    session,
                    catalog,
                    semester=semester,
                )
            tool_outputs.append(_function_call_output(call, result))
            trace.append(
                {
                    "round": round_index + 1,
                    "name": call["name"],
                    "arguments": arguments,
                    "result": result,
                }
            )
            if result.get("status") == "ask":
                ask_payload = result
                ended_with_ask = True
                if not final_text:
                    final_text = str(result.get("question") or "")
                break

        if ended_with_ask:
            break

        previous_response_id = _response_id(response)
        pending_input = tool_outputs
    else:
        if not final_text:
            final_text = "도구 호출 상한에 도달했습니다. 다시 요청해 주세요."

    if ask_payload is None:
        session.consecutive_asks = 0

    return TurnResult(
        text=final_text,
        ask=ask_payload,
        tool_trace=trace,
        rounds=len({item["round"] for item in trace}) if trace else 0,
    )


def extract_function_calls(response: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in getattr(response, "output", None) or []:
        item_type = _attr(item, "type")
        if item_type != "function_call":
            continue
        name = _attr(item, "name")
        call_id = _attr(item, "call_id") or _attr(item, "id") or f"call_{len(calls)+1}"
        raw_arguments = _attr(item, "arguments")
        arguments = _parse_arguments(raw_arguments)
        calls.append(
            {
                "name": str(name or ""),
                "call_id": str(call_id),
                "arguments": arguments,
                "raw": item,
            }
        )
    return calls


def extract_assistant_text(response: Any) -> str:
    direct = getattr(response, "output_text", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    chunks: list[str] = []
    for item in getattr(response, "output", None) or []:
        if _attr(item, "type") != "message":
            continue
        content = _attr(item, "content") or []
        for part in content:
            part_type = _attr(part, "type")
            if part_type in {"output_text", "text"}:
                text = _attr(part, "text")
                if text:
                    chunks.append(str(text))
            elif isinstance(part, str):
                chunks.append(part)
    return "\n".join(chunks).strip()


def _order_calls(calls: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    priority = {"search_courses": 0, "schedule": 1, "ask_user": 2}
    return sorted(calls, key=lambda call: priority.get(str(call["name"]), 99))


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {"_parse_error": f"인자 JSON 파싱 실패: {text[:200]}"}
        if isinstance(payload, Mapping):
            return dict(payload)
        return {"_parse_error": "인자 JSON이 객체가 아닙니다."}
    return {"_parse_error": "인자 형식이 올바르지 않습니다."}


def _function_call_output(call: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "function_call_output",
        "call_id": call["call_id"],
        "output": json.dumps(result, ensure_ascii=False),
    }


def _response_id(response: Any) -> str | None:
    value = getattr(response, "id", None)
    if value is None and isinstance(response, Mapping):
        value = response.get("id")
    return None if value is None else str(value)


def _attr(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def make_openai_caller(client: Any, model: str) -> ModelCaller:
    def call_model(
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        previous_response_id: str | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "tools": tools,
        }
        if previous_response_id is not None:
            kwargs["previous_response_id"] = previous_response_id
        return client.responses.create(**kwargs)

    return call_model
