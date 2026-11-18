"""표준 입력 한 줄이 한 턴인 CLI 에이전트."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.loop import make_openai_caller, run_turn
from agent.session import Session
from db.loader import load_csv
from db.validator import load_catalog

DEFAULT_MODEL = "gpt-5.6-luna"


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


def load_semester_catalog(
    csv_path: Path,
    db_path: Path,
    *,
    semester: int = 1,
):
    summary = load_csv(csv_path, db_path)
    if summary.get("parse_failures"):
        raise SystemExit("카탈로그 적재에 실패했습니다.")
    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        catalog = [
            course
            for course in load_catalog(connection)
            if course.semester == semester
        ]
    finally:
        connection.close()
    return catalog


def format_ask(ask: dict[str, Any]) -> str:
    lines = [ask["question"]]
    options = ask.get("options") or []
    for index, option in enumerate(options, start=1):
        lines.append(f"  {index}. {option}")
    if options:
        lines.append("번호를 입력하거나 답을 적어 주세요.")
    return "\n".join(lines)


def resolve_user_reply(raw: str, ask: dict[str, Any] | None) -> str:
    text = raw.strip()
    if ask is None:
        return text
    options = ask.get("options") or []
    if text.isdigit():
        index = int(text)
        if 1 <= index <= len(options):
            return str(options[index - 1])
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="원광대 시간표 에이전트 CLI")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--csv", type=Path, default=ROOT / "data" / "courses.csv")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "courses.db")
    args = parser.parse_args(argv)

    load_env(args.env)
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY가 필요합니다.", file=sys.stderr)
        return 1

    from openai import OpenAI

    catalog = load_semester_catalog(args.csv, args.db)
    client = OpenAI()
    call_model = make_openai_caller(client, args.model)
    session = Session()
    history: list[dict[str, Any]] = []
    pending_ask: dict[str, Any] | None = None

    print("시간표 에이전트. 종료하려면 빈 줄 또는 exit.")
    while True:
        try:
            raw = input("> ")
        except EOFError:
            print()
            break
        if raw.strip() == "" or raw.strip().lower() in {"exit", "quit"}:
            break

        user_text = resolve_user_reply(raw, pending_ask)
        result = run_turn(
            session,
            catalog,
            user_text,
            call_model=call_model,
            history=history,
        )
        history.append({"role": "user", "content": user_text})
        if result.ask is not None:
            pending_ask = result.ask
            reply = format_ask(result.ask)
        else:
            pending_ask = None
            reply = result.text or "(응답 없음)"
        history.append({"role": "assistant", "content": reply})
        print(reply)
        if session.last_result and result.ask is None:
            status = session.last_result.get("status")
            constraints = session.constraints
            print(f"[상태] {status}, 조건 {len(constraints)}개")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
