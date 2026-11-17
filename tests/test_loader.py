import sqlite3
from pathlib import Path

from db.loader import load_csv
from db.validator import load_catalog
from md_to_csv import catalog_rows, convert_text, load_categories, write_csv

ROOT = Path(__file__).resolve().parents[1]


def test_loader_stores_semester_one_catalog(tmp_path):
    text = (ROOT / "data" / "데이터베이스.md").read_text(encoding="utf-8")
    result = convert_text(text, load_categories())
    assert result.errors == []
    rows = catalog_rows(result.rows)
    csv_path = tmp_path / "courses.csv"
    db_path = tmp_path / "courses.db"
    write_csv(rows, csv_path)

    summary = load_csv(csv_path, db_path)
    assert summary["parse_failures"] == []
    assert summary["courses"] == len(rows) == 335
    assert summary["by_semester"] == {1: 335, 2: 0}
    assert summary["unschedulable"] == []

    connection = sqlite3.connect(db_path)
    try:
        catalog = load_catalog(connection)
        area = connection.execute(
            "SELECT area FROM courses WHERE course_code = 'L00105' AND semester = 1"
        ).fetchone()
    finally:
        connection.close()

    assert {course.semester for course in catalog} == {1}
    assert all(course.course_code != "L00005" for course in catalog)

    intro = next(course for course in catalog if course.course_code == "374140")
    assert intro.slots == (("MON", 1), ("MON", 2), ("WED", 1))
    assert intro.schedulable is True

    writing = [course for course in catalog if course.course_code == "L00105"]
    assert len(writing) == 1
    assert area == ("논리와표현",)


def test_loader_stores_unschedulable_blank_time(tmp_path):
    csv_path = tmp_path / "courses.csv"
    db_path = tmp_path / "courses.db"
    csv_path.write_text(
        "semester,course_code,category,category_group,name,name_en,credits,theory,practice,"
        "raw_time,schedulable,year,area,department\n"
        "1,L00005,교필,교양,졸업논문,Thesis,0,0,0,-,0,,졸업,\n",
        encoding="utf-8",
    )
    summary = load_csv(csv_path, db_path)
    assert summary["parse_failures"] == []
    assert summary["unschedulable"] == ["1학기 L00005 졸업논문"]

    connection = sqlite3.connect(db_path)
    try:
        catalog = load_catalog(connection)
    finally:
        connection.close()
    thesis = catalog[0]
    assert thesis.slots == ()
    assert thesis.schedulable is False
    assert thesis.credits == 0


def test_loader_rejects_unparsed_time(tmp_path):
    csv_path = tmp_path / "courses.csv"
    db_path = tmp_path / "courses.db"
    csv_path.write_text(
        "semester,course_code,category,category_group,name,name_en,credits,theory,practice,"
        "raw_time,schedulable,year,area,department\n"
        "1,C009,기전,전공,오류과목,,3,3,0,월21,1,,,,\n",
        encoding="utf-8",
    )
    summary = load_csv(csv_path, db_path)
    assert summary["parse_failures"]
    assert not db_path.exists()
