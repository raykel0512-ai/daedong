# app.py
# 시험 시감 자동 편성 v2.1
# streamlit run app.py

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st

from scheduler import (
    Teacher,
    build_teachers,
    run_assignment,
    compute_stats,
    check_violations,
)
import db

# ══════════════════════════════════════════════════════════════
# 페이지 설정
# ══════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="시험 시감 자동 편성",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🧮 시험 시감 자동 편성 v2.1")
st.caption(
    "날별 교사 명단 분리 · 정/부감독 전용 구분 · 특정 반 추가 배정 · "
    "누적 통계 · Supabase 공유 저장"
)

# ══════════════════════════════════════════════════════════════
# Supabase 연결
# ══════════════════════════════════════════════════════════════
supabase = db.get_client()
db_connected = supabase is not None

if not db_connected:
    st.info(
        "💡 Supabase가 연결되지 않아 **로컬 세션 모드**로 동작합니다. "
        "저장/공유 기능을 사용하려면 `.streamlit/secrets.toml`을 설정하세요 (README 참조).",
        icon="ℹ️",
    )

# ══════════════════════════════════════════════════════════════
# 사이드바: 기본 설정
# ══════════════════════════════════════════════════════════════
with st.sidebar:
    st.header("⚙️ 기본 설정")

    # ── 시험 세션 관리 (DB 연결 시) ──────────────────────────
    if db_connected:
        st.subheader("📂 시험 세션")
        sessions = db.list_sessions(supabase)
        session_names = ["+ 새 세션 만들기"] + [s["name"] for s in sessions]
        selected_session_name = st.selectbox("세션 선택", session_names)

        if selected_session_name == "+ 새 세션 만들기":
            new_session_name = st.text_input("새 세션 이름", placeholder="예: 2025-1학기 중간고사")
            st.session_state["current_session_id"] = None
            st.session_state["current_session_name"] = new_session_name
        else:
            sel = next((s for s in sessions if s["name"] == selected_session_name), None)
            if sel:
                st.session_state["current_session_id"] = sel["id"]
                st.session_state["current_session_name"] = sel["name"]
        st.markdown("---")

    # ── 시험 일수 ────────────────────────────────────────────
    num_days = st.number_input("시험 일수", min_value=1, max_value=10, value=4, step=1)

    # ── 학년/학급 구성 ────────────────────────────────────────
    st.subheader("학년/학급 구성")
    num_grades = st.number_input("학년 수", min_value=1, max_value=6, value=3, step=1)
    classes_per_grade = st.number_input(
        "학년별 학급 수 (동일)", min_value=1, max_value=30, value=8, step=1
    )

    # ── 하루·학년별 교시 수 ───────────────────────────────────
    st.subheader("일차별·학년별 교시 수")
    periods_by_day_grade: list[list[int]] = []
    for d in range(1, num_days + 1):
        with st.expander(f"{d}일차", expanded=(d == 1)):
            per_grade = []
            for g in range(1, num_grades + 1):
                per_grade.append(
                    st.number_input(
                        f"{g}학년 교시 수",
                        min_value=0,
                        max_value=10,
                        value=2,
                        step=1,
                        key=f"pbd_{d}_{g}",
                    )
                )
            periods_by_day_grade.append(per_grade)

    st.markdown("---")

    # ── 누적 통계 기준 일차 ───────────────────────────────────
    st.subheader("누적 통계 기준")
    accum_from_day = st.number_input(
        "누적 집계 시작 일차 (1 = 전체)",
        min_value=1,
        max_value=num_days,
        value=1,
        step=1,
    )

# ── 전역 타입 안전 변환 (st.number_input은 float 반환) ──
num_days          = int(num_days)
num_grades        = int(num_grades)
classes_per_grade = int(classes_per_grade)
periods_by_day_grade = [[int(p) for p in row] for row in periods_by_day_grade]

# ══════════════════════════════════════════════════════════════
# 섹션 1 · CSV 업로드 (날별)
# ══════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("① 일차별 교사 명단 업로드")

