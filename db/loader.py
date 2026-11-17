"""courses.csv를 SQLite에 적재한다."""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

from db.time_parser import parse_time

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def load_csv(csv_path: Path, db_path: Path) -> dict[str, object]:
    """CSV를 새 데이터베이스에 넣는다. 파싱에 실패하면 데이터베이스를 만들지 않는다."""
    failures: list[str] = []
    courses: list[dict[str, object]] = []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for line_number, row in enumerate(reader, start=2):
            raw_time = row["raw_time"].strip()
            slots, error = parse_time(raw_time)
            if error:
                failures.append(f"{line_number}행: 파싱 실패 {row['course_code']} {raw_time!r} ({error})")
                continue
            schedulable = int(row["schedulable"])
            if schedulable != (1 if slots else 0):
                failures.append(
                    f"{line_number}행: schedulable 값이 슬롯과 다릅니다 ({row['course_code']})."
                )
            courses.append({**row, "slots": slots, "raw_time": raw_time})

    summary = _summarize(courses, failures)
    if failures:
        return summary

    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        for course in courses:
            cursor = connection.execute(
                """
                INSERT INTO courses (
                    semester, course_code, category, category_group, name, name_en,
                    credits, theory, practice, raw_time, schedulable, year, area, department
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(course["semester"]),
                    course["course_code"],
                    course["category"],
                    course["category_group"],
                    course["name"],
                    _empty_none(course.get("name_en")),
                    int(course["credits"]),
                    _optional_int(course.get("theory")),
                    _optional_int(course.get("practice")),
                    course["raw_time"],
                    int(course["schedulable"]),
                    _optional_int(course.get("year")),
                    _empty_none(course.get("area")),
                    _empty_none(course.get("department")),
                ),
            )
            course_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO course_slots (course_id, day, period) VALUES (?, ?, ?)",
                [(course_id, day, period) for day, period in course["slots"]],
            )
        connection.commit()
    except Exception:
        connection.close()
        db_path.unlink(missing_ok=True)
        raise
    connection.close()
    return summary


def _summarize(courses: list[dict[str, object]], failures: list[str]) -> dict[str, object]:
    by_semester = {1: 0, 2: 0}
    unschedulable: list[str] = []
    slot_count = 0
    for course in courses:
        semester = int(course["semester"])
        by_semester[semester] = by_semester.get(semester, 0) + 1
        slot_count += len(course["slots"])
        if int(course["schedulable"]) == 0:
            unschedulable.append(f"{semester}학기 {course['course_code']} {course['name']}")
    return {
        "courses": len(courses),
        "by_semester": by_semester,
        "slots": slot_count,
        "unschedulable": unschedulable,
        "parse_failures": failures,
    }


def summary_lines(summary: dict[str, object]) -> list[str]:
    by_semester = summary["by_semester"]
    lines = [
        f"적재 과목: {summary['courses']}",
        f"1학기: {by_semester.get(1, 0)}",
        f"2학기: {by_semester.get(2, 0)}",
        f"슬롯: {summary['slots']}",
        "시간 없음:",
    ]
    unschedulable = summary["unschedulable"]
    lines.extend(f"  {item}" for item in unschedulable)
    if not unschedulable:
        lines.append("  (없음)")
    failures = summary["parse_failures"]
    if failures:
        lines.append("파싱 실패:")
        lines.extend(f"  {item}" for item in failures)
    else:
        lines.append("파싱 실패: (없음)")
    return lines


def _empty_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    text = _empty_none(value)
    if text is None:
        return None
    return int(text)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="courses.csv를 SQLite에 적재합니다.")
    parser.add_argument("--csv", type=Path, default=ROOT / "data" / "courses.csv")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "courses.db")
    args = parser.parse_args(argv)
    summary = load_csv(args.csv, args.db)
    for line in summary_lines(summary):
        print(line)
    if summary["parse_failures"]:
        return 1
    print(f"저장: {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
