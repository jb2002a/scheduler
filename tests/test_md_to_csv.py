import json
from pathlib import Path

from md_to_csv import catalog_rows, convert_text, load_categories

ROOT = Path(__file__).resolve().parents[1]

SAMPLE = """
## 1. 전공
### 1학년
| 학기 | 구분 | 학수번호 | 교과목명 | 학점 | 이론 | 실습 | 강의시간 |
|---|---|---|---|---|---|---|---|
| 1 | 교필 | L00002 | 대학생활 / Guide | 2.0 | 2.00 | 0.00 | 화34 |
| 1 | 기전 | C001 | 컴퓨터개론 / Intro | 3.0 | 3.00 | 0.00 | 월12수1 |
| 2 | 교필 | L00005 | 졸업논문 / Thesis | 0.0 | 0.00 | 0.00 | - |

## 2. 교양필수
| 영역 | 학기 | 구분 | 학수번호 | 교과목명 | 학점 | 이론 | 실습 | 주관학과 | 강의시간 |
|---|---|---|---|---|---|---|---|---|---|
| 대학생활 | 1 | 교필 | L00002 | 대학생활 / Guide | 2.0 | 2.00 | 0.00 | 각 학과 | 화34 |
| 졸업 | 2 | 교필 | L00005 | 졸업논문 / Thesis | 0.0 | 0.00 | 0.00 | - | - |

## 3. 교양기초
### 3.1. 논리와표현
| 학기 | 구분 | 학수번호 | 교과목명 | 학점 | 이론 | 실습 | 주관학과 | 강의시간 |
|---|---|---|---|---|---|---|---|---|
| 1, 2 | 교기 | L00105 | 글쓰기 | 3.0 | 3.00 | 0.00 | 교양교육원 | 월23목2 |
"""

CATEGORIES = {"교필": "교양", "교기": "교양", "기전": "전공", "선전": "전공"}


def _rows(text: str = SAMPLE):
    result = convert_text(text, CATEGORIES)
    return result


def test_splits_both_semesters_and_merges_duplicate_code():
    result = _rows()
    assert result.errors == []
    assert result.duplicates_removed == 2

    codes = [(row["course_code"], row["semester"]) for row in result.rows]
    assert codes.count(("L00002", 1)) == 1
    assert ("L00105", 1) in codes
    assert ("L00105", 2) in codes

    life = next(row for row in result.rows if row["course_code"] == "L00002")
    assert life["year"] == 1
    assert life["area"] == "대학생활"
    assert life["department"] == "각 학과"
    assert life["name"] == "대학생활"
    assert life["name_en"] == "Guide"
    assert life["category_group"] == "교양"
    assert life["credits"] == 2
    assert life["schedulable"] == 1

    thesis = next(row for row in result.rows if row["course_code"] == "L00005")
    assert thesis["schedulable"] == 0
    assert thesis["department"] is None
    assert thesis["area"] == "졸업"

    writing = next(row for row in result.rows if row["course_code"] == "L00105" and row["semester"] == 1)
    assert writing["area"] == "논리와표현"
    assert writing["name_en"] is None
    assert writing["category_group"] == "교양"

    major = next(row for row in result.rows if row["course_code"] == "C001")
    assert major["category_group"] == "전공"
    assert major["year"] == 1


def test_conflicting_duplicate_is_an_error():
    text = """
| 학기 | 구분 | 학수번호 | 교과목명 | 학점 | 강의시간 |
|---|---|---|---|---|---|
| 1 | 기전 | C001 | 같은코드 | 3.0 | 월12 |
| 1 | 기전 | C001 | 같은코드 | 3.0 | 화34 |
"""
    result = convert_text(text, CATEGORIES)
    assert any("C001" in error and "강의시간" in error for error in result.errors)


def test_bad_time_and_credit_are_errors():
    text = """
| 학기 | 구분 | 학수번호 | 교과목명 | 학점 | 강의시간 |
|---|---|---|---|---|---|
| 1 | 기전 | C002 | 시간오류 | 3.0 | 월21 |
| 1 | 기전 | C003 | 학점오류 | 3.5 | 월12 |
| 1 | 기타 | C004 | 구분오류 | 3.0 | 월12 |
"""
    result = convert_text(text, CATEGORIES)
    assert any("파싱 실패" in error and "C002" in error for error in result.errors)
    assert any("3.5" in error for error in result.errors)
    assert any("알 수 없는 구분" in error for error in result.errors)
    assert result.rows == []


def test_real_markdown_has_no_parse_failures():
    text = (ROOT / "data" / "데이터베이스.md").read_text(encoding="utf-8")
    categories = load_categories(ROOT / "config" / "categories.json")
    result = convert_text(text, categories)
    assert result.errors == []

    keys = [(row["course_code"], row["semester"]) for row in result.rows]
    assert len(keys) == len(set(keys))
    assert keys.count(("L00002", 1)) == 1
    assert keys.count(("L00005", 2)) == 1
    assert ("L00105", 1) in keys and ("L00105", 2) in keys
    assert {row["semester"] for row in result.rows} <= {1, 2}
    assert {row["category_group"] for row in result.rows} <= {"전공", "교양"}

    thesis = next(row for row in result.rows if row["course_code"] == "L00005")
    assert thesis["schedulable"] == 0
    life = next(row for row in result.rows if row["course_code"] == "L00002")
    assert life["year"] == 1
    assert life["area"] == "대학생활"
    assert life["department"] == "각 학과(부)"

    intro = next(row for row in result.rows if row["course_code"] == "374140")
    assert intro["name"] == "컴퓨터개론"
    assert intro["name_en"] == "Interduction to Computers"
    assert intro["raw_time"] == "월12수1"

    assert all(isinstance(row["credits"], int) for row in result.rows)
    assert result.duplicates_removed >= 2
    json.dumps([row["name"] for row in result.rows], ensure_ascii=False)

    catalog = catalog_rows(result.rows)
    assert len(catalog) == 335
    assert {row["semester"] for row in catalog} == {1}
    assert all(row["course_code"] != "L00005" for row in catalog)
    assert any(row["course_code"] == "L00105" for row in catalog)