st.markdown("""
**CSV 필수 열**: `name`

**선택 열**:
| 열 이름 | 설명 | 예시 |
|--------|------|------|
| `role` | 역할 구분 | `정부` (기본) / `부만` |
| `exclude` | 제외 규칙 (세미콜론 구분) | `D1P2; 1-3; D2P1@2-4` |
| `extra_classes` | 추가 감독 담당 반 | `1-4~6` 또는 `1-4,1-5` |
| `priority` | 우선순위 (숫자, 작을수록 먼저) | `1` |

**제외 규칙 형식**:
- `D1P2` → 1일차 2교시 전체
- `1-3` → 1학년 3반 전체
- `D1P2@1-3` → 1일차 2교시 1학년 3반만

**추가 감독 반 (extra_classes)**:
- `1-4~6` → 1학년 4, 5, 6반
- `2-1,2-2` → 2학년 1반, 2반
""")

# 샘플 CSV 다운로드
sample_df = pd.DataFrame({
    "name": [f"교사{i:02d}" for i in range(1, 11)],
    "role": ["정부"] * 8 + ["부만"] * 2,
    "exclude": ["", "D1P2", "1-3", "D2P1@1-4", "", "", "", "", "", ""],
    "extra_classes": ["", "", "", "", "1-4~6", "", "", "", "", ""],
    "priority": [1, 1, 2, 2, 3, 3, None, None, None, None],
})
st.download_button(
    "📥 샘플 CSV 내려받기",
    data=sample_df.to_csv(index=False).encode("utf-8-sig"),
    file_name="sample_teachers.csv",
    mime="text/csv",
)

# 구글 스프레드시트 URL → CSV 변환 함수
def gsheet_url_to_csv_url(url: str) -> str | None:
    """
    구글 스프레드시트 공유 URL → CSV 다운로드 URL 변환
    지원 형식:
      - https://docs.google.com/spreadsheets/d/SHEET_ID/edit#gid=0
      - https://docs.google.com/spreadsheets/d/SHEET_ID/edit?usp=sharing
    """
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        return None
    sheet_id = m.group(1)
    # gid 파라미터 추출 (시트 탭 구분)
    gid_m = re.search(r"gid=(\d+)", url)
    gid = gid_m.group(1) if gid_m else "0"
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def load_gsheet(url: str) -> pd.DataFrame | None:
    """구글 스프레드시트 URL로부터 DataFrame 로드"""
    csv_url = gsheet_url_to_csv_url(url)
    if not csv_url:
        return None
    try:
        df = pd.read_csv(csv_url)
        df.columns = [c.strip().lower() for c in df.columns]
        return df
    except Exception as e:
        st.error(f"구글 스프레드시트 로드 실패: {e}\n공유 설정이 '링크가 있는 모든 사용자'로 되어 있는지 확인하세요.")
        return None


# ── session_state에 날별 df 유지 (버튼 클릭시 재실행돼도 데이터 보존) ──
if "day_dfs" not in st.session_state:
    st.session_state["day_dfs"] = {}

# 날별 업로드 탭
upload_tabs = st.tabs([f"{d}일차 교사" for d in range(1, num_days + 1)])
day_teacher_dfs: list[pd.DataFrame | None] = []

