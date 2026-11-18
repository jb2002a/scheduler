"""베이스라인 응답을 E1–E8으로 채점한다."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from db.validator import CatalogCourse, Finding, validate

ERROR_CODES = ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8")
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ScoreResult:
    findings: tuple[Finding, ...]
    parsed: dict[str, Any] | None
    parse_error: str | None = None

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def codes(self) -> list[str]:
        return [finding.code for finding in self.findings]


def resolve_constraints(
    catalog: Sequence[CatalogCourse],
    constraints: Sequence[Mapping],
) -> list[dict]:
    """시나리오 조건의 name 기반 course 제약을 course_id로 바꾼다."""
    resolved: list[dict] = []
    for raw in constraints:
        item = {
            "id": raw.get("id"),
            "type": raw["type"],
            "params": dict(raw.get("params") or {}),
        }
        if raw.get("source_text") is not None:
            item["source_text"] = raw["source_text"]
        if item["type"] == "course":
            params = item["params"]
            if "course_id" not in params:
                name = str(params.get("name") or "").strip()
                course = _require_unique_named_course(catalog, name)
                params = {
                    "course_id": course.id,
                    "mode": params.get("mode"),
                }
                item["params"] = params
        resolved.append(item)
    return resolved


def parse_model_response(text: str) -> tuple[dict[str, Any] | None, str | None]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = FENCE_RE.sub("", cleaned).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as error:
        extracted = _extract_json_object(cleaned)
        if extracted is None:
            return None, f"JSON 파싱 실패: {error.msg}"
        try:
            payload = json.loads(extracted)
        except json.JSONDecodeError as nested:
            return None, f"JSON 파싱 실패: {nested.msg}"
    if not isinstance(payload, dict):
        return None, "JSON 최상위가 객체가 아닙니다."
    return payload, None


def score_response(
    catalog: Sequence[CatalogCourse],
    response_text: str,
    constraints: Sequence[Mapping],
    expected_feasible: bool,
    semester: int = 1,
) -> ScoreResult:
    parsed, parse_error = parse_model_response(response_text)
    if parse_error is not None or parsed is None:
        return ScoreResult(
            findings=(Finding("E8", parse_error or "JSON 파싱 실패"),),
            parsed=None,
            parse_error=parse_error,
        )

    findings: list[Finding] = []
    feasible = parsed.get("feasible")
    if not isinstance(feasible, bool):
        findings.append(Finding("E8", "feasible가 boolean이 아닙니다."))
        return ScoreResult(tuple(findings), parsed, parse_error="feasible 형식 오류")

    if feasible != expected_feasible:
        findings.append(
            Finding(
                "E7",
                f"실행 가능성 오판: expected={expected_feasible}, got={feasible}",
            )
        )

    if not feasible:
        # 불가능하다고 답한 경우 시간표 내용은 채점하지 않는다.
        return ScoreResult(tuple(findings), parsed)

    courses = parsed.get("courses")
    if not isinstance(courses, list):
        findings.append(Finding("E8", "courses가 배열이 아닙니다."))
        return ScoreResult(tuple(findings), parsed, parse_error="courses 형식 오류")

    selected: list[dict] = []
    for item in courses:
        if not isinstance(item, Mapping):
            findings.append(Finding("E8", "courses 항목이 객체가 아닙니다."))
            return ScoreResult(tuple(findings), parsed, parse_error="courses 항목 형식 오류")
        selected.append(dict(item))

    total_credits = parsed.get("total_credits")
    if total_credits is not None and not isinstance(total_credits, int):
        if isinstance(total_credits, float) and total_credits.is_integer():
            total_credits = int(total_credits)
        else:
            findings.append(Finding("E8", "total_credits가 정수가 아닙니다."))
            return ScoreResult(tuple(findings), parsed, parse_error="total_credits 형식 오류")

    selected = align_course_names(catalog, selected)
    resolved = resolve_constraints(catalog, constraints)
    validation = validate(
        catalog,
        selected,
        resolved,
        total_credits=total_credits,
        semester=semester,
    )
    findings.extend(validation.findings)
    return ScoreResult(tuple(findings), parsed)


def summarize_scores(results: Sequence[ScoreResult]) -> dict[str, Any]:
    total = len(results)
    perfect = sum(1 for result in results if result.ok)
    counts = {code: 0 for code in ERROR_CODES}
    for result in results:
        for code in set(result.codes):
            counts[code] += 1
    return {
        "total": total,
        "perfect": perfect,
        "perfect_rate": (perfect / total) if total else 0.0,
        "error_counts": counts,
    }


def align_course_names(
    catalog: Sequence[CatalogCourse],
    selected: Sequence[Mapping],
) -> list[dict]:
    """띄어쓰기만 다른 이름은 목록의 표기로 맞춘다. 그 차이는 오류로 치지 않는다."""
    by_compact: dict[str, list[CatalogCourse]] = {}
    for course in catalog:
        by_compact.setdefault(_compact_name(course.name), []).append(course)
    aligned: list[dict] = []
    for raw in selected:
        item = dict(raw)
        name = str(item.get("name") or "")
        matches = by_compact.get(_compact_name(name), [])
        if len(matches) == 1:
            item["name"] = matches[0].name
        aligned.append(item)
    return aligned


def _compact_name(name: str) -> str:
    return WHITESPACE_RE.sub("", name)


def _require_unique_named_course(
    catalog: Sequence[CatalogCourse], name: str
) -> CatalogCourse:
    matched = [course for course in catalog if course.name == name]
    if not matched:
        raise ValueError(f"카탈로그에 없는 과목명: {name}")
    if len(matched) > 1:
        raise ValueError(f"과목명이 중복됩니다: {name}")
    return matched[0]


def _extract_json_object(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    return text[start : end + 1]
