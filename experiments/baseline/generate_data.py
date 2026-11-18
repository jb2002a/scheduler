"""시드 고정 100/300/500 카탈로그를 만들고 솔버로 가능 여부를 확인한다."""

from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.time_parser import parse_time
from db.validator import CatalogCourse
from solver.solve import solve

DAY_CHARS = ("월", "화", "수", "목", "금")
REQUIRED_NAMES = ("데이터구조", "영어회화")
SIZES = (100, 300, 500)
DEFAULT_SEED = 42
DATA_DIR = Path(__file__).with_name("data")


def load_source_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    schedulable = [row for row in rows if int(row["schedulable"]) == 1]
    if not schedulable:
        raise ValueError("시간 있는 과목이 없습니다.")
    missing = [name for name in REQUIRED_NAMES if not any(row["name"] == name for row in schedulable)]
    if missing:
        raise ValueError(f"필수 과목이 없습니다: {missing}")
    return schedulable


def row_to_course(row: dict[str, str], course_id: int) -> CatalogCourse:
    slots, error = parse_time(row["raw_time"])
    if error:
        raise ValueError(f"시간 파싱 실패: {row['name']} {row['raw_time']!r} ({error})")
    return CatalogCourse(
        id=course_id,
        semester=int(row["semester"]),
        course_code=row["course_code"],
        category=row["category"],
        category_group=row["category_group"],
        name=row["name"],
        name_en=row.get("name_en") or None,
        credits=int(row["credits"]),
        raw_time=row["raw_time"],
        schedulable=True,
        slots=tuple(slots),
    )


def build_catalog(
    source: list[dict[str, str]],
    size: int,
    seed: int = DEFAULT_SEED,
) -> list[CatalogCourse]:
    if size < len(REQUIRED_NAMES):
        raise ValueError(f"size는 {len(REQUIRED_NAMES)} 이상이어야 합니다.")

    rng = random.Random(seed + size)
    required = [_find_row(source, name) for name in REQUIRED_NAMES]
    pool = [row for row in source if row["name"] not in REQUIRED_NAMES]
    used_names = set(REQUIRED_NAMES)
    rows: list[dict[str, str]] = [dict(row) for row in required]

    if size <= len(source):
        chosen = rng.sample(pool, size - len(REQUIRED_NAMES))
        rows.extend(dict(row) for row in chosen)
    else:
        rows.extend(dict(row) for row in pool)
        while len(rows) < size:
            base = rng.choice(source)
            rows.append(_mutate_row(base, rng, used_names, len(rows) + 1))

    rng.shuffle(rows)
    # Keep required courses present after shuffle; IDs are assigned after order is fixed.
    return [row_to_course(row, index) for index, row in enumerate(rows, start=1)]


def _find_row(source: list[dict[str, str]], name: str) -> dict[str, str]:
    for row in source:
        if row["name"] == name:
            return row
    raise ValueError(f"과목을 찾을 수 없습니다: {name}")


def _mutate_row(
    base: dict[str, str],
    rng: random.Random,
    used_names: set[str],
    serial: int,
) -> dict[str, str]:
    row = dict(base)
    name = f"{base['name']}_변형{serial}"
    while name in used_names:
        serial += 1
        name = f"{base['name']}_변형{serial}"
    used_names.add(name)
    row["name"] = name
    row["name_en"] = ""
    row["course_code"] = f"X{serial:05d}"
    row["raw_time"] = _random_time(rng)
    row["schedulable"] = "1"
    return row


def _random_time(rng: random.Random) -> str:
    day_count = rng.randint(1, 3)
    days = rng.sample(DAY_CHARS, day_count)
    # Preserve weekday order as in real data strings.
    days.sort(key=DAY_CHARS.index)
    parts: list[str] = []
    for day in days:
        period_count = rng.randint(1, 3)
        start = rng.randint(1, 8)
        periods = list(range(start, start + period_count))
        parts.append(day + "".join(str(period) for period in periods))
    text = "".join(parts)
    slots, error = parse_time(text)
    if error or not slots:
        raise RuntimeError(f"생성한 시간이 올바르지 않습니다: {text}")
    return text


def prompt_csv(courses: list[CatalogCourse]) -> str:
    lines = ["구분,과목명,학점,시간"]
    for course in courses:
        lines.append(
            f"{course.category_group},{course.name},{course.credits},{course.raw_time}"
        )
    return "\n".join(lines) + "\n"


