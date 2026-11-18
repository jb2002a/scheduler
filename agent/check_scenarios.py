"""S1–S10과 되묻기 케이스를 실제 카탈로그·모델로 한 번씩 확인한다."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.cli import load_env, load_semester_catalog
from agent.loop import make_openai_caller, run_turn
from agent.session import Session
from db.validator import CatalogCourse
from experiments.solver_compare.run import compact_name

DEFAULT_MODEL = "gpt-5.6-luna"
SCENARIOS_PATH = ROOT / "experiments" / "baseline" / "scenarios.json"
WHITESPACE_RE = re.compile(r"\s+")


ALL_PERIODS = list(range(0, 11))


def normalize_params(params: Mapping[str, Any], constraint_type: str | None = None) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, list):
            if key in {"days", "periods"}:
                normalized[key] = sorted(value)
            else:
                normalized[key] = value
        else:
            normalized[key] = value
    if constraint_type == "free_slots":
        periods = normalized.get("periods")
        if periods == ALL_PERIODS:
            normalized.pop("periods", None)
    return normalized


def resolve_gold_constraints(
    catalog: Sequence[CatalogCourse],
    constraints: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for raw in constraints:
        item = {
            "type": raw["type"],
            "params": dict(raw.get("params") or {}),
        }
        if item["type"] == "course" and "name" in item["params"]:
            name = str(item["params"]["name"])
            target = compact_name(name)
            matches = [
                course
                for course in catalog
                if course.schedulable and compact_name(course.name) == target
            ]
            if len(matches) != 1:
                raise ValueError(f"정답 과목명을 유일하게 해석할 수 없습니다: {name}")
            item["params"] = {
                "course_id": matches[0].id,
                "mode": item["params"]["mode"],
            }
        resolved.append(item)
    return resolved


def constraint_key(item: Mapping[str, Any]) -> tuple:
    constraint_type = item["type"]
    return (
        constraint_type,
        json.dumps(
            normalize_params(item["params"], constraint_type),
            sort_keys=True,
            ensure_ascii=False,
        ),
    )


def constraints_match(
    actual: Sequence[Mapping[str, Any]],
    expected: Sequence[Mapping[str, Any]],
) -> bool:
    actual_keys = sorted(constraint_key(item) for item in actual)
    expected_keys = sorted(constraint_key(item) for item in expected)
    return actual_keys == expected_keys


def run_conversation(
    session: Session,
    catalog: Sequence[CatalogCourse],
    turns: Sequence[str],
    call_model,
) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    last_result = None
    for turn in turns:
        last_result = run_turn(
            session,
            catalog,
            turn,
            call_model=call_model,
            history=history,
        )
        history.append({"role": "user", "content": turn})
        content = last_result.text
        if last_result.ask is not None:
            content = last_result.ask.get("question") or content
        history.append({"role": "assistant", "content": content or ""})
        if last_result.ask is not None:
            break
    return {
        "result": last_result,
        "constraints": session.snapshot_constraints(),
        "asked": last_result.ask is not None if last_result else False,
        "ask": last_result.ask if last_result else None,
        "trace": last_result.tool_trace if last_result else [],
    }


def check_scenarios(catalog, call_model, scenarios: list[dict]) -> list[dict]:
    reports = []
    for scenario in scenarios:
        session = Session()
        expected = resolve_gold_constraints(catalog, scenario["constraints"])
        try:
            outcome = run_conversation(session, catalog, scenario["turns"], call_model)
            ok = constraints_match(outcome["constraints"], expected)
            if scenario["id"] == "S8":
                ok = ok and session.last_result is not None and session.last_result.get("status") == "infeasible"
            elif scenario.get("expected_feasible", True):
                ok = ok and (
                    session.last_result is None
                    or session.last_result.get("status") == "ok"
                )
            reports.append(
                {
                    "id": scenario["id"],
                    "ok": ok,
                    "asked": outcome["asked"],
                    "actual": outcome["constraints"],
                    "expected": expected,
                }
            )
        except Exception as error:  # noqa: BLE001
            reports.append(
                {
                    "id": scenario["id"],
                    "ok": False,
                    "error": str(error),
                }
            )
    return reports


def check_ask_ambiguous(catalog, call_model) -> dict[str, Any]:
    session = Session()
    outcome = run_conversation(
        session,
        catalog,
        ["컴퓨터개론은 빼줘."],
        call_model,
    )
    asked = outcome["asked"]
    unchanged = outcome["constraints"] == []
    return {
        "id": "ASK_AMBIGUOUS",
        "ok": asked and unchanged,
        "asked": asked,
        "ask": outcome["ask"],
        "constraints": outcome["constraints"],
    }


def check_morning_no_ask(catalog, call_model) -> dict[str, Any]:
    session = Session()
    outcome = run_conversation(
        session,
        catalog,
        ["아침 비워줘."],
        call_model,
    )
    expected = [{"type": "free_slots", "params": {"periods": [0, 1]}}]
    ok = (not outcome["asked"]) and constraints_match(outcome["constraints"], expected)
    return {
        "id": "MORNING_NO_ASK",
        "ok": ok,
        "asked": outcome["asked"],
        "constraints": outcome["constraints"],
        "expected": expected,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M4 시나리오 확인")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--csv", type=Path, default=ROOT / "data" / "courses.csv")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "courses.db")
    parser.add_argument("--scenarios", type=Path, default=SCENARIOS_PATH)
    args = parser.parse_args(argv)

    load_env(args.env)
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY가 필요합니다.", file=sys.stderr)
        return 1

    from openai import OpenAI

    catalog = load_semester_catalog(args.csv, args.db)
    client = OpenAI()
    call_model = make_openai_caller(client, args.model)
    scenarios = json.loads(args.scenarios.read_text(encoding="utf-8"))

    reports = check_scenarios(catalog, call_model, scenarios)
    reports.append(check_ask_ambiguous(catalog, call_model))
    reports.append(check_morning_no_ask(catalog, call_model))

    failed = [item for item in reports if not item.get("ok")]
    for item in reports:
        mark = "OK" if item.get("ok") else "FAIL"
        extra = ""
        if item.get("error"):
            extra = f" error={item['error']}"
        elif item.get("asked") is not None:
            extra = f" asked={item['asked']}"
        print(f"{mark} {item['id']}{extra}")
        if not item.get("ok") and "actual" in item:
            print(f"  actual={json.dumps(item['actual'], ensure_ascii=False)}")
            print(f"  expected={json.dumps(item['expected'], ensure_ascii=False)}")

    print(f"통과 {len(reports) - len(failed)}/{len(reports)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