for d_idx, utab in enumerate(upload_tabs, start=1):
    with utab:
        col_upload, col_preview = st.columns([1, 2])
        with col_upload:
            # 입력 방식 선택
            input_method = st.radio(
                "입력 방식",
                ["CSV 파일 업로드", "구글 스프레드시트 URL"],
                key=f"input_method_{d_idx}",
                horizontal=True,
            )

            if input_method == "CSV 파일 업로드":
                uploaded = st.file_uploader(
                    f"{d_idx}일차 교사 명단 CSV",
                    type=["csv"],
                    key=f"upload_{d_idx}",
                )
                if uploaded is not None:
                    try:
                        df_input = pd.read_csv(uploaded)
                    except UnicodeDecodeError:
                        df_input = pd.read_csv(uploaded, encoding="utf-8-sig")
                    df_input.columns = [c.strip().lower() for c in df_input.columns]
                    if "name" not in df_input.columns:
                        st.error("'name' 열이 없습니다.")
                    else:
                        # session_state에 저장 → 재실행해도 유지
                        st.session_state["day_dfs"][d_idx] = df_input
                        st.success(f"{len(df_input)}명 로드됨")

            else:  # 구글 스프레드시트
                st.caption(
                    "스프레드시트 공유 설정을 **'링크가 있는 모든 사용자 → 뷰어'** 로 설정해야 해요."
                )
                gsheet_url = st.text_input(
                    "구글 스프레드시트 URL 붙여넣기",
                    placeholder="https://docs.google.com/spreadsheets/d/...",
                    key=f"gsheet_url_{d_idx}",
                )
                if st.button("📥 불러오기", key=f"gsheet_load_{d_idx}"):
                    if gsheet_url.strip():
                        df_gs = load_gsheet(gsheet_url.strip())
                        if df_gs is not None:
                            st.session_state["day_dfs"][d_idx] = df_gs
                            st.success(f"{len(df_gs)}명 불러왔습니다!")
                    else:
                        st.warning("URL을 먼저 입력해 주세요.")

            # DB에서 불러오기
            if db_connected and st.session_state.get("current_session_id"):
                if st.button(f"☁️ DB에서 불러오기", key=f"db_load_{d_idx}"):
                    raw = db.load_day_teachers(
                        supabase, st.session_state["current_session_id"], d_idx
                    )
                    if raw:
                        df_db = pd.read_json(raw)
                        st.session_state["day_dfs"][d_idx] = df_db
                        st.success("DB에서 불러왔습니다!")
                    else:
                        st.warning("저장된 명단 없음")

            # 현재 로드된 데이터 초기화 버튼
            if d_idx in st.session_state["day_dfs"]:
                if st.button(f"🗑️ {d_idx}일차 초기화", key=f"clear_{d_idx}"):
                    del st.session_state["day_dfs"][d_idx]
                    st.rerun()

        # ── 최종 df 결정: session_state > 샘플 ──
        df = st.session_state["day_dfs"].get(d_idx, None)

        if df is None:
            df = pd.DataFrame({
                "name": [f"교사{i:02d}" for i in range(1, 11)],
                "role": ["정부"] * 8 + ["부만"] * 2,
                "exclude": [""] * 10,
                "extra_classes": [""] * 10,
                "priority": [None] * 10,
            })
            with col_preview:
                st.caption("📋 샘플 데이터 — CSV 업로드 또는 구글 시트 URL을 입력하면 교체됩니다")
        else:
            with col_preview:
                st.caption(f"✅ {len(df)}명 로드됨")

        # 열 보정
        for col, default in [("role", "정부"), ("exclude", ""), ("extra_classes", ""), ("priority", None)]:
            if col not in df.columns:
                df[col] = default

        with col_preview:
            st.dataframe(df, use_container_width=True, height=250)

        # DB 저장 버튼
        if db_connected and st.session_state.get("current_session_id"):
            if st.button(f"💾 {d_idx}일차 명단 DB 저장", key=f"db_save_{d_idx}"):
                db.save_day_teachers(
                    supabase,
                    st.session_state["current_session_id"],
                    d_idx,
                    df.to_json(orient="records", force_ascii=False),
                )
                st.success("저장 완료!")

        day_teacher_dfs.append(df)

# ══════════════════════════════════════════════════════════════
# 섹션 2 · 자동 배정 실행
# ══════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("② 자동 배정 실행")

# 이전 누적 통계 불러오기 (DB 연결 시)
prev_counts: dict = {}
if db_connected and st.session_state.get("current_session_id"):
    prev_counts = db.load_cumulative_stats(
        supabase, st.session_state["current_session_id"]
    )

run_btn = st.button("🚀 배정 시작", type="primary", use_container_width=True)

# session_state 초기화
if "assignments" not in st.session_state:
    st.session_state["assignments"] = {}
if "all_teachers" not in st.session_state:
    st.session_state["all_teachers"] = []
if "stat_rows" not in st.session_state:
    st.session_state["stat_rows"] = []

