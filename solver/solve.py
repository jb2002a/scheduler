"""OR-Tools CP-SAT으로 조건 JSON을 시간표 후보로 바꾼다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from ortools.sat.python import cp_model

from db.time_parser import DAY_CODES
from db.validator import CatalogCourse

EARLY_PERIODS = frozenset({0, 1})
LATE_PERIODS = frozenset({9, 10})
SCORE_DAY_WEIGHT = 1_000_000
SCORE_EARLY_WEIGHT = 1_000


@dataclass(frozen=True)
class _NormalizedConstraint:
    id: str
    type: str
    params: Mapping
    source_text: str | None


def solve(
    catalog: Sequence[CatalogCourse],
    constraints: Sequence[Mapping] | None = None,
    n: int = 3,
    semester: int = 1,
) -> dict:
    """조건을 만족하는 시간표 후보 또는 완화안을 반환한다."""
    if n < 1:
        raise ValueError(f"n은 1 이상이어야 합니다: {n}")

    courses = [
        course
        for course in catalog
        if course.semester == semester and course.schedulable
    ]
    by_id = {course.id: course for course in courses}
    known_categories = {course.category for course in courses}
    known_groups = {course.category_group for course in courses}
    normalized = _normalize_constraints(
        constraints or [],
        by_id,
        known_categories,
        known_groups,
    )
    constraint_dicts = [_constraint_dict(item) for item in normalized]

    candidates = _search_candidates(courses, by_id, normalized, n)
    if candidates is not None:
        return {
            "status": "ok",
            "constraints": constraint_dicts,
            "candidates": candidates,
        }

    return {
        "status": "infeasible",
        "constraints": constraint_dicts,
        "candidates": [],
        "relaxations": _find_relaxations(courses, by_id, normalized),
    }


def _constraint_dict(item: _NormalizedConstraint) -> dict:
    payload = {"id": item.id, "type": item.type, "params": dict(item.params)}
    if item.source_text is not None:
        payload["source_text"] = item.source_text
    return payload


def _normalize_constraints(
    constraints: Sequence[Mapping],
    by_id: Mapping[int, CatalogCourse],
    known_categories: set[str],
    known_groups: set[str],
) -> list[_NormalizedConstraint]:
    normalized: list[_NormalizedConstraint] = []
    used_ids: set[str] = set()
    next_index = 1
    for raw in constraints:
        constraint_type = raw.get("type")
        params = dict(raw.get("params") or {})
        _validate_constraint(constraint_type, params, by_id, known_categories, known_groups)
        constraint_id = raw.get("id")
        if constraint_id is None:
            while f"c{next_index}" in used_ids:
                next_index += 1
            constraint_id = f"c{next_index}"
            next_index += 1
        constraint_id = str(constraint_id)
        if constraint_id in used_ids:
            raise ValueError(f"조건 id가 중복됩니다: {constraint_id}")
        used_ids.add(constraint_id)
        source_text = raw.get("source_text")
        normalized.append(
            _NormalizedConstraint(
                id=constraint_id,
                type=str(constraint_type),
                params=params,
                source_text=None if source_text is None else str(source_text),
            )
        )
    return normalized


def _validate_constraint(
    constraint_type: object,
    params: Mapping,
    by_id: Mapping[int, CatalogCourse],
    known_categories: set[str],
    known_groups: set[str],
) -> None:
    if constraint_type == "free_slots":
        days = params.get("days")
        periods = params.get("periods")
        if days is not None:
            unknown = [day for day in days if day not in DAY_CODES]
            if unknown:
                raise ValueError(f"알 수 없는 요일: {unknown}")
        if periods is not None:
            invalid = [
                period for period in periods if not isinstance(period, int) or not 0 <= period <= 10
            ]
            if invalid:
                raise ValueError(f"알 수 없는 교시: {invalid}")
        return

    if constraint_type == "course":
        course_id = params.get("course_id")
        mode = params.get("mode")
        if mode not in {"require", "exclude"}:
            raise ValueError(f"course.mode가 올바르지 않습니다: {mode}")
        if course_id not in by_id:
            raise ValueError(f"이번 학기 후보에 없는 course_id: {course_id}")
        return

    if constraint_type == "credits":
        category = params.get("category")
        minimum = params.get("min")
        maximum = params.get("max")
        if minimum is None and maximum is None:
            raise ValueError("credits 조건에 min 또는 max가 필요합니다.")
        if category is not None and category not in known_categories and category not in known_groups:
            raise ValueError(f"알 수 없는 구분: {category}")
        return

    if constraint_type == "limit":
        kind = params.get("kind")
        count = params.get("count")
        if kind not in {"days", "per_day", "consecutive"}:
            raise ValueError(f"limit.kind가 올바르지 않습니다: {kind}")
        if not isinstance(count, int) or count < 0:
            raise ValueError(f"limit.count가 올바르지 않습니다: {count}")
        return

    raise ValueError(f"알 수 없는 조건 타입: {constraint_type}")


def _search_candidates(
    courses: Sequence[CatalogCourse],
    by_id: Mapping[int, CatalogCourse],
    constraints: Sequence[_NormalizedConstraint],
    n: int,
) -> list[dict] | None:
    model, x, day_used, early_count, late_count = _build_model(courses, constraints)
    model.Minimize(
        day_used * SCORE_DAY_WEIGHT + early_count * SCORE_EARLY_WEIGHT + late_count
    )

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1

    candidates: list[dict] = []
    for _ in range(n):
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
        selected_ids = frozenset(course_id for course_id, var in x.items() if solver.Value(var) == 1)
        candidates.append(
            _candidate_from_ids(
                selected_ids,
                by_id,
                score=int(solver.ObjectiveValue()),
            )
        )
        _forbid_exact_selection(model, x, selected_ids)

    if not candidates and not _is_feasible(courses, constraints):
        return None
    return candidates


def _find_relaxations(
    courses: Sequence[CatalogCourse],
    by_id: Mapping[int, CatalogCourse],
    constraints: Sequence[_NormalizedConstraint],
) -> list[dict]:
    if not constraints:
        return []

    relaxations: list[dict] = []
    ids = [item.id for item in constraints]
    by_constraint_id = {item.id: item for item in constraints}

    for constraint_id in ids:
        remaining = [by_constraint_id[item_id] for item_id in ids if item_id != constraint_id]
        if _is_feasible(courses, remaining):
            relaxations.append({"drop": [constraint_id]})

    if relaxations:
        return relaxations

    for index, first in enumerate(ids):
        for second in ids[index + 1 :]:
            drop = {first, second}
            remaining = [by_constraint_id[item_id] for item_id in ids if item_id not in drop]
            if _is_feasible(courses, remaining):
                relaxations.append({"drop": [first, second]})
    return relaxations


def _is_feasible(
    courses: Sequence[CatalogCourse],
    constraints: Sequence[_NormalizedConstraint],
) -> bool:
    model, _x, _day_used, _early, _late = _build_model(courses, constraints)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    status = solver.Solve(model)
    return status in (cp_model.OPTIMAL, cp_model.FEASIBLE)


def _build_model(
    courses: Sequence[CatalogCourse],
    constraints: Sequence[_NormalizedConstraint],
) -> tuple[cp_model.CpModel, dict[int, cp_model.IntVar], cp_model.LinearExpr, cp_model.LinearExpr, cp_model.LinearExpr]:
    model = cp_model.CpModel()
    x = {
        course.id: model.NewBoolVar(f"x_{course.id}")
        for course in courses
    }

    slots_to_courses: dict[tuple[str, int], list[int]] = {}
    for course in courses:
        for slot in course.slots:
            slots_to_courses.setdefault(slot, []).append(course.id)
    for course_ids in slots_to_courses.values():
        if len(course_ids) > 1:
            model.Add(sum(x[course_id] for course_id in course_ids) <= 1)

    day_vars: dict[str, cp_model.IntVar] = {}
    for day in DAY_CODES:
        on_day = [course for course in courses if any(slot_day == day for slot_day, _ in course.slots)]
        day_var = model.NewBoolVar(f"day_{day}")
        day_vars[day] = day_var
        if not on_day:
            model.Add(day_var == 0)
            continue
        model.AddMaxEquality(day_var, [x[course.id] for course in on_day])

    early_flags: list[cp_model.IntVar] = []
    late_flags: list[cp_model.IntVar] = []
    for course in courses:
        early = any(period in EARLY_PERIODS for _day, period in course.slots)
        late = any(period in LATE_PERIODS for _day, period in course.slots)
        if early:
            early_flags.append(x[course.id])
        if late:
            late_flags.append(x[course.id])

    day_used = sum(day_vars.values()) if day_vars else 0
    early_count = sum(early_flags) if early_flags else 0
    late_count = sum(late_flags) if late_flags else 0

    for constraint in constraints:
        _apply_constraint(model, x, day_vars, courses, constraint)

    return model, x, day_used, early_count, late_count


def _apply_constraint(
    model: cp_model.CpModel,
    x: Mapping[int, cp_model.IntVar],
    day_vars: Mapping[str, cp_model.IntVar],
    courses: Sequence[CatalogCourse],
    constraint: _NormalizedConstraint,
) -> None:
    params = constraint.params
    if constraint.type == "free_slots":
        day_set = set(params["days"]) if params.get("days") is not None else set(DAY_CODES)
        period_set = (
            set(params["periods"]) if params.get("periods") is not None else set(range(11))
        )
        for course in courses:
            if any(day in day_set and period in period_set for day, period in course.slots):
                model.Add(x[course.id] == 0)
        return

    if constraint.type == "course":
        course_id = params["course_id"]
        if params["mode"] == "require":
            model.Add(x[course_id] == 1)
        else:
            model.Add(x[course_id] == 0)
        return

    if constraint.type == "credits":
        category = params.get("category")
        terms = [_credits_of(course, category) * x[course.id] for course in courses]
        total = sum(terms) if terms else 0
        if params.get("min") is not None:
            model.Add(total >= int(params["min"]))
        if params.get("max") is not None:
            model.Add(total <= int(params["max"]))
        return

    if constraint.type == "limit":
        kind = params["kind"]
        count = int(params["count"])
        if kind == "days":
            model.Add(sum(day_vars.values()) <= count)
            return
        if kind == "per_day":
            for day in DAY_CODES:
                on_day = [
                    x[course.id]
                    for course in courses
                    if any(slot_day == day for slot_day, _ in course.slots)
                ]
                if on_day:
                    model.Add(sum(on_day) <= count)
            return

        # consecutive: no window of count+1 consecutive occupied periods
        window = count + 1
        for day in DAY_CODES:
            occupied: dict[int, cp_model.IntVar] = {}
            for period in range(11):
                occupying = [
                    x[course.id]
                    for course in courses
                    if (day, period) in course.slots
                ]
                if not occupying:
                    continue
                flag = model.NewBoolVar(f"occ_{constraint.id}_{day}_{period}")
                model.AddMaxEquality(flag, occupying)
                occupied[period] = flag
            if window <= 0:
                continue
            for start in range(0, 12 - window):
                window_periods = list(range(start, start + window))
                if not all(period in occupied for period in window_periods):
                    continue
                model.Add(sum(occupied[period] for period in window_periods) <= count)
        return

    raise ValueError(f"알 수 없는 조건 타입: {constraint.type}")


def _credits_of(course: CatalogCourse, category: str | None) -> int:
    if category is None:
        return course.credits
    if category in {"전공", "교양"}:
        return course.credits if course.category_group == category else 0
    return course.credits if course.category == category else 0


def _forbid_exact_selection(
    model: cp_model.CpModel,
    x: Mapping[int, cp_model.IntVar],
    selected_ids: frozenset[int],
) -> None:
    if not x:
        model.Add(0 >= 1)
        return
    literals = []
    for course_id, var in x.items():
        if course_id in selected_ids:
            literals.append(var)
        else:
            literals.append(1 - var)
    model.Add(sum(literals) <= len(x) - 1)


def _candidate_from_ids(
    selected_ids: frozenset[int],
    by_id: Mapping[int, CatalogCourse],
    score: int,
) -> dict:
    selected = [by_id[course_id] for course_id in sorted(selected_ids)]
    used = {day for course in selected for day, _period in course.slots}
    days_used = [day for day in DAY_CODES if day in used]
    metrics = _metrics(selected)
    return {
        "courses": [
            {
                "id": course.id,
                "name": course.name,
                "credits": course.credits,
                "raw_time": course.raw_time,
            }
            for course in selected
        ],
        "total_credits": sum(course.credits for course in selected),
        "days_used": days_used,
        "metrics": metrics,
        "score": score,
    }


def _metrics(courses: Sequence[CatalogCourse]) -> dict:
    free_periods_by_day: dict[str, int] = {}
    max_consecutive = 0
    early_class_count = 0
    late_class_count = 0
    for course in courses:
        if any(period in EARLY_PERIODS for _day, period in course.slots):
            early_class_count += 1
        if any(period in LATE_PERIODS for _day, period in course.slots):
            late_class_count += 1

    for day in DAY_CODES:
        periods = {period for course in courses for slot_day, period in course.slots if slot_day == day}
        if not periods:
            continue
        free_periods_by_day[day] = max(periods) - min(periods) + 1 - len(periods)
        max_consecutive = max(max_consecutive, _max_consecutive(periods))

    return {
        "free_periods_by_day": free_periods_by_day,
        "max_consecutive": max_consecutive,
        "early_class_count": early_class_count,
        "late_class_count": late_class_count,
    }


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
