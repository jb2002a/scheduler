"""주간 시간표 그리드를 PNG로 그린다."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from db.time_parser import DAY_CODES
from app.grid import GridView, day_label

BLOCK_COLORS = [
    "#DBEAFE",
    "#DCFCE7",
    "#FEF3C7",
    "#FCE7F3",
    "#E0E7FF",
    "#FFEDD5",
    "#E0F2FE",
    "#F3E8FF",
]

SCALE = 2
CELL_W = 160 * SCALE
CELL_H = 68 * SCALE
PERIOD_W = 76 * SCALE
HEADER_H = 40 * SCALE
MARGIN = 24 * SCALE
BORDER = SCALE

TEXT = (17, 24, 39)
META = (75, 85, 99)
GRID = (209, 213, 219)
HEADER_BG = (243, 244, 246)
TIME = (156, 163, 175)
WHITE = (255, 255, 255)

_FONT_CANDIDATES: tuple[tuple[str, int], ...] = (
    ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 0),
    ("/System/Library/Fonts/Supplemental/AppleGothic.ttf", 0),
    ("/Library/Fonts/Arial Unicode.ttf", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 0),
)


def render_timetable_png(view: GridView) -> bytes:
    buffer = BytesIO()
    render_timetable_image(view).save(buffer, format="PNG")
    return buffer.getvalue()


def render_timetable_image(view: GridView) -> Image.Image:
    width, height = image_size(view)
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)
    _draw_title(draw, view)
    _draw_grid(draw, view)
    _draw_blocks(draw, view)
    return image


def image_size(view: GridView) -> tuple[int, int]:
    width = MARGIN * 2 + PERIOD_W + len(view.days) * CELL_W
    height = MARGIN * 2 + _title_height(view) + HEADER_H + len(view.periods) * CELL_H
    return width, height


def cell_box(view: GridView, day: str, period: int) -> tuple[int, int, int, int]:
    """한 교시 칸의 (x, y, w, h). 좌표는 이미지 픽셀이다."""
    day_index = view.days.index(day)
    period_index = view.periods.index(period)
    origin_x, origin_y = _table_origin(view)
    x = origin_x + PERIOD_W + day_index * CELL_W
    y = origin_y + HEADER_H + period_index * CELL_H
    return x, y, CELL_W, CELL_H


def _table_origin(view: GridView) -> tuple[int, int]:
    return MARGIN, MARGIN + _title_height(view)


def _title_height(view: GridView) -> int:
    if view.summary is None:
        return 0
    return 36 * SCALE


def _draw_title(draw: ImageDraw.ImageDraw, view: GridView) -> None:
    summary = view.summary
    if summary is None:
        return
    days = ", ".join(day_label(day) for day in _weekday_order(summary.days_used)) or "-"
    text = (
        f"총 {summary.total_credits}학점 · 등교 {days} · "
        f"아침 {summary.early_class_count} · 저녁 {summary.late_class_count}"
    )
    font = _font(15 * SCALE)
    draw.text((MARGIN, MARGIN), text, font=font, fill=TEXT)


def _draw_grid(draw: ImageDraw.ImageDraw, view: GridView) -> None:
    origin_x, origin_y = _table_origin(view)
    header_font = _font(15 * SCALE)
    period_font = _font(14 * SCALE)
    time_font = _font(10 * SCALE)

    _box(draw, origin_x, origin_y, PERIOD_W, HEADER_H, HEADER_BG)
    for index, day in enumerate(view.days):
        x = origin_x + PERIOD_W + index * CELL_W
        _box(draw, x, origin_y, CELL_W, HEADER_H, HEADER_BG)
        _center_text(draw, x, origin_y, CELL_W, HEADER_H, day_label(day), header_font, TEXT)

    for row, period in enumerate(view.periods):
        y = origin_y + HEADER_H + row * CELL_H
        _box(draw, origin_x, y, PERIOD_W, CELL_H, WHITE)
        label = view.period_labels.get(period, "")
        number_box = (origin_x, y, PERIOD_W, CELL_H // 2 + 4 * SCALE)
        _center_text(
            draw,
            number_box[0],
            number_box[1],
            number_box[2],
            number_box[3],
            str(period),
            period_font,
            META,
        )
        if label:
            _center_text(
                draw,
                origin_x,
                y + CELL_H // 2 - 2 * SCALE,
                PERIOD_W,
                CELL_H // 2,
                label,
                time_font,
                TIME,
            )
        for col in range(len(view.days)):
            x = origin_x + PERIOD_W + col * CELL_W
            _box(draw, x, y, CELL_W, CELL_H, WHITE)


def _draw_blocks(draw: ImageDraw.ImageDraw, view: GridView) -> None:
    colors = _colors_by_course(view)
    origin_x, origin_y = _table_origin(view)
    day_index = {day: index for index, day in enumerate(view.days)}
    period_index = {period: index for index, period in enumerate(view.periods)}

    for block in view.blocks:
        if block.day not in day_index or block.start_period not in period_index:
            continue
        x = origin_x + PERIOD_W + day_index[block.day] * CELL_W
        y = origin_y + HEADER_H + period_index[block.start_period] * CELL_H
        height = block.row_span * CELL_H
        fill = colors[block.course_id]
        _box(draw, x, y, CELL_W, height, fill)
        _draw_block_text(
            draw,
            x,
            y,
            CELL_W,
            height,
            block.name,
            f"{block.credits}학점 · {block.raw_time}",
        )


def _colors_by_course(view: GridView) -> dict[int, tuple[int, int, int]]:
    colors: dict[int, tuple[int, int, int]] = {}
    for block in view.blocks:
        if block.course_id not in colors:
            colors[block.course_id] = _hex(BLOCK_COLORS[len(colors) % len(BLOCK_COLORS)])
    return colors


def _draw_block_text(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    name: str,
    meta: str,
) -> None:
    pad = 8 * SCALE
    name_font = _font(14 * SCALE)
    meta_font = _font(11 * SCALE)
    max_width = width - pad * 2
    name_h = int(name_font.size * 1.3)
    meta_h = int(meta_font.size * 1.3)
    max_name_lines = max(1, (height - pad * 2 - meta_h) // name_h)
    name_lines = _clip_lines(_wrap(draw, name, name_font, max_width), max_name_lines)
    cursor = y + pad
    for line in name_lines:
        draw.text((x + pad, cursor), line, font=name_font, fill=TEXT)
        cursor += name_h
    if cursor + meta_h <= y + height - pad // 2:
        meta_line = _clip_lines(_wrap(draw, meta, meta_font, max_width), 1)[0]
        draw.text((x + pad, cursor), meta_line, font=meta_font, fill=META)


def _box(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    fill: tuple[int, int, int],
) -> None:
    draw.rectangle((x, y, x + width - 1, y + height - 1), fill=fill, outline=GRID, width=BORDER)


def _center_text(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    draw.text((x + width / 2, y + height / 2), text, font=font, fill=fill, anchor="mm")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    for char in text:
        trial = current + char
        if current and draw.textlength(trial, font=font) > max_width:
            lines.append(current)
            current = char
        else:
            current = trial
    if current:
        lines.append(current)
    return lines or [""]


def _clip_lines(lines: list[str], max_lines: int) -> list[str]:
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    last = kept[-1]
    kept[-1] = (last[:-1] + "…") if last else "…"
    return kept


def _weekday_order(days: tuple[str, ...] | list[str]) -> list[str]:
    order = {day: index for index, day in enumerate(DAY_CODES)}
    return sorted(days, key=lambda day: order.get(day, len(order)))


def _hex(value: str) -> tuple[int, int, int]:
    raw = value.removeprefix("#")
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


@lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.ImageFont:
    for path, index in _FONT_CANDIDATES:
        if Path(path).is_file():
            return ImageFont.truetype(path, size=size, index=index)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()
