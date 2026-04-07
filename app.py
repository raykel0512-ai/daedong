# app.py — 시험 시감 자동 편성 v3.5
import streamlit as st, pandas as pd, re
from collections import defaultdict
from io import BytesIO
from scheduler import build_teachers, run_assignment, compute_teacher_stats, compute_parent_stats
import db

st.set_page_config(page_title="시험 시감 자동 편성 v3.5", layout="wide")
st.title("🧮 시험 시감 자동 편성 v3.5")
st.caption("교사: 제외(Exclude) 방식 | 학부모: 가능날(Available) 방식 | 롤링 배정 시스템 | 백지연쌤 화이팅! 👍👍")

# ══════════════════════════════════════════════════════════════
# 사이드바 설정
# ══════════════════════════════════════════════════════════════
with st.sidebar:
    st.header("⚙️ 기본 설정")
    num_days          = int(st.number_input("시험 일수", 1, 10, 4))
    num_grades        = int(st.number_input("학년 수", 1, 6, 3))
    classes_per_grade = int(st.number_input("학급 수", 1, 30, 8))

    st.subheader("일차별·학년별 교시 수")
    periods_by_day_grade = []
    for d in range(1, num_days + 1):
        with st.expander(f"{d}일차", expanded=(d == 1)):
            p_grade = [
                int(st.number_input(f"{g}학년 교시", 0, 10, 2, key=f"p_{d}_{g}"))
                for g in range(1, num_grades + 1)
            ]
            periods_by_day_grade.append(p_grade)

    st.markdown("---")
    st.header("🔗 구글 시트 URL 설정")
    st.caption("공유 설정: **링크가 있는 모든 사용자 → 뷰어**")
    raw_sheet_url = st.text_input("구글 시트 URL", placeholder="https://docs.google.com/spreadsheets/d/...")
    teacher_gid   = st.text_input("교사 명단 탭 GID", "0")
    parent_gid    = st.text_input("학부모 명단 탭 GID (없으면 빈칸)", "")

    st.markdown("---")
    st.caption(
        "**열 이름 안내**\n\n"
        "교사 시트: `name` / `exclude` / `extra_classes` / `priority`\n\n"
        "학부모 시트: `name` / `available` / `extra_classes` / `priority`\n\n"
        "exclude 예: `D1P2` `D3` `1-3` `D2P1@2-4`\n\n"
        "available 예: `D1` `D2;D4`"
    )

# ══════════════════════════════════════════════════════════════
# 데이터 로드 함수
# ══════════════════════════════════════════════════════════════
def get_csv_url(url: str, gid: str) -> str | None:
    if not url or not gid:
        return None
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        return None
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid={gid}"

@st.cache_data(ttl=60)
def load_all_data(url: str, t_gid: str, p_gid: str):
    t_url = get_csv_url(url, t_gid)
    p_url = get_csv_url(url, p_gid) if p_gid.strip() else None
    try:
        t_df = pd.read_csv(t_url) if t_url else pd.DataFrame()
    except Exception as e:
        st.error(f"교사 시트 로드 실패: {e}")
        t_df = pd.DataFrame()
    try:
        p_df = pd.read_csv(p_url) if p_url else pd.DataFrame()
    except Exception as e:
        st.error(f"학부모 시트 로드 실패: {e}")
        p_df = pd.DataFrame()
    if not t_df.empty:
        t_df.columns = [c.strip().lower() for c in t_df.columns]
    if not p_df.empty:
        p_df.columns = [c.strip().lower() for c in p_df.columns]
    return t_df, p_df

# session_state 초기화
if "assignments"  not in st.session_state: st.session_state["assignments"]  = {}
if "all_teachers" not in st.session_state: st.session_state["all_teachers"] = []

# ══════════════════════════════════════════════════════════════
# ① 명단 데이터 확인
# ══════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("① 명단 데이터 확인")

t_df, p_df = pd.DataFrame(), pd.DataFrame()