if run_btn:
    all_assignments: dict = {}
    all_teachers_union: list[Teacher] = []
    seen_names: set[str] = set()

    # ── 날별 교사 리스트 구성 ──
    day_teacher_lists: list[list[Teacher]] = []
    for d_idx, df in enumerate(day_teacher_dfs, start=1):
        if df is None or df.empty:
            day_teacher_lists.append([])
            continue
        tlist = build_teachers(df)
        day_teacher_lists.append(tlist)
        for t in tlist:
            if t.name not in seen_names:
                all_teachers_union.append(t)
                seen_names.add(t.name)

    # ── 날별 배정 실행 ──
    for d_idx in range(1, num_days + 1):
        tlist = day_teacher_lists[d_idx - 1]

        # 이 날의 교시 설정 (1일치 리스트, shape: [[p_grade1, p_grade2, ...]])
        single_day_periods = [periods_by_day_grade[d_idx - 1]]
        max_p = max(single_day_periods[0]) if single_day_periods[0] else 0

        if not tlist:
            # 교사 없으면 미배정으로 빈 슬롯 채우기
            for p in range(1, max_p + 1):
                inner = {}
                for g in range(1, num_grades + 1):
                    if single_day_periods[0][g - 1] >= p:
                        for c in range(1, classes_per_grade + 1):
                            inner[(g, c)] = ("(미배정)", "(미배정)")
                all_assignments[(d_idx, p)] = inner
            continue

        result = run_assignment(
            teachers=tlist,
            num_days=1,
            num_grades=num_grades,
            classes_per_grade=classes_per_grade,
            periods_by_day_grade=single_day_periods,
            prev_counts=prev_counts,
        )
        # result key: (1, p) → (d_idx, p) 로 변환
        for (_, p), per_slot in result.items():
            all_assignments[(d_idx, p)] = per_slot

    st.session_state["assignments"] = all_assignments
    st.session_state["all_teachers"] = all_teachers_union

    # 통계 계산
    stat_rows = compute_stats(all_assignments, all_teachers_union, prev_counts)
    st.session_state["stat_rows"] = stat_rows

    # DB 저장
    if db_connected:
        sid = st.session_state.get("current_session_id")
        if not sid and st.session_state.get("current_session_name"):
            meta = {
                "num_days": num_days,
                "num_grades": num_grades,
                "classes_per_grade": classes_per_grade,
            }
            sid = db.create_session(
                supabase,
                st.session_state["current_session_name"],
                meta,
            )
            if sid:
                st.session_state["current_session_id"] = sid
        if sid:
            db.save_assignments(supabase, sid, db.assignments_to_json(all_assignments))
            db.save_cumulative_stats(supabase, sid, stat_rows)
            st.success(f"✅ 배정 완료 & DB 저장 (세션: {st.session_state['current_session_name']})")
        else:
            st.success("✅ 배정 완료 (로컬)")
    else:
        st.success("✅ 배정 완료 (로컬 모드)")


# ══════════════════════════════════════════════════════════════
# DB에서 기존 배정 불러오기
# ══════════════════════════════════════════════════════════════
if db_connected and st.session_state.get("current_session_id") and not st.session_state["assignments"]:
    if st.button("☁️ 기존 배정 결과 DB에서 불러오기"):
        loaded = db.load_assignments(supabase, st.session_state["current_session_id"])
        if loaded:
            st.session_state["assignments"] = loaded
            st.success("불러왔습니다!")
        else:
            st.info("저장된 배정 결과 없음")

# ══════════════════════════════════════════════════════════════
# 섹션 3 · 시각화 (일차별 탭)
# ══════════════════════════════════════════════════════════════
assignments: dict = st.session_state.get("assignments", {})