def write_catalog_csv(courses: list[CatalogCourse], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id",
        "semester",
        "course_code",
        "category",
        "category_group",
        "name",
        "name_en",
        "credits",
        "raw_time",
        "schedulable",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for course in courses:
            writer.writerow(
                {
                    "id": course.id,
                    "semester": course.semester,
                    "course_code": course.course_code,
                    "category": course.category,
                    "category_group": course.category_group,
                    "name": course.name,
                    "name_en": course.name_en or "",
                    "credits": course.credits,
                    "raw_time": course.raw_time,
                    "schedulable": 1 if course.schedulable else 0,
                }
            )


def load_catalog_csv(path: Path) -> list[CatalogCourse]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    courses: list[CatalogCourse] = []
    for row in rows:
        slots, error = parse_time(row["raw_time"])
        if error:
            raise ValueError(f"시간 파싱 실패: {row['name']} {row['raw_time']!r} ({error})")
        courses.append(
            CatalogCourse(
                id=int(row["id"]),
                semester=int(row["semester"]),
                course_code=row["course_code"],
                category=row["category"],
                category_group=row["category_group"],
                name=row["name"],
                name_en=row.get("name_en") or None,
                credits=int(row["credits"]),
                raw_time=row["raw_time"],
                schedulable=bool(int(row["schedulable"])),
                slots=tuple(slots),
            )
        )
    return courses


def course_id_by_name(courses: list[CatalogCourse], name: str) -> int:
    matched = [course for course in courses if course.name == name]
    if len(matched) != 1:
        raise ValueError(f"과목명 매칭 실패: {name} ({len(matched)}개)")
    return matched[0].id


def feasibility_constraints(courses: list[CatalogCourse]) -> dict[str, list[dict]]:
    data_structure_id = course_id_by_name(courses, "데이터구조")
    english_id = course_id_by_name(courses, "영어회화")
    return {
        "S1": [{"type": "credits", "params": {"min": 18, "max": 18}}],
        "S2": [
            {"type": "credits", "params": {"min": 18, "max": 18}},
            {"type": "free_slots", "params": {"days": ["WED"]}},
        ],
        "S3": [
            {"type": "credits", "params": {"min": 18, "max": 18}},
            {"type": "free_slots", "params": {"periods": [0, 1]}},
        ],
        "S4": [
            {"type": "credits", "params": {"min": 18, "max": 18}},
            {"type": "free_slots", "params": {"periods": [4]}},
        ],
        "S5": [
            {"type": "credits", "params": {"min": 18, "max": 18}},
            {"type": "free_slots", "params": {"days": ["WED"]}},
            {"type": "free_slots", "params": {"periods": [4]}},
            {"type": "limit", "params": {"kind": "days", "count": 3}},
        ],
        "S6": [
            {"type": "credits", "params": {"min": 18, "max": 18}},
            {
                "type": "course",
                "params": {"course_id": data_structure_id, "mode": "require"},
            },
            {
                "type": "course",
                "params": {"course_id": english_id, "mode": "exclude"},
            },
        ],
        "S7": [
            {"type": "credits", "params": {"min": 18, "max": 18}},
            {"type": "credits", "params": {"category": "전공", "min": 12}},
        ],
        "S8": [
            {"type": "credits", "params": {"min": 18, "max": 18}},
            {
                "type": "free_slots",
                "params": {
                    "days": ["MON", "TUE", "WED", "THU", "FRI"],
                    "periods": list(range(11)),
                },
            },
        ],
        "S9": [
            {"type": "credits", "params": {"min": 18, "max": 18}},
            {"type": "free_slots", "params": {"days": ["WED"]}},
            {"type": "free_slots", "params": {"periods": [0, 1]}},
        ],
        "S10": [
            {"type": "credits", "params": {"min": 18, "max": 18}},
            {"type": "free_slots", "params": {"periods": [0, 1]}},
        ],
    }


def verify_feasibility(courses: list[CatalogCourse]) -> dict[str, bool]:
    expected = {
        "S1": True,
        "S2": True,
        "S3": True,
        "S4": True,
        "S5": True,
        "S6": True,
        "S7": True,
        "S8": False,
        "S9": True,
        "S10": True,
    }
    results: dict[str, bool] = {}
    constraints = feasibility_constraints(courses)
    for scenario_id, expected_ok in expected.items():
        status = solve(courses, constraints[scenario_id], n=1)["status"]
        ok = status == "ok"
        results[scenario_id] = ok
        if ok != expected_ok:
            raise RuntimeError(
                f"{scenario_id} 가능 여부가 예상과 다릅니다: status={status}, expected_ok={expected_ok}"
            )
    return results


def generate_all(
    source_csv: Path,
    output_dir: Path,
    seed: int = DEFAULT_SEED,
    sizes: tuple[int, ...] = SIZES,
) -> dict[int, list[CatalogCourse]]:
    source = load_source_rows(source_csv)
    catalogs: dict[int, list[CatalogCourse]] = {}
    for size in sizes:
        courses = None
        last_error: Exception | None = None
        for attempt in range(20):
            try:
                courses = build_catalog(source, size, seed=seed + attempt * 1000)
                verify_feasibility(courses)
                break
            except RuntimeError as error:
                last_error = error
                courses = None
        if courses is None:
            raise RuntimeError(f"size={size} 카탈로그를 만들지 못했습니다: {last_error}")
        catalogs[size] = courses
        write_catalog_csv(courses, output_dir / f"courses_{size}.csv")
        (output_dir / f"prompt_{size}.csv").write_text(prompt_csv(courses), encoding="utf-8")
    return catalogs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="베이스라인 실험용 카탈로그를 생성합니다.")
    parser.add_argument("--csv", type=Path, default=ROOT / "data" / "courses.csv")
    parser.add_argument("--out", type=Path, default=DATA_DIR)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    catalogs = generate_all(args.csv, args.out, seed=args.seed)
    for size, courses in catalogs.items():
        print(f"{size}: {len(courses)}과목 저장")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
