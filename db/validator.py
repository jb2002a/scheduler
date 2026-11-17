"""제안된 시간표를 카탈로그와 조건에 대해 결정적으로 판정한다."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Mapping, Sequence

from db.time_parser import DAY_CODES, parse_time

Slot = tuple[str, int]


@dataclass(frozen=True)
class CatalogCourse:
    id: int
    semester: int
    course_code: str
    category: str
    category_group: str
    name: str
    name_en: str | None
    credits: int
    raw_time: str
    schedulable: bool
    slots: tuple[Slot, ...]


@dataclass(frozen=True)
class Finding:
    code: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    findings: tuple[Finding, ...]

    @property
    def ok(self) -> bool:
        return not self.findings


def load_catalog(connection: sqlite3.Connection) -> list[CatalogCourse]:
    courses = connection.execute(
        """
        SELECT id, semester, course_code, category, category_group, name, name_en,
               credits, raw_time, schedulable
        FROM courses
        ORDER BY id
        """
    ).fetchall()
    slot_rows = connection.execute(
        """
        SELECT course_id, day, period
        FROM course_slots
        ORDER BY course_id, id
        """
    ).fetchall()
    slots_by_course: dict[int, list[Slot]] = {}
    for course_id, day, period in slot_rows:
        slots_by_course.setdefault(course_id, []).append((day, period))
    return [
        CatalogCourse(
            id=row[0],
            semester=row[1],
            course_code=row[2],
            category=row[3],
            category_group=row[4],
            name=row[5],
            name_en=row[6],
            credits=row[7],
            raw_time=row[8],
            schedulable=bool(row[9]),
            slots=tuple(slots_by_course.get(row[0], [])),
        )
        for row in courses
    ]


def validate(
    catalog: Sequence[CatalogCourse],
    selected: Sequence[Mapping],
    constraints: Sequence[Mapping] | None = None,
    total_credits: int | None = None,
    semester: int | None = None,
) -> ValidationResult:
    """E1~E6을 판정한다.

    선택된 과목은 ``id`` 또는 ``name``으로 지정한다.
    ``credits``와 ``time``이 있으면 카탈로그와 비교해 E2를 낸다.
    시간 충돌과 조건은 식별된 카탈로그 과목 기준으로 본다.
    """
    findings: list[Finding] = []
    resolved = _resolve_selection(catalog, selected, semester, findings)
    unique: list[CatalogCourse] = []
    seen: set[int] = set()
    for course in resolved:
        if course is None:
            continue
        if course.id in seen:
            findings.append(Finding("E4", f"과목 중복: {course.name} ({course.course_code})"))
            continue
        seen.add(course.id)
        unique.append(course)

    findings.extend(_time_conflicts(unique))
    findings.extend(_constraint_findings(catalog, unique, constraints or [], semester))
    credit_finding = _credit_sum_finding(selected, resolved, total_credits)
    if credit_finding is not None:
        findings.append(credit_finding)
    return ValidationResult(tuple(findings))


def _resolve_selection(
    catalog: Sequence[CatalogCourse],
    selected: Sequence[Mapping],
    semester: int | None,
    findings: list[Finding],
) -> list[CatalogCourse | None]:
    by_id = {course.id: course for course in catalog}
    resolved: list[CatalogCourse | None] = []
    for item in selected:
        if item.get("id") is not None:
            resolved.append(_resolve_by_id(by_id, item, semester, findings))
        else:
            resolved.append(_resolve_by_name(catalog, item, semester, findings))
    return resolved


def _resolve_by_id(
    by_id: Mapping[int, CatalogCourse],
    item: Mapping,
    semester: int | None,
    findings: list[Finding],
) -> CatalogCourse | None:
    course_id = item.get("id")
    course = by_id.get(course_id)
    label = str(item.get("name") or course_id)
    if course is None or (semester is not None and course.semester != semester):
        findings.append(Finding("E1", f"존재하지 않는 과목: {label}"))
        return None
    _check_attributes(course, item, findings)
    return course


def _resolve_by_name(
    catalog: Sequence[CatalogCourse],
    item: Mapping,
    semester: int | None,
    findings: list[Finding],
) -> CatalogCourse | None:
    name = str(item.get("name") or "").strip()
    if not name:
        findings.append(Finding("E1", "존재하지 않는 과목: (이름 없음)"))
        return None
    candidates = [
        course
        for course in catalog
        if course.name == name and (semester is None or course.semester == semester)
    ]
    if not candidates:
        findings.append(Finding("E1", f"존재하지 않는 과목: {name}"))
        return None

    claimed_slots = _claimed_slots(item)
    if claimed_slots == "invalid":
        findings.append(Finding("E2", f"속성 오기재: {name}의 시간을 파싱할 수 없습니다."))
        if len(candidates) == 1:
            _check_credits(candidates[0], item, findings)
            return candidates[0]
        return None

    if claimed_slots is not None:
        matched = [course for course in candidates if _slot_key(course.slots) == claimed_slots]
        if len(matched) == 1:
            chosen = matched[0]
        elif len(candidates) == 1:
            chosen = candidates[0]
            findings.append(Finding("E2", f"속성 오기재: {name}의 시간이 데이터와 다릅니다."))
        else:
            findings.append(Finding("E2", f"속성 오기재: {name}의 시간과 일치하는 과목을 정할 수 없습니다."))
            return None
    elif len(candidates) == 1:
        chosen = candidates[0]
    else:
        findings.append(Finding("E2", f"속성 오기재: {name}이 여러 과목과 일치합니다."))
        return None

    _check_credits(chosen, item, findings)
    return chosen


def _check_attributes(course: CatalogCourse, item: Mapping, findings: list[Finding]) -> None:
    _check_credits(course, item, findings)
    claimed_slots = _claimed_slots(item)
    if claimed_slots == "invalid":
        findings.append(Finding("E2", f"속성 오기재: {course.name}의 시간을 파싱할 수 없습니다."))
    elif claimed_slots is not None and claimed_slots != _slot_key(course.slots):
        findings.append(Finding("E2", f"속성 오기재: {course.name}의 시간이 데이터와 다릅니다."))


def _check_credits(course: CatalogCourse, item: Mapping, findings: list[Finding]) -> None:
    if "credits" not in item or item.get("credits") is None:
        return
    claimed = _as_int(item.get("credits"))
    if claimed is None or claimed != course.credits:
        findings.append(
            Finding(
                "E2",
                f"속성 오기재: {course.name}의 학점이 데이터와 다릅니다.",
            )
        )


def _claimed_slots(item: Mapping) -> tuple[Slot, ...] | None | str:
    if "time" not in item or item.get("time") is None:
        return None
    slots, error = parse_time(str(item.get("time")))
    if error:
        return "invalid"
    return _slot_key(slots)


def _slot_key(slots: Sequence[Slot]) -> tuple[Slot, ...]:
    return tuple(sorted(slots))


def _time_conflicts(courses: Sequence[CatalogCourse]) -> list[Finding]:
    occupants: dict[Slot, list[str]] = {}
    for course in courses:
        for slot in course.slots:
            occupants.setdefault(slot, []).append(course.name)
    findings: list[Finding] = []
    for slot, names in sorted(occupants.items()):
        if len(names) > 1:
            day, period = slot
            joined = ", ".join(names)
            findings.append(Finding("E3", f"시간 충돌: {day} {period}교시 ({joined})"))
    return findings


def _constraint_findings(
    catalog: Sequence[CatalogCourse],
    courses: Sequence[CatalogCourse],
    constraints: Sequence[Mapping],
    semester: int | None,
) -> list[Finding]:
    known_categories = {course.category for course in catalog}
    known_groups = {course.category_group for course in catalog}
    if semester is not None:
        known_categories = {course.category for course in catalog if course.semester == semester}
        known_groups = {course.category_group for course in catalog if course.semester == semester}
    selected_ids = {course.id for course in courses}
    findings: list[Finding] = []
    for constraint in constraints:
        constraint_type = constraint.get("type")
        params = constraint.get("params") or {}
        label = constraint.get("id") or constraint_type
        if constraint_type == "free_slots":
            findings.extend(_free_slot_findings(courses, params, label))
        elif constraint_type == "course":
            findings.extend(_course_findings(catalog, selected_ids, params, label, semester))
        elif constraint_type == "credits":
            findings.extend(
                _credits_findings(courses, params, label, known_categories, known_groups)
            )
        elif constraint_type == "limit":
            findings.extend(_limit_findings(courses, params, label))
        else:
            raise ValueError(f"알 수 없는 조건 타입: {constraint_type}")
    return findings


def _free_slot_findings(
    courses: Sequence[CatalogCourse], params: Mapping, label: object
) -> list[Finding]:
    days = params.get("days")
    periods = params.get("periods")
    if days is not None:
        unknown = [day for day in days if day not in DAY_CODES]
        if unknown:
            raise ValueError(f"알 수 없는 요일: {unknown}")
    if periods is not None:
        invalid = [period for period in periods if not isinstance(period, int) or not 0 <= period <= 10]
        if invalid:
            raise ValueError(f"알 수 없는 교시: {invalid}")
    day_set = set(days) if days is not None else set(DAY_CODES)
    period_set = set(periods) if periods is not None else set(range(11))
    blocked = {
        course.name
        for course in courses
        for day, period in course.slots
        if day in day_set and period in period_set
    }
    if not blocked:
        return []
    names = ", ".join(sorted(blocked))
    return [Finding("E5", f"조건 위반({label}): 비워야 하는 시간에 수업이 있습니다 ({names})")]


def _course_findings(
    catalog: Sequence[CatalogCourse],
    selected_ids: set[int],
    params: Mapping,
    label: object,
    semester: int | None,
) -> list[Finding]:
    course_id = params.get("course_id")
    mode = params.get("mode")
    if mode not in {"require", "exclude"}:
        raise ValueError(f"course.mode가 올바르지 않습니다: {mode}")
    matched = [
        course
        for course in catalog
        if course.id == course_id and (semester is None or course.semester == semester)
    ]
    if not matched:
        return [Finding("E5", f"조건 위반({label}): 존재하지 않는 course_id {course_id}")]
    course = matched[0]
    present = course.id in selected_ids
    if mode == "require" and not present:
        return [Finding("E5", f"조건 위반({label}): {course.name}이 포함되어야 합니다.")]
    if mode == "exclude" and present:
        return [Finding("E5", f"조건 위반({label}): {course.name}이 제외되어야 합니다.")]
    return []


def _credits_findings(
    courses: Sequence[CatalogCourse],
    params: Mapping,
    label: object,
    known_categories: set[str],
    known_groups: set[str],
) -> list[Finding]:
    category = params.get("category")
    minimum = params.get("min")
    maximum = params.get("max")
    if minimum is None and maximum is None:
        raise ValueError("credits 조건에 min 또는 max가 필요합니다.")
    if category is not None and category not in known_categories and category not in known_groups:
        raise ValueError(f"알 수 없는 구분: {category}")
    total = sum(_credits_of(course, category) for course in courses)
    findings: list[Finding] = []
    scope = category or "총학점"
    if minimum is not None and total < minimum:
        findings.append(Finding("E5", f"조건 위반({label}): {scope} {total}학점이 최소 {minimum}보다 작습니다."))
    if maximum is not None and total > maximum:
        findings.append(Finding("E5", f"조건 위반({label}): {scope} {total}학점이 최대 {maximum}보다 큽니다."))
    return findings


def _credits_of(course: CatalogCourse, category: str | None) -> int:
    if category is None:
        return course.credits
    if category in {"전공", "교양"}:
        return course.credits if course.category_group == category else 0
    return course.credits if course.category == category else 0


def _limit_findings(
    courses: Sequence[CatalogCourse], params: Mapping, label: object
) -> list[Finding]:
    kind = params.get("kind")
    count = params.get("count")
    if kind not in {"days", "per_day", "consecutive"}:
        raise ValueError(f"limit.kind가 올바르지 않습니다: {kind}")
    if not isinstance(count, int) or count < 0:
        raise ValueError(f"limit.count가 올바르지 않습니다: {count}")

    if kind == "days":
        used = {day for course in courses for day, _period in course.slots}
        if len(used) > count:
            return [Finding("E5", f"조건 위반({label}): 등교 요일 {len(used)}일이 상한 {count}일을 넘습니다.")]
        return []

    if kind == "per_day":
        by_day: dict[str, set[int]] = {}
        for course in courses:
            for day, _period in course.slots:
                by_day.setdefault(day, set()).add(course.id)
        over = sorted(day for day, course_ids in by_day.items() if len(course_ids) > count)
        if over:
            days = ", ".join(over)
            return [Finding("E5", f"조건 위반({label}): 하루 과목 수 상한 {count}을 넘습니다 ({days})")]
        return []

    over_days = []
    for day in DAY_CODES:
        periods = {period for course in courses for slot_day, period in course.slots if slot_day == day}
        if _max_consecutive(periods) > count:
            over_days.append(day)
    if over_days:
        days = ", ".join(over_days)
        return [Finding("E5", f"조건 위반({label}): 연속 교시가 {count}을 넘습니다 ({days})")]
    return []


def _max_consecutive(periods: set[int]) -> int:
    if not periods:
        return 0
    ordered = sorted(periods)
    best = run = 1
    for prev, cur in zip(ordered, ordered[1:]):
        if cur == prev + 1:
            run += 1
            best = max(best, run)
        else:
            run = 1
    return best


def _credit_sum_finding(
    selected: Sequence[Mapping],
    resolved: Sequence[CatalogCourse | None],
    total_credits: int | None,
) -> Finding | None:
    if total_credits is None:
        return None
    stated = 0
    for item, course in zip(selected, resolved):
        if item.get("credits") is not None:
            claimed = _as_int(item.get("credits"))
            if claimed is None:
                return Finding("E6", "학점 계산 오류: 과목 학점을 정수로 읽을 수 없습니다.")
            stated += claimed
        elif course is not None:
            stated += course.credits
        else:
            return None
    if stated != total_credits:
        return Finding("E6", f"학점 계산 오류: 합계는 {stated}인데 total_credits는 {total_credits}입니다.")
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None