if assignments:
    st.markdown("---")
    st.subheader("③ 일차별 시험 시간표")

    tabs = st.tabs([f"{d}일차" for d in range(1, num_days + 1)])
    tables_edited: dict[tuple, pd.DataFrame] = {}

    for d_idx, tab in enumerate(tabs, start=1):
        with tab:
            for g in range(1, num_grades + 1):
                p_cnt = int(periods_by_day_grade[d_idx - 1][g - 1])
                if p_cnt == 0:
                    continue

                cols_hdr = [f"{g}-{c}반" for c in range(1, classes_per_grade + 1)]
                idx_labels = []
                for p in range(1, p_cnt + 1):
                    idx_labels += [f"P{p} 정감독", f"P{p} 부감독"]

                table = pd.DataFrame("", index=idx_labels, columns=cols_hdr)
                for p in range(1, p_cnt + 1):
                    per_slot = assignments.get((d_idx, p), {})
                    for c in range(1, classes_per_grade + 1):
                        chief, asst = per_slot.get((g, c), ("", ""))
                        table.loc[f"P{p} 정감독", f"{g}-{c}반"] = chief if chief != "(미배정)" else ""
                        table.loc[f"P{p} 부감독", f"{g}-{c}반"] = asst if asst != "(미배정)" else ""

                st.markdown(f"#### {g}학년 ({p_cnt}교시)")
                edited = st.data_editor(
                    table,
                    key=f"edit_{d_idx}_{g}",
                    use_container_width=True,
                    hide_index=False,
                    num_rows="fixed",
                )
                tables_edited[(d_idx, g)] = edited

    # 수정된 표 → assignments 재구성
    if tables_edited:
        new_assignments: dict = {}
        for (d, g), ed in tables_edited.items():
            for row_label, row in ed.iterrows():
                # 행 레이블: "P1 정감독" / "P1 부감독"
                parts = row_label.split(" ")
                if len(parts) < 2:
                    continue
                try:
                    p = int(parts[0].replace("P", ""))
                except ValueError:
                    continue
                role = "chief" if "정" in parts[1] else "assistant"
                for col_label, val in row.items():
                    # 열: "1-3반"
                    col_clean = col_label.replace("반", "")
                    try:
                        g_str, c_str = col_clean.split("-")
                        col_g, col_c = int(g_str), int(c_str)
                    except ValueError:
                        continue
                    if col_g != g:
                        continue

                    name = str(val).strip() if isinstance(val, str) else ""
                    if not name:
                        name = "(미배정)"

                    key = (d, p)
                    if key not in new_assignments:
                        new_assignments[key] = {}
                    current = new_assignments[key].get((col_g, col_c), ("(미배정)", "(미배정)"))
                    chief_v, asst_v = current
                    if role == "chief":
                        chief_v = name
                    else:
                        asst_v = name
                    new_assignments[key][(col_g, col_c)] = (chief_v, asst_v)

        # 편집 안된 슬롯은 원본 유지
        for key, per_slot in assignments.items():
            if key not in new_assignments:
                new_assignments[key] = per_slot
            else:
                for gc, val in per_slot.items():
                    if gc not in new_assignments[key]:
                        new_assignments[key][gc] = val

        st.session_state["assignments"] = new_assignments
        assignments = new_assignments

    # ── 수정 내용 DB 반영 버튼 ──
    if db_connected and st.session_state.get("current_session_id"):
        if st.button("💾 수정 내용 DB에 저장", key="save_edited"):
            db.save_assignments(
                supabase,
                st.session_state["current_session_id"],
                db.assignments_to_json(assignments),
            )
            st.success("저장 완료!")

# ══════════════════════════════════════════════════════════════
# 섹션 4 · 배정 통계
# ══════════════════════════════════════════════════════════════
if assignments and st.session_state.get("all_teachers"):
    st.markdown("---")
    st.subheader("④ 배정 통계 & 검증")

    all_teachers: list[Teacher] = st.session_state["all_teachers"]
    stat_rows = compute_stats(assignments, all_teachers, prev_counts)
    st.session_state["stat_rows"] = stat_rows

    stat_df = pd.DataFrame(stat_rows)

    # 정렬
    stat_df["_prio"] = pd.to_numeric(stat_df["priority"], errors="coerce").fillna(1e9)
    stat_df = stat_df.sort_values(["_prio", "name"]).drop(columns=["_prio"])

    # 컬럼 순서
    col_order = [
        "name", "role", "priority",
        "정감독(이전)", "정감독(금번)", "정감독(합계)",
        "부감독(이전)", "부감독(금번)", "부감독(합계)",
        "총합계",
    ]
    stat_df = stat_df.reindex(columns=col_order)

    # 색상 하이라이트 (총합계 기준)
    def highlight_total(val):
        if not stat_df["총합계"].empty:
            avg = stat_df["총합계"].mean()
            if val > avg * 1.2:
                return "background-color: #ffcccc"
            elif val < avg * 0.8:
                return "background-color: #ccffcc"
        return ""

    try:
        styled = stat_df.style.map(highlight_total, subset=["총합계"])
    except AttributeError:
        # pandas < 2.1 fallback
        styled = stat_df.style.applymap(highlight_total, subset=["총합계"])
    st.dataframe(styled, use_container_width=True)

    # 위반 검사
    teacher_map = {t.name: t for t in all_teachers}
    violations = check_violations(assignments, teacher_map)
    if violations:
        st.error(f"⚠️ 제외 조건 위반 {len(violations)}건")
        st.dataframe(pd.DataFrame(violations))
    else:
        st.success("✅ 제외 조건 위반 없음")

