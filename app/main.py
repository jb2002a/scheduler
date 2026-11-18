"""Streamlit UI: 왼쪽 채팅, 오른쪽 주간 시간표."""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Any

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.cli import format_ask, load_env, load_semester_catalog, resolve_user_reply
from agent.loop import make_openai_caller, run_turn
from agent.session import Session
from app.grid import GridView, build_grid, day_label
from app.image import BLOCK_COLORS, render_timetable_png

DEFAULT_MODEL = "gpt-5.6-luna"
CSV_PATH = ROOT / "data" / "courses.csv"
DB_PATH = ROOT / "data" / "courses.db"
ENV_PATH = ROOT / ".env"


def main() -> None:
    st.set_page_config(page_title="원광대 시간표 에이전트", layout="wide")
    st.markdown(
        """
        <style>
        :root {
          color-scheme: light !important;
          --background-color: #FFFFFF;
          --secondary-background-color: #F8FAFC;
          --text-color: #0F172A;
          --primary-color: #2563EB;
        }
        html, body, .stApp,
        [data-testid="stAppViewContainer"],
        [data-testid="stAppViewBlockContainer"],
        [data-testid="stHeader"],
        [data-testid="stToolbar"],
        [data-testid="stMain"],
        section.main {
          background-color: #FFFFFF !important;
          color: #0F172A !important;
        }
        [data-testid="stHeader"] {
          background: rgba(255, 255, 255, 0.95) !important;
        }
        h1, h2, h3, h4, p, label, span, .stMarkdown, .stCaption {
          color: #0F172A !important;
        }
        [data-testid="stChatMessage"] {
          background-color: #F8FAFC !important;
          border: 1px solid #E2E8F0 !important;
          border-radius: 10px;
          color: #0F172A !important;
        }
        [data-testid="stAlert"] {
          background-color: #EFF6FF !important;
          color: #0F172A !important;
        }
        div[data-testid="stTextInput"] input {
          background-color: #FFFFFF !important;
          color: #0F172A !important;
          border: 1px solid #CBD5E1 !important;
        }
        .stButton > button, .stFormSubmitButton > button, .stDownloadButton > button {
          background-color: #2563EB !important;
          color: #FFFFFF !important;
          border: none !important;
        }
        [data-baseweb="radio"] label, [data-baseweb="radio"] div {
          color: #0F172A !important;
        }
        [data-testid="stAppViewBlockContainer"],
        .block-container {
          padding-top: 2.5rem !important;
          padding-bottom: 1.5rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    load_env(ENV_PATH)
    _init_state()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        st.error("OPENAI_API_KEY가 필요합니다. `.env`에 키를 넣은 뒤 새로고침하세요.")
        st.stop()

    catalog = _get_catalog()
    call_model = _get_caller()

    left, right = st.columns([1, 1.2], gap="large")

    with left:
        _render_composer(catalog, call_model)
        _render_chat()

    with right:
        _render_schedule_panel(catalog)


def _init_state() -> None:
    defaults: dict[str, Any] = {
        "session": Session(),
        "messages": [],
        "history": [],
        "pending_ask": None,
        "candidate_radio": 0,
        "busy": False,
        "pending_user_text": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


@st.cache_resource
def _get_catalog():
    return load_semester_catalog(CSV_PATH, DB_PATH, semester=1)


@st.cache_resource
def _get_caller():
    from openai import OpenAI

    client = OpenAI()
    return make_openai_caller(client, DEFAULT_MODEL)


def _render_chat() -> None:
    # 최신이 입력창 바로 아래, 이전 대화는 아래로 밀린다.
    for message in reversed(st.session_state.messages):
        role = message["role"]
        with st.chat_message("user" if role == "user" else "assistant"):
            st.markdown(message["content"])


def _render_composer(catalog, call_model) -> None:
    pending_ask = st.session_state.pending_ask
    busy = st.session_state.busy

    st.subheader("대화")
    if pending_ask and (pending_ask.get("options") or []):
        st.caption(pending_ask.get("question") or "선택해 주세요.")
        cols = st.columns(min(4, len(pending_ask["options"])))
        for index, option in enumerate(pending_ask["options"]):
            if cols[index].button(
                option,
                key=f"ask_opt_{index}_{option}",
                disabled=busy,
                use_container_width=True,
            ):
                st.session_state.pending_user_text = option

    with st.form("chat_form", clear_on_submit=True, border=False):
        row = st.columns([5, 1])
        with row[0]:
            prompt = st.text_input(
                "메시지",
                placeholder="조건을 말해 주세요. 예: 18학점, 수요일 공강",
                disabled=busy,
                label_visibility="collapsed",
            )
        with row[1]:
            submitted = st.form_submit_button(
                "전송",
                disabled=busy,
                use_container_width=True,
            )
        if submitted and prompt.strip():
            st.session_state.pending_user_text = resolve_user_reply(
                prompt, pending_ask
            )

    if st.session_state.pending_user_text is not None:
        _process_turn(catalog, call_model)


def _process_turn(catalog, call_model) -> None:
    user_text = st.session_state.pending_user_text
    st.session_state.pending_user_text = None
    if not user_text:
        return

    st.session_state.busy = True
    st.session_state.messages.append({"role": "user", "content": user_text})

    with st.spinner("시간표를 짜는 중…"):
        result = run_turn(
            st.session_state.session,
            catalog,
            user_text,
            call_model=call_model,
            history=st.session_state.history,
        )

    st.session_state.history.append({"role": "user", "content": user_text})

    if result.ask is not None:
        st.session_state.pending_ask = result.ask
        reply = format_ask(result.ask)
    else:
        st.session_state.pending_ask = None
        reply = result.text or "(응답 없음)"

    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.session_state.history.append({"role": "assistant", "content": reply})
    st.session_state.candidate_radio = 0
    st.session_state.busy = False
    st.rerun()


def _render_schedule_panel(catalog) -> None:
    st.subheader("시간표")
    session: Session = st.session_state.session
    last = session.last_result

    _render_constraints(session.constraints)

    if last is None:
        st.info("아직 시간표가 없습니다. 왼쪽에서 조건을 말해 주세요.")
        view = build_grid(None, catalog)
        _render_grid(view)
        return

    status = last.get("status")
    if status == "infeasible":
        st.warning("현재 조건으로는 해가 없습니다.")
        _render_relaxations(last.get("relaxations") or [], session.constraints)
        view = build_grid(None, catalog)
        _render_grid(view)
        return

    if status != "ok":
        st.error(last.get("message") or f"상태: {status}")
        view = build_grid(None, catalog)
        _render_grid(view)
        return

    candidates = last.get("candidates") or []
    if not candidates:
        st.info("후보가 비어 있습니다.")
        view = build_grid(None, catalog)
        _render_grid(view)
        return

    labels = [f"후보 {index}" for index in range(1, len(candidates) + 1)]
    if st.session_state.candidate_radio >= len(candidates):
        st.session_state.candidate_radio = 0
    selected = st.radio(
        "후보",
        options=list(range(len(candidates))),
        format_func=lambda i: labels[i],
        horizontal=True,
        key="candidate_radio",
    )

    candidate = candidates[selected]
    view = build_grid(candidate, catalog)
    if view.summary is not None:
        days = ", ".join(day_label(day) for day in view.summary.days_used) or "-"
        st.caption(
            f"총 {view.summary.total_credits}학점 · 등교 {days} · "
            f"아침 {view.summary.early_class_count} · 저녁 {view.summary.late_class_count}"
        )
    if view.missing_ids:
        st.caption(f"카탈로그에 없는 과목 id: {', '.join(map(str, view.missing_ids))}")
    _render_image_download(view, f"시간표-후보{selected + 1}.png")
    _render_grid(view)


def _render_grid(view: GridView) -> None:
    st.html(_grid_html(view))


def _render_image_download(view: GridView, filename: str) -> None:
    st.download_button(
        "이미지로 저장",
        data=render_timetable_png(view),
        file_name=filename,
        mime="image/png",
        key=f"timetable_png_{filename}",
    )


def _render_constraints(constraints: list[dict[str, Any]]) -> None:
    if not constraints:
        st.caption("적용된 조건 없음")
        return
    texts = []
    for item in constraints:
        source = str(item.get("source_text") or "").strip()
        if source:
            texts.append(source)
        else:
            texts.append(f"{item.get('type')} ({item.get('id')})")
    st.caption("조건: " + " · ".join(texts))


def _render_relaxations(
    relaxations: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
) -> None:
    if not relaxations:
        st.caption("완화 후보가 없습니다.")
        return
    by_id = {str(item.get("id")): item for item in constraints}
    lines = []
    for item in relaxations:
        drops = item.get("drop") or []
        labels = []
        for constraint_id in drops:
            constraint = by_id.get(str(constraint_id))
            if constraint and constraint.get("source_text"):
                labels.append(str(constraint["source_text"]))
            else:
                labels.append(str(constraint_id))
        lines.append("빼기: " + ", ".join(labels))
    st.markdown("\n".join(f"- {line}" for line in lines))


def _grid_html(view: GridView) -> str:
    color_by_id: dict[int, str] = {}
    next_color = 0

    def color_for(course_id: int) -> str:
        nonlocal next_color
        if course_id not in color_by_id:
            color_by_id[course_id] = BLOCK_COLORS[next_color % len(BLOCK_COLORS)]
            next_color += 1
        return color_by_id[course_id]

    header = "".join(
        f"<th>{html.escape(day_label(day))}</th>" for day in view.days
    )
    rows: list[str] = []
    skip: set[tuple[str, int]] = set()

    for period in view.periods:
        label = view.period_labels.get(period, "")
        cells = [
            f"<th class='period'>{period}<br/><span>{html.escape(label)}</span></th>"
        ]
        for day in view.days:
            key = (day, period)
            if key in skip:
                continue
            block = view.cells.get(key)
            if block is None:
                cells.append("<td class='empty'></td>")
                continue
            if block.start_period != period:
                cells.append("<td class='empty'></td>")
                continue
            for covered in range(block.start_period, block.end_period + 1):
                if covered != period:
                    skip.add((day, covered))
            bg = color_for(block.course_id)
            name = html.escape(block.name)
            meta = html.escape(f"{block.credits}학점 · {block.raw_time}")
            cells.append(
                f"<td class='course' rowspan='{block.row_span}' "
                f"style='background:{bg}'>"
                f"<div class='name'>{name}</div>"
                f"<div class='meta'>{meta}</div>"
                f"</td>"
            )
        rows.append("<tr>" + "".join(cells) + "</tr>")

    style = """
    <style>
    .tt { border-collapse: collapse; width: 100%; table-layout: fixed; font-size: 0.85rem; }
    .tt th, .tt td { border: 1px solid #d1d5db; vertical-align: top; padding: 4px; }
    .tt th { background: #f3f4f6; text-align: center; font-weight: 600; }
    .tt th.period { width: 3.5rem; color: #4b5563; font-weight: 500; }
    .tt th.period span { font-size: 0.7rem; color: #9ca3af; }
    .tt td.empty { height: 2.4rem; background: #fff; }
    .tt td.course { text-align: left; color: #111827; }
    .tt td.course .name { font-weight: 600; line-height: 1.2; color: #111827; }
    .tt td.course .meta { color: #4b5563; font-size: 0.75rem; margin-top: 2px; }
    </style>
    """
    table = (
        f"<table class='tt'><thead><tr><th></th>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    return style + table


if __name__ == "__main__":
    main()
