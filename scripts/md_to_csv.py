"""마크다운 강의표를 1학기 courses.csv로 변환한다."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.time_parser import parse_time

CSV_COLUMNS = [
    "semester",
    "course_code",
    "category",
    "category_group",
    "name",
    "name_en",
    "credits",
    "theory",
    "practice",
    "raw_time",
    "schedulable",
    "year",
    "area",
    "department",
]

REQUIRED_COLUMNS = ("학기", "구분", "학수번호", "교과목명", "학점", "강의시간")
CATALOG_SEMESTER = 1
IDENTITY_FIELDS = ("category", "name", "name_en", "credits", "raw_time")
IDENTITY_LABELS = {
    "category": "구분",
    "name": "과목명",
    "name_en": "영문명",
    "credits": "학점",
    "raw_time": "강의시간",
}
FILL_FIELDS = ("name_en", "theory", "practice", "year", "area", "department")


@dataclass
class Draft:
    line: int
    semester: int
    course_code: str
    category: str
    category_group: str
    name: str
    name_en: str | None
    credits: int
    theory: int | None
    practice: int | None
    raw_time: str
    schedulable: int
    year: int | None
    area: str | None
    department: str | None

    def as_row(self) -> dict[str, object]:
        return {
            "semester": self.semester,
            "course_code": self.course_code,
            "category": self.category,
            "category_group": self.category_group,
            "name": self.name,
            "name_en": self.name_en,
            "credits": self.credits,
            "theory": self.theory,
            "practice": self.practice,
            "raw_time": self.raw_time,
            "schedulable": self.schedulable,
            "year": self.year,
            "area": self.area,
            "department": self.department,
        }


@dataclass
class ConvertResult:
    rows: list[dict[str, object]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duplicates_removed: int = 0
    source_rows: int = 0
    expanded_rows: int = 0

    def summary_lines(self) -> list[str]:
        by_semester = {1: 0, 2: 0}
        unschedulable: list[str] = []
        for row in self.rows:
            by_semester[int(row["semester"])] += 1
            if int(row["schedulable"]) == 0:
                unschedulable.append(
                    f"  {row['semester']}학기 {row['course_code']} {row['name']}"
                )
        lines = [
            f"원본 행: {self.source_rows}",
            f"학기 분리 후: {self.expanded_rows}",
            f"중복 제거: {self.duplicates_removed}",
            f"출력 행: {len(self.rows)}",
            f"1학기: {by_semester[1]}",
            f"2학기: {by_semester[2]}",
            "시간 없음:",
        ]
        lines.extend(unschedulable or ["  (없음)"])
        if self.errors:
            lines.append("오류:")
            lines.extend(f"  {error}" for error in self.errors)
        else:
            lines.append("파싱 실패: (없음)")
        return lines


def load_categories(path: Path | None = None) -> dict[str, str]:
    if path is None:
        path = ROOT / "config" / "categories.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError("categories.json은 비어 있지 않은 객체여야 합니다.")
    return {str(key): str(value) for key, value in data.items()}


def convert_text(text: str, categories: dict[str, str]) -> ConvertResult:
    drafts, source_rows, errors = _read_drafts(text, categories)
    merged, removed, merge_errors = _merge_drafts(drafts)
    errors.extend(merge_errors)
    rows = [draft.as_row() for draft in merged]
    rows.sort(key=lambda row: (int(row["semester"]), str(row["course_code"]), str(row["name"])))
    return ConvertResult(
        rows=rows,
        errors=errors,
        duplicates_removed=removed,
        source_rows=source_rows,
        expanded_rows=len(drafts),
    )


def catalog_rows(
    rows: list[dict[str, object]],
    semester: int = CATALOG_SEMESTER,
) -> list[dict[str, object]]:
    """솔버가 쓰는 카탈로그. 현재는 1학기 행만 남긴다."""
    return [row for row in rows if int(row["semester"]) == semester]


def write_csv(rows: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row.get(column)) for column in CSV_COLUMNS})


def _read_drafts(
    text: str, categories: dict[str, str]
) -> tuple[list[Draft], int, list[str]]:
    drafts: list[Draft] = []
    errors: list[str] = []
    source_rows = 0
    current_year: int | None = None
    current_area: str | None = None
    header: list[str] | None = None
    expecting_separator = False

    def close_table() -> None:
        nonlocal header, expecting_separator
        header = None
        expecting_separator = False

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if line.startswith("## "):
            close_table()
            current_year = None
            current_area = None
            continue
        year_match = re.fullmatch(r"###\s+(\d)학년", line)
        if year_match:
            close_table()
            current_year = int(year_match.group(1))
            current_area = None
            continue
        area_match = re.fullmatch(r"###\s+(.+)", line)
        if area_match:
            close_table()
            current_area = _clean_area(area_match.group(1))
            current_year = None
            continue
        if not line.startswith("|"):
            close_table()
            continue

        cells = _split_row(line)
        if expecting_separator:
            expecting_separator = False
            if not _is_separator(cells):
                errors.append(f"{line_number}행: 표 헤더 구분선이 아닙니다.")
                header = None
            continue
        if header is None:
            header = cells
            expecting_separator = True
            missing = [column for column in REQUIRED_COLUMNS if column not in header]
            if missing:
                errors.append(
                    f"{line_number}행: 표에 열이 없습니다 ({', '.join(missing)})."
                )
                header = None
                expecting_separator = False
            continue

        source_rows += 1
        if len(cells) != len(header):
            errors.append(
                f"{line_number}행: 열 개수가 헤더와 다릅니다 ({len(cells)} != {len(header)})."
            )
            continue
        record = dict(zip(header, cells))
        drafts.extend(
            _drafts_from_record(
                record,
                line_number,
                categories,
                current_year,
                current_area,
                errors,
            )
        )
    return drafts, source_rows, errors


def _drafts_from_record(
    record: dict[str, str],
    line_number: int,
    categories: dict[str, str],
    current_year: int | None,
    current_area: str | None,
    errors: list[str],
) -> list[Draft]:
    semesters = _parse_semesters(record.get("학기", ""))
    if semesters is None:
        errors.append(f"{line_number}행: 학기를 해석할 수 없습니다 ({record.get('학기', '')!r}).")
        return []

    category = record.get("구분", "").strip()
    category_group = categories.get(category)
    if category_group is None:
        errors.append(f"{line_number}행: 알 수 없는 구분 {category!r}.")
        return []

    course_code = record.get("학수번호", "").strip()
    if not course_code:
        errors.append(f"{line_number}행: 학수번호가 비어 있습니다.")
        return []

    name, name_en, name_error = _split_name(record.get("교과목명", ""))
    if name_error:
        errors.append(f"{line_number}행: {name_error}")
        return []

    credits = _parse_whole_number(record.get("학점", ""))
    if credits is None:
        errors.append(f"{line_number}행: 학점이 정수가 아닙니다 ({record.get('학점', '')!r}).")
        return []

    theory = _optional_whole_number(record.get("이론"), line_number, "이론", errors)
    practice = _optional_whole_number(record.get("실습"), line_number, "실습", errors)
    if theory is _INVALID or practice is _INVALID:
        return []

    raw_time = record.get("강의시간", "").strip()
    slots, parse_error = parse_time(raw_time)
    if parse_error:
        errors.append(f"{line_number}행: 파싱 실패 {course_code} {raw_time!r} ({parse_error})")
        return []

    area = _blank(record.get("영역")) or current_area
    department = _blank(record.get("주관학과"))
    drafts: list[Draft] = []
    for semester in semesters:
        drafts.append(
            Draft(
                line=line_number,
                semester=semester,
                course_code=course_code,
                category=category,
                category_group=category_group,
                name=name,
                name_en=name_en,
                credits=credits,
                theory=None if theory is _INVALID else theory,
                practice=None if practice is _INVALID else practice,
                raw_time=raw_time,
                schedulable=1 if slots else 0,
                year=current_year,
                area=area,
                department=department,
            )
        )
    return drafts


def _merge_drafts(drafts: list[Draft]) -> tuple[list[Draft], int, list[str]]:
    merged: dict[tuple[str, int], Draft] = {}
    removed = 0
    errors: list[str] = []
    for draft in drafts:
        key = (draft.course_code, draft.semester)
        current = merged.get(key)
        if current is None:
            merged[key] = draft
            continue
        conflict = _identity_conflict(current, draft)
        if conflict:
            errors.append(
                f"{draft.course_code} {draft.semester}학기: {conflict} "
                f"({current.line}행, {draft.line}행)"
            )
            continue
        merged[key] = _fill_blanks(current, draft)
        removed += 1
    return list(merged.values()), removed, errors


def _identity_conflict(current: Draft, incoming: Draft) -> str | None:
    for field_name in IDENTITY_FIELDS:
        left = getattr(current, field_name)
        right = getattr(incoming, field_name)
        if field_name == "name_en" and (left is None or right is None):
            continue
        if left != right:
            label = IDENTITY_LABELS[field_name]
            return f"{label}이 다릅니다 ({left!r} != {right!r})"
    return None


def _fill_blanks(current: Draft, incoming: Draft) -> Draft:
    values = current.as_row()
    values["line"] = current.line
    incoming_values = incoming.as_row()
    for field_name in FILL_FIELDS:
        if values[field_name] in (None, "") and incoming_values[field_name] not in (None, ""):
            values[field_name] = incoming_values[field_name]
    values.pop("line", None)
    return Draft(line=current.line, **values)  # type: ignore[arg-type]


def _split_row(line: str) -> list[str]:
    parts = line.split("|")
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [part.strip() for part in parts]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _clean_area(title: str) -> str:
    without_note = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
    without_number = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", without_note).strip()
    return re.sub(r"\s*영역$", "", without_number).strip()


def _parse_semesters(text: str) -> list[int] | None:
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if not parts or any(part not in {"1", "2"} for part in parts):
        return None
    semesters: list[int] = []
    for part in parts:
        semester = int(part)
        if semester not in semesters:
            semesters.append(semester)
    return semesters


def _split_name(raw: str) -> tuple[str, str | None, str | None]:
    text = raw.strip()
    if not text:
        return "", None, "교과목명이 비어 있습니다."
    if " / " not in text:
        return text, None, None
    name, name_en = text.split(" / ", 1)
    name = name.strip()
    name_en = name_en.strip()
    if not name:
        return "", None, "국문 과목명이 비어 있습니다."
    return name, name_en or None, None


_INVALID = object()


def _optional_whole_number(
    raw: str | None,
    line_number: int,
    label: str,
    errors: list[str],
) -> int | None | object:
    if raw is None:
        return None
    text = raw.strip()
    if text == "" or text == "-":
        return None
    value = _parse_whole_number(text)
    if value is None:
        errors.append(f"{line_number}행: {label}이 정수가 아닙니다 ({text!r}).")
        return _INVALID
    return value


def _parse_whole_number(raw: str) -> int | None:
    text = raw.strip()
    if not text or text == "-":
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if value != value.to_integral_value():
        return None
    return int(value)


def _blank(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if text == "" or text == "-":
        return None
    return text


def _csv_value(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="마크다운 강의표를 courses.csv로 변환합니다.")
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "data" / "데이터베이스.md",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "courses.csv",
    )
    parser.add_argument(
        "--categories",
        type=Path,
        default=ROOT / "config" / "categories.json",
    )
    args = parser.parse_args(argv)

    categories = load_categories(args.categories)
    text = args.input.read_text(encoding="utf-8")
    result = convert_text(text, categories)
    for line in result.summary_lines():
        print(line)
    if result.errors:
        return 1
    catalog = catalog_rows(result.rows)
    print(f"카탈로그({CATALOG_SEMESTER}학기): {len(catalog)}")
    print(f"제외: {len(result.rows) - len(catalog)}")
    write_csv(catalog, args.output)
    print(f"저장: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
