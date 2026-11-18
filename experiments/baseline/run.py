"""GPT-6 Luna 베이스라인 실험을 실행하고 채점한다."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.baseline.generate_data import DATA_DIR, load_catalog_csv, prompt_csv
from experiments.baseline.score import ScoreResult, score_response, summarize_scores

DEFAULT_MODEL = "gpt-5.6-luna"
SCENARIOS_PATH = Path(__file__).with_name("scenarios.json")
RESULTS_DIR = Path(__file__).with_name("results")
REPEATS = 5
SIZES = (100, 300, 500)


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


def system_prompt(catalog_csv: str) -> str:
    return (
        "너는 원광대학교 시간표를 짜주는 도우미야.\n"
        "아래 강의 목록에 있는 과목만 사용해서 사용자 요청에 맞는 시간표를 만들어.\n"
        "시간 표기는 요일 뒤에 교시가 붙는 형식이야. (예: 목3금12 = 목 3교시, 금 1·2교시)\n\n"
        "[강의 목록]\n"
        f"{catalog_csv.strip()}\n\n"
        "응답은 아래 JSON 형식으로만 해.\n"
        "{\n"
        '  "feasible": true,\n'
        '  "courses": [{ "name": "...", "credits": 3, "time": "목3금12" }],\n'
        '  "total_credits": 18,\n'
        '  "explanation": "..."\n'
        "}\n"
        '요청을 만족하는 시간표를 만들 수 없으면 "feasible": false 로 답하고 이유를 explanation에 적어.'
    )


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


def extract_output_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text
    chunks: list[str] = []
    for item in getattr(response, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            value = getattr(content, "text", None)
            if isinstance(value, str):
                chunks.append(value)
    return "".join(chunks)


def call_model(client: Any, model: str, messages: list[dict[str, str]]) -> str:
    response = client.responses.create(model=model, input=messages)
    text = extract_output_text(response)
    if not text.strip():
        raise RuntimeError("모델 응답이 비어 있습니다.")
    return text


def run_scenario(
    client: Any,
    model: str,
    catalog_csv: str,
    turns: list[str],
) -> tuple[str, list[dict[str, str]]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt(catalog_csv)},
    ]
    final_text = ""
    for turn in turns:
        messages.append({"role": "user", "content": turn})
        final_text = call_model(client, model, messages)
        messages.append({"role": "assistant", "content": final_text})
    return final_text, messages


def score_to_row(
    size: int,
    scenario: dict[str, Any],
    trial: int,
    model: str,
    response_text: str,
    score: ScoreResult,
    elapsed_ms: int,
) -> dict[str, Any]:
    return {
        "size": size,
        "scenario_id": scenario["id"],
        "trial": trial,
        "model": model,
        "expected_feasible": scenario["expected_feasible"],
        "response_text": response_text,
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
    from db.validator import Finding

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
    parser = argparse.ArgumentParser(description="Luna 베이스라인 실험을 실행합니다.")
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
    catalogs = {size: load_catalog_csv(args.data_dir / f"courses_{size}.csv") for size in args.sizes}
    prompt_texts = {size: prompt_csv(catalogs[size]) for size in args.sizes}

    for size, scenario, trial in remaining:
        key = result_key(size, scenario["id"], trial)
        started = time.time()
        response_text, _messages = run_scenario(
            client,
            args.model,
            prompt_texts[size],
            scenario["turns"],
        )
        elapsed_ms = int((time.time() - started) * 1000)
        raw_path = raw_dir / f"{size}_{scenario['id']}_{trial}.txt"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(response_text, encoding="utf-8")
        score = score_response(
            catalogs[size],
            response_text,
            scenario["constraints"],
            expected_feasible=bool(scenario["expected_feasible"]),
        )
        row = score_to_row(size, scenario, trial, args.model, response_text, score, elapsed_ms)
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