if raw_sheet_url:
    t_df, p_df = load_all_data(raw_sheet_url, teacher_gid, parent_gid)

    col_reload, _ = st.columns([1, 4])
    with col_reload:
        if st.button("🔄 시트 새로고침", help="캐시를 비우고 다시 불러옵니다"):
            st.cache_data.clear()
            st.rerun()

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**👨‍🏫 교사 명단 (Exclude 방식)**")
        if not t_df.empty:
            for col in ["exclude", "extra_classes", "priority"]:
                if col not in t_df.columns: t_df[col] = ""
            show_cols = [c for c in ["name", "exclude", "extra_classes", "priority"] if c in t_df.columns]
            st.dataframe(t_df[show_cols], use_container_width=True, height=250)
            st.caption(f"총 {len(t_df)}명")
        else:
            st.info("교사 시트를 불러오지 못했습니다.")

    with c2:
        st.markdown("**👥 학부모 명단 (Available 방식)**")
        if not p_df.empty:
            for col in ["available", "extra_classes", "priority"]:
                if col not in p_df.columns: p_df[col] = ""
            show_cols = [c for c in ["name", "available", "extra_classes", "priority"] if c in p_df.columns]
            st.dataframe(p_df[show_cols], use_container_width=True, height=250)
            st.caption(f"총 {len(p_df)}명")
        else:
            st.info("학부모 GID를 입력하면 학부모 명단도 표시됩니다.")
else:
    st.info("사이드바에 구글 시트 URL을 입력하면 명단이 표시됩니다.")

# ══════════════════════════════════════════════════════════════
# ② 자동 배정
# ══════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("② 자동 배정 실행")

if st.button("🚀 배정 시작", type="primary", use_container_width=True):
    if t_df.empty:
        st.error("교사 명단을 먼저 불러오세요.")
    else:
        with st.spinner("배정 중..."):
            teachers = build_teachers(t_df, p_df, num_days=num_days)
            asgn = run_assignment(
                teachers, num_days, num_grades,
                classes_per_grade, periods_by_day_grade,
            )
        st.session_state["all_teachers"] = teachers
        st.session_state["assignments"]  = asgn

        # 미배정 체크
        miss = sum(
            1 for ps in asgn.values()
            for (ch, ass) in ps.values()
            if ch == "(미배정)" or ass == "(미배정)"
        )
        if miss:
            st.warning(f"⚠️ 배정 완료 (미배정 {miss}건 — 인원 부족 또는 제외 규칙 확인 필요)")
        else:
            st.success("✅ 배정 완료! 미배정 0건")

# ══════════════════════════════════════════════════════════════
# ③ 결과 확인 및 수동 수정
# ══════════════════════════════════════════════════════════════
asgn: dict = st.session_state.get("assignments", {})

