"""모델이 schedule 조건만 내고 솔버 시간표를 M1과 같은 채점으로 비교한다."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.validator import CatalogCourse, Finding
from experiments.baseline.generate_data import DATA_DIR, load_catalog_csv
from experiments.baseline.score import ScoreResult, score_response, summarize_scores
from solver.solve import solve

DEFAULT_MODEL = "gpt-5.6-luna"
SCENARIOS_PATH = Path(__file__).resolve().parents[1] / "baseline" / "scenarios.json"
RESULTS_DIR = Path(__file__).with_name("results")
REPEATS = 5
SIZES = (100, 300, 500)
WHITESPACE_RE = re.compile(r"\s+")

SCHEDULE_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "schedule",
    "description": (
        "사용자 요청을 만족하는 현재 조건 전체를 솔버에 넘긴다. "
        "과목을 고르지 말고, 조건만 제출한다. 매 호출마다 누적된 전체 조건을 넣는다."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "constraints": {
                "type": "array",
                "description": "현재 적용할 조건 전체",
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
            }
        },
        "required": ["constraints"],
        "additionalProperties": False,
    },
}


class ResolveError(Exception):
    """과목명 해석 또는 schedule 인자 오류."""


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def system_prompt() -> str:
    return (
        "너는 원광대학교 시간표를 짜주는 도우미야.\n"
        "과목을 직접 고르지 말고, 사용자 요청을 조건 JSON으로 바꿔 schedule 도구만 호출해.\n"
        "매 턴마다 현재 조건 전체를 constraints에 넣어. 서버가 이전 조건을 기억하지 않는다.\n"
        "강의 목록은 너에게 주지 않는다. 과목 지정은 사용자가 말한 이름 그대로 params.name에 적어.\n\n"
        "조건 종류:\n"
        '- free_slots: {"days"?: ["MON"..."SUN"], "periods"?: [0-10]}\n'
        '- course: {"name": "과목명", "mode": "require"|"exclude"}\n'
        '- credits: {"category"?: "전공"|"교양"|구분코드, "min"?: number, "max"?: number}\n'
        '- limit: {"kind": "days"|"per_day"|"consecutive", "count": number}\n\n'
        "기본 해석:\n"
        "- 아침 → periods [0, 1]\n"
        "- 점심 → periods [4]\n"
        "- 저녁 → periods [9, 10]\n"
        '- "전공" → credits.category "전공"\n'
        "- 요일: 월=MON, 화=TUE, 수=WED, 목=THU, 금=FRI, 토=SAT, 일=SUN\n"
    )


def compact_name(name: str) -> str:
    return WHITESPACE_RE.sub("", name)


def find_unique_named_course(
    catalog: Sequence[CatalogCourse],
    name: str,
) -> CatalogCourse:
    target = compact_name(name)
    if not target:
        raise ResolveError("과목명이 비어 있습니다.")
    matched = [course for course in catalog if compact_name(course.name) == target]
    if not matched:
        raise ResolveError(f"카탈로그에 없는 과목명: {name}")
    if len(matched) > 1:
        raise ResolveError(f"과목명이 둘 이상입니다: {name}")
    return matched[0]


def resolve_named_constraints(
    catalog: Sequence[CatalogCourse],
    constraints: Sequence[Mapping],
) -> list[dict]:
    """모델이 낸 조건에서 course.name을 course_id로 바꾼다."""
    resolved: list[dict] = []
    for raw in constraints:
        if not isinstance(raw, Mapping):
            raise ResolveError("조건 항목이 객체가 아닙니다.")
        constraint_type = raw.get("type")
        if constraint_type is None:
            raise ResolveError("조건 type이 없습니다.")
        params = dict(raw.get("params") or {})
        item: dict[str, Any] = {
            "type": constraint_type,
            "params": params,
        }
        if raw.get("id") is not None:
            item["id"] = raw["id"]
        if raw.get("source_text") is not None:
            item["source_text"] = raw["source_text"]

        if constraint_type == "course":
            if "course_id" in params:
                raise ResolveError("course 조건은 name으로만 지정해야 합니다.")
            name = str(params.get("name") or "").strip()
            course = find_unique_named_course(catalog, name)
            mode = params.get("mode")
            item["params"] = {"course_id": course.id, "mode": mode}
        resolved.append(item)
    return resolved


def candidate_to_score_payload(candidate: Mapping) -> dict[str, Any]:
    courses = []
    for item in candidate.get("courses") or []:
        courses.append(
            {
                "name": item["name"],
                "credits": item["credits"],
                "time": item.get("time") or item.get("raw_time"),
            }
        )
    return {
        "feasible": True,
        "courses": courses,
        "total_credits": candidate["total_credits"],
        "explanation": "solver",
    }


def solver_result_to_response_text(result: Mapping) -> str:
    status = result.get("status")
    if status == "infeasible":
        payload = {
            "feasible": False,
            "courses": [],
            "total_credits": 0,
            "explanation": "infeasible",
        }
        return json.dumps(payload, ensure_ascii=False)
    if status != "ok":
        raise ValueError(f"알 수 없는 솔버 status: {status}")
    candidates = result.get("candidates") or []
    if not candidates:
        payload = {
            "feasible": False,
            "courses": [],
            "total_credits": 0,
            "explanation": "empty candidates",
        }
        return json.dumps(payload, ensure_ascii=False)
    return json.dumps(candidate_to_score_payload(candidates[0]), ensure_ascii=False)


def e8_result(message: str) -> ScoreResult:
    return ScoreResult(
        findings=(Finding("E8", message),),
        parsed=None,
        parse_error=message,
    )


def parse_schedule_arguments(arguments: str | Mapping) -> list[dict]:
    if isinstance(arguments, Mapping):
        payload = dict(arguments)
    else:
        try:
            payload = json.loads(arguments)
        except json.JSONDecodeError as error:
            raise ResolveError(f"schedule 인자 JSON 파싱 실패: {error.msg}") from error
    if not isinstance(payload, dict):
        raise ResolveError("schedule 인자가 객체가 아닙니다.")
    constraints = payload.get("constraints")
    if not isinstance(constraints, list):
        raise ResolveError("constraints가 배열이 아닙니다.")
    return [dict(item) if isinstance(item, Mapping) else item for item in constraints]


def extract_schedule_call(response: Any) -> tuple[str | None, str | None]:
    """Responses API 출력에서 schedule 호출 인자를 꺼낸다. (arguments, error)."""
    calls: list[Any] = []
    for item in getattr(response, "output", None) or []:
        item_type = getattr(item, "type", None)
        if item_type == "function_call" and getattr(item, "name", None) == "schedule":
            calls.append(item)
        elif isinstance(item, Mapping) and item.get("type") == "function_call":
            if item.get("name") == "schedule":
                calls.append(item)
    if not calls:
        return None, "schedule 도구를 호출하지 않았습니다."
    if len(calls) > 1:
        return None, f"schedule 호출이 {len(calls)}번입니다."
    call = calls[0]
    arguments = getattr(call, "arguments", None)
    if arguments is None and isinstance(call, Mapping):
        arguments = call.get("arguments")
    if arguments is None:
        return None, "schedule 인자가 없습니다."
    if isinstance(arguments, Mapping):
        return json.dumps(arguments, ensure_ascii=False), None
    return str(arguments), None


def apply_schedule(
    catalog: Sequence[CatalogCourse],
    constraints: Sequence[Mapping],
    n: int = 1,
) -> tuple[str, list[dict], Mapping]:
    """이름 해석 후 솔버를 돌리고 채점용 JSON 문자열을 반환한다."""
    resolved = resolve_named_constraints(catalog, constraints)
    result = solve(catalog, resolved, n=n)
    return solver_result_to_response_text(result), resolved, result


def assistant_constraint_message(constraints: Sequence[Mapping]) -> str:
    return json.dumps({"constraints": list(constraints)}, ensure_ascii=False)


def build_messages(turns: Sequence[str], prior_constraints: Sequence[Sequence[Mapping]]) -> list[dict]:
    """이전 턴의 조건 JSON만 이어 붙인 메시지 목록을 만든다."""
    messages: list[dict] = [{"role": "system", "content": system_prompt()}]
    for index, turn in enumerate(turns):
        messages.append({"role": "user", "content": turn})
        if index < len(prior_constraints):
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_constraint_message(prior_constraints[index]),
                }
            )
    return messages


def call_model_for_schedule(client: Any, model: str, messages: list[dict]) -> Any:
    return client.responses.create(
        model=model,
        input=messages,
        tools=[SCHEDULE_TOOL],
        tool_choice={"type": "function", "name": "schedule"},
    )


def run_scenario(
    client: Any,
    model: str,
    catalog: Sequence[CatalogCourse],
    turns: Sequence[str],
) -> dict[str, Any]:
    """모든 턴을 돌리고 마지막 턴의 솔버 결과만 채점 입력으로 남긴다."""
    prior_constraints: list[list[dict]] = []
    last_response_text = ""
    last_resolved: list[dict] = []
    last_solver: Mapping | None = None
    last_raw_arguments: str | None = None
    turn_trace: list[dict[str, Any]] = []

    for turn_index, _turn in enumerate(turns):
        messages = build_messages(turns[: turn_index + 1], prior_constraints)
        response = call_model_for_schedule(client, model, messages)
        arguments, extract_error = extract_schedule_call(response)
        if extract_error is not None or arguments is None:
            return {
                "response_text": None,
                "error": extract_error or "schedule 호출 추출 실패",
                "raw_arguments": None,
                "model_constraints": None,
                "resolved_constraints": None,
                "solver_result": None,
                "turn_trace": turn_trace,
                "scored_turn": turn_index,
            }
        try:
            model_constraints = parse_schedule_arguments(arguments)
            response_text, resolved, solver_result = apply_schedule(catalog, model_constraints)
        except ResolveError as error:
            return {
                "response_text": None,
                "error": str(error),
                "raw_arguments": arguments,
                "model_constraints": None,
                "resolved_constraints": None,
                "solver_result": None,
                "turn_trace": turn_trace,
                "scored_turn": turn_index,
            }
        except Exception as error:  # noqa: BLE001 - 실험 러너는 호출 단위로 기록한다.
            return {
                "response_text": None,
                "error": f"솔버 실행 실패: {error}",
                "raw_arguments": arguments,
                "model_constraints": model_constraints,
                "resolved_constraints": None,
                "solver_result": None,
                "turn_trace": turn_trace,
                "scored_turn": turn_index,
            }

        prior_constraints.append(model_constraints)
        last_response_text = response_text
        last_resolved = resolved
        last_solver = solver_result
        last_raw_arguments = arguments
        turn_trace.append(
            {
                "turn": turn_index + 1,
                "raw_arguments": arguments,
                "model_constraints": model_constraints,
                "resolved_constraints": resolved,
                "solver_status": solver_result.get("status"),
            }
        )

    return {
        "response_text": last_response_text,
        "error": None,
        "raw_arguments": last_raw_arguments,
        "model_constraints": prior_constraints[-1] if prior_constraints else None,
        "resolved_constraints": last_resolved,
        "solver_result": last_solver,
        "turn_trace": turn_trace,
        "scored_turn": len(turns) - 1,
    }


def result_key(size: int, scenario_id: str, trial: int) -> str:
    return f"{size}:{scenario_id}:{trial}"


def load_completed(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    completed: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            completed[result_key(row["size"], row["scenario_id"], row["trial"])] = row
    return completed


def append_result(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def score_run(
    catalog: Sequence[CatalogCourse],
    scenario: Mapping[str, Any],
    run_result: Mapping[str, Any],
) -> ScoreResult:
    if run_result.get("error"):
        return e8_result(str(run_result["error"]))
    response_text = run_result.get("response_text")
    if not isinstance(response_text, str) or not response_text.strip():
        return e8_result("채점용 응답이 비어 있습니다.")
    return score_response(
        catalog,
        response_text,
        scenario["constraints"],
        expected_feasible=bool(scenario["expected_feasible"]),
    )


def score_to_row(
    size: int,
    scenario: dict[str, Any],
    trial: int,
    model: str,
    run_result: Mapping[str, Any],
    score: ScoreResult,
    elapsed_ms: int,
) -> dict[str, Any]:
    return {
        "size": size,
        "scenario_id": scenario["id"],
        "trial": trial,
        "model": model,
        "expected_feasible": scenario["expected_feasible"],
        "response_text": run_result.get("response_text"),
        "error": run_result.get("error"),
        "raw_arguments": run_result.get("raw_arguments"),
        "model_constraints": run_result.get("model_constraints"),
        "resolved_constraints": run_result.get("resolved_constraints"),
        "solver_status": (run_result.get("solver_result") or {}).get("status")
        if isinstance(run_result.get("solver_result"), Mapping)
        else None,
        "turn_trace": run_result.get("turn_trace"),
        "scored_turn": run_result.get("scored_turn"),
        "parsed": score.parsed,
        "ok": score.ok,
        "codes": score.codes,
        "findings": [
            {"code": finding.code, "message": finding.message} for finding in score.findings
        ],
        "elapsed_ms": elapsed_ms,
    }


def print_summary(rows: list[dict[str, Any]], model: str, sizes: list[int]) -> None:
    print(f"model={model}")
    print("size\tperfect_rate\tperfect/total\tE1\tE2\tE3\tE4\tE5\tE6\tE7\tE8")
    for size in sizes:
        subset = [row for row in rows if row["size"] == size]
        summary = _summarize_rows(subset)
        counts = summary["error_counts"]
        print(
            f"{size}\t{summary['perfect_rate']:.2%}\t"
            f"{summary['perfect']}/{summary['total']}\t"
            f"{counts['E1']}\t{counts['E2']}\t{counts['E3']}\t{counts['E4']}\t"
            f"{counts['E5']}\t{counts['E6']}\t{counts['E7']}\t{counts['E8']}"
        )


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    results = [
        ScoreResult(
            findings=tuple(
                Finding(item["code"], item["message"]) for item in row.get("findings", [])
            ),
            parsed=row.get("parsed"),
        )
        for row in rows
    ]
    return summarize_scores(results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="솔버 도구 비교 실험을 실행합니다.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--sizes", nargs="+", type=int, default=list(SIZES))
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument("--scenarios", type=Path, default=SCENARIOS_PATH)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="API를 호출하지 않고 실행 계획만 출력합니다.",
    )
    args = parser.parse_args(argv)

    load_env(args.env)
    scenarios = json.loads(args.scenarios.read_text(encoding="utf-8"))
    results_path = args.results_dir / f"{args.model.replace('/', '_')}.jsonl"
    raw_dir = args.results_dir / "raw" / args.model.replace("/", "_")
    completed = load_completed(results_path)

    planned = [
        (size, scenario, trial)
        for size in args.sizes
        for scenario in scenarios
        for trial in range(1, args.repeats + 1)
    ]
    remaining = [
        item
        for item in planned
        if result_key(item[0], item[1]["id"], item[2]) not in completed
    ]
    print(f"planned={len(planned)} completed={len(completed)} remaining={len(remaining)}")
    if args.dry_run:
        return 0

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY가 없습니다.")

    from openai import OpenAI

    client = OpenAI()
    catalogs = {
        size: load_catalog_csv(args.data_dir / f"courses_{size}.csv") for size in args.sizes
    }

    for size, scenario, trial in remaining:
        key = result_key(size, scenario["id"], trial)
        started = time.time()
        run_result = run_scenario(client, args.model, catalogs[size], scenario["turns"])
        elapsed_ms = int((time.time() - started) * 1000)
        raw_path = raw_dir / f"{size}_{scenario['id']}_{trial}.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(
            json.dumps(run_result, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        score = score_run(catalogs[size], scenario, run_result)
        row = score_to_row(size, scenario, trial, args.model, run_result, score, elapsed_ms)
        append_result(results_path, row)
        completed[key] = row
        status = "ok" if score.ok else ",".join(score.codes) or "fail"
        print(f"{key} {status} {elapsed_ms}ms", flush=True)

    all_rows = list(completed.values())
    print_summary(all_rows, args.model, args.sizes)
    summary_path = args.results_dir / f"{args.model.replace('/', '_')}_summary.json"
    summary = {
        "model": args.model,
        "by_size": {
            str(size): _summarize_rows([row for row in all_rows if row["size"] == size])
            for size in args.sizes
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved={results_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