# ══════════════════════════════════════════════════════════════
# 섹션 5 · Excel 다운로드
# ══════════════════════════════════════════════════════════════
if assignments:
    st.markdown("---")
    st.subheader("⑤ 결과 내보내기 (Excel)")

    excel_buf = BytesIO()
    with pd.ExcelWriter(excel_buf, engine="xlsxwriter") as writer:
        workbook = writer.book

        # ── 서식 ──
        fmt_header = workbook.add_format({
            "bold": True, "bg_color": "#4472C4", "font_color": "white",
            "border": 1, "align": "center", "valign": "vcenter",
        })
        fmt_chief = workbook.add_format({
            "bg_color": "#DDEEFF", "border": 1, "align": "center",
        })
        fmt_asst = workbook.add_format({
            "bg_color": "#EEFFDD", "border": 1, "align": "center",
        })
        fmt_unassigned = workbook.add_format({
            "bg_color": "#FFDDDD", "border": 1, "align": "center", "font_color": "#999999",
        })
        fmt_grade_title = workbook.add_format({
            "bold": True, "bg_color": "#F2F2F2", "border": 1,
            "align": "left", "valign": "vcenter",
        })

        for d in range(1, num_days + 1):
            ws_name = f"{d}일차"
            worksheet = workbook.add_worksheet(ws_name)
            writer.sheets[ws_name] = worksheet
            worksheet.set_column(0, 0, 12)  # 행 레이블 열

            start_row = 0
            for g in range(1, num_grades + 1):
                p_cnt = int(periods_by_day_grade[d - 1][g - 1])
                if p_cnt == 0:
                    continue

                num_cols = classes_per_grade + 1  # 레이블 포함

                # 학년 제목 행
                worksheet.merge_range(
                    start_row, 0, start_row, num_cols - 1,
                    f"{g}학년 (교시수: {p_cnt})",
                    fmt_grade_title,
                )
                start_row += 1

                # 헤더 행 (반 번호)
                worksheet.write(start_row, 0, "교시/역할", fmt_header)
                for c in range(1, classes_per_grade + 1):
                    worksheet.write(start_row, c, f"{g}-{c}반", fmt_header)
                    worksheet.set_column(c, c, 10)
                start_row += 1

                # 데이터 행
                for p in range(1, p_cnt + 1):
                    per_slot = assignments.get((d, p), {})
                    # 정감독 행
                    worksheet.write(start_row, 0, f"P{p} 정감독", fmt_header)
                    for c in range(1, classes_per_grade + 1):
                        chief, _ = per_slot.get((g, c), ("(미배정)", "(미배정)"))
                        fmt = fmt_unassigned if chief == "(미배정)" else fmt_chief
                        worksheet.write(start_row, c, chief, fmt)
                    start_row += 1
                    # 부감독 행
                    worksheet.write(start_row, 0, f"P{p} 부감독", fmt_header)
                    for c in range(1, classes_per_grade + 1):
                        _, asst = per_slot.get((g, c), ("(미배정)", "(미배정)"))
                        fmt = fmt_unassigned if asst == "(미배정)" else fmt_asst
                        worksheet.write(start_row, c, asst, fmt)
                    start_row += 1

                start_row += 1  # 학년 간 여백

        # 통계 시트 (with 블록 안에 있어야 writer 사용 가능)
        if st.session_state.get("stat_rows"):
            sdf = pd.DataFrame(st.session_state["stat_rows"])
            _col_order = [
                "name", "role", "priority",
                "정감독(이전)", "정감독(금번)", "정감독(합계)",
                "부감독(이전)", "부감독(금번)", "부감독(합계)",
                "총합계",
            ]
            sdf = sdf.reindex(columns=_col_order)
            sdf.to_excel(writer, sheet_name="통계", index=False)
    # with 블록이 닫히며 자동 저장됨 → getvalue() 호출
    excel_value = excel_buf.getvalue()
    fname = f"exam_schedule_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    st.download_button(
        label="📥 Excel 다운로드",
        data=excel_value,
        file_name=fname,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

# ══════════════════════════════════════════════════════════════
# 푸터
# ══════════════════════════════════════════════════════════════
st.markdown("---")
st.caption(
    "📌 **공유 방법**: 이 앱 URL을 그대로 공유하면 동일 DB를 바라보므로 "
    "같은 세션을 선택하면 최신 배정 결과를 함께 볼 수 있습니다. "
    "| **편집 동기화**: 수정 후 '💾 수정 내용 DB에 저장' → 상대방 새로고침"
)