if asgn:
    st.markdown("---")
    st.subheader("③ 결과 확인 및 수동 수정")

    # 탭: 일차별
    day_tabs = st.tabs([f"{d}일차" for d in range(1, num_days + 1)] + ["📊 통계"])

    for d in range(1, num_days + 1):
        with day_tabs[d - 1]:
            # 이 날의 최대 교시 수
            max_p_day = max(int(periods_by_day_grade[d - 1][g - 1]) for g in range(1, num_grades + 1))

            # 교시별로 출력 (1교시 → 1,2,3학년 / 2교시 → 1,2,3학년)
            for p in range(1, max_p_day + 1):
                st.markdown(f"#### 📌 {p}교시")

                for g in range(1, num_grades + 1):
                    p_cnt = int(periods_by_day_grade[d - 1][g - 1])
                    if p_cnt < p:
                        continue  # 이 학년은 이 교시가 없음

                    cols = [f"{g}-{c}반" for c in range(1, classes_per_grade + 1)]
                    idx  = [f"정감독", f"부감독"]

                    df_view = pd.DataFrame("", index=idx, columns=cols)
                    slot = asgn.get((d, p), {})
                    for c in range(1, classes_per_grade + 1):
                        pair = slot.get((g, c), ("(미배정)", "(미배정)"))
                        df_view.loc["정감독", f"{g}-{c}반"] = pair[0] if pair[0] != "(미배정)" else ""
                        df_view.loc["부감독", f"{g}-{c}반"] = pair[1] if pair[1] != "(미배정)" else ""

                    st.markdown(f"**{g}학년**")
                    edited = st.data_editor(
                        df_view,
                        key=f"editor_{d}_{p}_{g}",
                        use_container_width=True,
                        hide_index=False,
                        num_rows="fixed",
                    )

                    # 수동 수정 반영
                    for row_lbl, row_vals in edited.iterrows():
                        is_ch = "정" in row_lbl
                        for col_lbl, val in row_vals.items():
                            try:
                                c_num = int(col_lbl.replace("반","").split("-")[1])
                            except (ValueError, IndexError):
                                continue
                            if (d, p) not in asgn:
                                asgn[(d, p)] = {}
                            pair = list(asgn[(d, p)].get((g, c_num), ("(미배정)", "(미배정)")))
                            val_str = str(val).strip() if val else "(미배정)"
                            if is_ch: pair[0] = val_str
                            else:     pair[1] = val_str
                            asgn[(d, p)][(g, c_num)] = tuple(pair)

                st.markdown("---")

    # ── 통계 탭 ──
    with day_tabs[-1]:
        all_teachers = st.session_state.get("all_teachers", [])

        st.markdown("### 👨‍🏫 교사 통계")
        t_stats = compute_teacher_stats(asgn, all_teachers)
        if t_stats:
            t_stat_df = pd.DataFrame(t_stats)
            # 총합계 기준 색상 강조
            def highlight_total(val):
                if not t_stat_df["총합계"].empty:
                    avg = t_stat_df["총합계"].mean()
                    if val > avg * 1.15:
                        return "background-color: #ffcccc"
                    elif val < avg * 0.85:
                        return "background-color: #ccffcc"
                return ""
            try:
                styled = t_stat_df.style.map(highlight_total, subset=["총합계"])
            except AttributeError:
                styled = t_stat_df.style.applymap(highlight_total, subset=["총합계"])
            st.dataframe(styled, use_container_width=True)
        else:
            st.info("교사 통계 없음")

        st.markdown("### 👥 학부모 현황")
        p_stats = compute_parent_stats(asgn, all_teachers, num_days)
        if p_stats:
            st.dataframe(pd.DataFrame(p_stats), use_container_width=True)
        else:
            st.info("학부모 배정 없음 (학부모 GID 미입력 또는 배정 없음)")

    st.session_state["assignments"] = asgn

    # ── Excel 다운로드 ──
    st.markdown("---")
    st.subheader("④ 결과 내보내기")

    excel_buf = BytesIO()
    with pd.ExcelWriter(excel_buf, engine="xlsxwriter") as writer:
        wb = writer.book
        fmt_hdr   = wb.add_format({"bold": True, "bg_color": "#4472C4", "font_color": "white", "border": 1, "align": "center"})
        fmt_chief = wb.add_format({"bg_color": "#DDEEFF", "border": 1, "align": "center"})
        fmt_asst  = wb.add_format({"bg_color": "#EEFFDD", "border": 1, "align": "center"})
        fmt_miss  = wb.add_format({"bg_color": "#FFDDDD", "border": 1, "align": "center", "font_color": "#999999"})
        fmt_grade = wb.add_format({"bold": True, "bg_color": "#F2F2F2", "border": 1})

        for d in range(1, num_days + 1):
            ws = wb.add_worksheet(f"{d}일차")
            writer.sheets[f"{d}일차"] = ws
            ws.set_column(0, 0, 12)
            row = 0
            for g in range(1, num_grades + 1):
                p_cnt = int(periods_by_day_grade[d - 1][g - 1])
                if p_cnt == 0:
                    continue
                ws.merge_range(row, 0, row, classes_per_grade, f"{g}학년 (교시수: {p_cnt})", fmt_grade)
                row += 1
                ws.write(row, 0, "교시/역할", fmt_hdr)
                for c in range(1, classes_per_grade + 1):
                    ws.write(row, c, f"{g}-{c}반", fmt_hdr)
                    ws.set_column(c, c, 10)
                row += 1
                for p in range(1, p_cnt + 1):
                    slot = asgn.get((d, p), {})
                    ws.write(row, 0, f"P{p} 정감독", fmt_hdr)
                    for c in range(1, classes_per_grade + 1):
                        ch, _ = slot.get((g, c), ("(미배정)", ""))
                        ws.write(row, c, ch, fmt_miss if ch == "(미배정)" else fmt_chief)
                    row += 1
                    ws.write(row, 0, f"P{p} 부감독", fmt_hdr)
                    for c in range(1, classes_per_grade + 1):
                        _, ass = slot.get((g, c), ("", "(미배정)"))
                        ws.write(row, c, ass, fmt_miss if ass == "(미배정)" else fmt_asst)
                    row += 1
                row += 1

        # 통계 시트
        all_teachers = st.session_state.get("all_teachers", [])
        t_stats = compute_teacher_stats(asgn, all_teachers)
        if t_stats:
            pd.DataFrame(t_stats).to_excel(writer, sheet_name="교사통계", index=False)
        p_stats = compute_parent_stats(asgn, all_teachers, num_days)
        if p_stats:
            pd.DataFrame(p_stats).to_excel(writer, sheet_name="학부모현황", index=False)

    st.download_button(
        "📥 Excel 다운로드",
        data=excel_buf.getvalue(),
        file_name=f"exam_schedule_v34.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

# ── 푸터 ──
st.markdown("---")
st.caption("📌 URL 공유 시 같은 화면 공유 가능 | 수정 후 새로고침으로 동기화")
