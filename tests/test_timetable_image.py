"""시간표 PNG에 과목 블록이 칠해지는지 확인한다."""

from __future__ import annotations

from app.grid import WEEKDAY_CODES, build_grid
from app.image import BLOCK_COLORS, cell_box, image_size, render_timetable_image, render_timetable_png
from db.validator import CatalogCourse


def course(course_id, name, credits, raw_time, slots):
    return CatalogCourse(
        id=course_id,
        semester=1,
        course_code=f"C{course_id}",
        category="기전",
        category_group="전공",
        name=name,
        name_en=None,
        credits=credits,
        raw_time=raw_time,
        schedulable=True,
        slots=tuple(slots),
    )


ALGO = course(1, "알고리즘", 3, "월12", [("MON", 1), ("MON", 2)])
LIBERAL = course(5, "글쓰기", 2, "수3", [("WED", 3)])


def _candidate():
    return {
        "courses": [
            {"id": 1, "name": "알고리즘", "credits": 3, "raw_time": "월12"},
            {"id": 5, "name": "글쓰기", "credits": 2, "raw_time": "수3"},
        ],
        "total_credits": 5,
        "days_used": ["MON", "WED"],
        "metrics": {
            "free_periods_by_day": {"TUE": 11},
            "max_consecutive": 2,
            "early_class_count": 1,
            "late_class_count": 0,
        },
    }


def _view():
    return build_grid(
        _candidate(),
        [ALGO, LIBERAL],
        period_labels={1: "09:00", 2: "10:00", 3: "11:00"},
    )


def _fill(image, day: str, period: int, view):
    x, y, width, height = cell_box(view, day, period)
    return image.getpixel((x + width - 12, y + height // 2))


def test_png_bytes_and_size():
    view = _view()
    png = render_timetable_png(view)
    assert png.startswith(b"\x89PNG")
    image = render_timetable_image(view)
    assert image.size == image_size(view)
    assert image.size[0] > 0 and image.size[1] > 0


def test_course_blocks_use_palette_and_span_rows():
    view = _view()
    image = render_timetable_image(view)
    first = tuple(int(part, 16) for part in (BLOCK_COLORS[0][1:3], BLOCK_COLORS[0][3:5], BLOCK_COLORS[0][5:7]))
    second = tuple(int(part, 16) for part in (BLOCK_COLORS[1][1:3], BLOCK_COLORS[1][3:5], BLOCK_COLORS[1][5:7]))

    assert view.days == WEEKDAY_CODES
    assert _fill(image, "MON", 1, view) == first
    assert _fill(image, "MON", 2, view) == first
    assert _fill(image, "WED", 3, view) == second
    assert _fill(image, "TUE", 1, view) == (255, 255, 255)
    assert _fill(image, "WED", 1, view) == (255, 255, 255)


def test_empty_grid_is_white_weekday_table():
    view = build_grid(None, [ALGO], period_labels={0: "08:50"})
    image = render_timetable_image(view)
    assert view.summary is None
    assert _fill(image, "MON", 0, view) == (255, 255, 255)
    assert _fill(image, "FRI", 10, view) == (255, 255, 255)
