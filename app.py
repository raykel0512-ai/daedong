# app.py — 시험 시감 자동 편성 v3.8
import streamlit as st, pandas as pd, re
from collections import defaultdict
from io import BytesIO
from scheduler import build_teachers, run_assignment, compute_teacher_stats, compute_parent_stats

# ══════════════════════════════════════════════════════════════
# 페이지 설정
# ══════════════════════════════════════════════════════════════
st.set_page_config(page_title="시험 시감 자동 편성 v3.8", layout="wide")
st.title("🧮 시험 시감 자동 편성 v3.8")
st.caption("v3.8: 엑셀 양식 UI 동기화(교시별) | 부감독 동일반 배정 방지 | 백지연쌤 화이팅! 👍")

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
        "교사 시트: `name`, `exclude`, `extra_classes`, `priority`\n\n"
        "학부모 시트: `name`, `available`, `extra_classes`, `priority`"
    )

# ══════════════════════════════════════════════════════════════
# 데이터 로드 함수
# ══════════════════════════════════════════════════════════════
def get_csv_url(url: str, gid: str) -> str | None:
    if not url or not gid: return None
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m: return None
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid={gid}"

@st.cache_data(ttl=60)
def load_all_data(url: str, t_gid: str, p_gid: str):
    t_url = get_csv_url(url, t_gid)
    p_url = get_csv_url(url, p_gid) if p_gid.strip() else None
    try:
        t_df = pd.read_csv(t_url) if t_url else pd.DataFrame()
    except: t_df = pd.DataFrame()
    try:
        p_df = pd.read_csv(p_url) if p_url else pd.DataFrame()
    except: p_df = pd.DataFrame()
    
    if not t_df.empty: t_df.columns = [c.strip().lower() for c in t_df.columns]
    if not p_df.empty: p_df.columns = [c.strip().lower() for c in p_df.columns]
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
        if st.button("🔄 시트 새로고침"):
            st.cache_data.clear()
            st.rerun()

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**👨‍🏫 교사 명단**")
        if not t_df.empty:
            for col in ["exclude", "extra_classes", "priority"]:
                if col not in t_df.columns: t_df[col] = ""
            st.dataframe(t_df[["name", "exclude", "extra_classes", "priority"]], use_container_width=True, height=200)
        else: st.info("교사 시트를 불러오세요.")

    with c2:
        st.markdown("**👥 학부모 명단**")
        if not p_df.empty:
            for col in ["available", "extra_classes", "priority"]:
                if col not in p_df.columns: p_df[col] = ""
            st.dataframe(p_df[["name", "available", "extra_classes", "priority"]], use_container_width=True, height=200)

# ══════════════════════════════════════════════════════════════
# ② 자동 배정 실행
# ══════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("② 자동 배정 실행")

if st.button("🚀 배정 시작", type="primary", use_container_width=True):
    if t_df.empty:
        st.error("교사 명단을 먼저 확인하세요.")
    else:
        with st.spinner("배정 알고리즘 작동 중..."):
            teachers = build_teachers(t_df, p_df, num_days=num_days)
            asgn = run_assignment(teachers, num_days, num_grades, classes_per_grade, periods_by_day_grade)
        st.session_state["all_teachers"] = teachers
        st.session_state["assignments"]  = asgn
        st.success("배정 완료! 하단에서 결과를 확인하세요.")

# ══════════════════════════════════════════════════════════════
# ③ 결과 확인 및 수동 수정
# ══════════════════════════════════════════════════════════════
asgn = st.session_state.get("assignments", {})

if asgn:
    st.markdown("---")
    st.subheader("③ 결과 확인 및 수동 수정")
    day_tabs = st.tabs([f"{d}일차" for d in range(1, num_days + 1)] + ["📊 통계"])

    for d in range(1, num_days + 1):
        with day_tabs[d - 1]:
            max_p_day = max(int(periods_by_day_grade[d - 1][g - 1]) for g in range(1, num_grades + 1))
            
            for p in range(1, max_p_day + 1):
                st.markdown(f"#### 📌 {p}교시")
                for g in range(1, num_grades + 1):
                    if int(periods_by_day_grade[d - 1][g - 1]) < p: continue
                    
                    cols = [f"{g}-{c}반" for c in range(1, classes_per_grade + 1)]
                    df_view = pd.DataFrame("", index=["정감독", "부감독"], columns=cols)
                    slot = asgn.get((d, p), {})
                    for c in range(1, classes_per_grade + 1):
                        pair = slot.get((g, c), ("(미배정)", "(미배정)"))
                        df_view.loc["정감독", f"{g}-{c}반"] = pair[0] if pair[0] != "(미배정)" else ""
                        df_view.loc["부감독", f"{g}-{c}반"] = pair[1] if pair[1] != "(미배정)" else ""
                    
                    st.write(f"**{g}학년**")
                    edited = st.data_editor(df_view, key=f"editor_{d}_{p}_{g}", use_container_width=True)
                    
                    # 수동 수정 사항 세션 저장
                    for row_lbl, row_vals in edited.iterrows():
                        is_ch = "정" in row_lbl
                        for col_lbl, val in row_vals.items():
                            try:
                                c_num = int(col_lbl.split("-")[1].replace("반",""))
                                if (d, p) not in asgn: asgn[(d, p)] = {}
                                pair = list(asgn[(d, p)].get((g, c_num), ("(미배정)", "(미배정)")))
                                val_str = str(val).strip() if val else "(미배정)"
                                if is_ch: pair[0] = val_str
                                else:     pair[1] = val_str
                                asgn[(d, p)][(g, c_num)] = tuple(pair)
                            except: continue
                st.markdown("---")

    # 통계 탭
    with day_tabs[-1]:
        all_t = st.session_state.get("all_teachers", [])
        st.markdown("### 👨‍🏫 교사 통계 (정감독 횟수 균등 확인)")
        t_stats = pd.DataFrame(compute_teacher_stats(asgn, all_t))
        if not t_stats.empty:
            st.dataframe(t_stats, use_container_width=True)
        
        st.markdown("### 👥 학부모 현황 (일일 2회 제한 확인)")
        p_stats = pd.DataFrame(compute_parent_stats(asgn, all_t, num_days))
        if not p_stats.empty:
            st.dataframe(p_stats, use_container_width=True)

    st.session_state["assignments"] = asgn

    # ══════════════════════════════════════════════════════════════
    # ④ 엑셀 다운로드 (UI 동기화 양식)
    # ══════════════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("④ 결과 내보내기")

    excel_buf = BytesIO()
    with pd.ExcelWriter(excel_buf, engine="xlsxwriter") as writer:
        wb = writer.book
        # 서식 정의
        f_hdr = wb.add_format({"bold":True, "bg_color":"#4472C4", "font_color":"white", "border":1, "align":"center"})
        f_ch  = wb.add_format({"bg_color":"#DDEEFF", "border":1, "align":"center"})
        f_as  = wb.add_format({"bg_color":"#EEFFDD", "border":1, "align":"center"})
        f_mi  = wb.add_format({"bg_color":"#FFDDDD", "font_color":"#999999", "border":1, "align":"center"})
        f_p   = wb.add_format({"bold":True, "bg_color":"#F2F2F2", "border":1, "align":"left"})

        for d in range(1, num_days + 1):
            ws = wb.add_worksheet(f"{d}일차")
            ws.set_column(0, 0, 15)
            row_idx = 0
            
            day_max_p = max(int(periods_by_day_grade[d-1][g-1]) for g in range(1, num_grades+1))
            
            for p in range(1, day_max_p + 1):
                # 교시 제목
                ws.merge_range(row_idx, 0, row_idx, classes_per_grade, f"[{p}교시]", f_p)
                row_idx += 1
                
                for g in range(1, num_grades + 1):
                    if int(periods_by_day_grade[d-1][g-1]) < p: continue
                    
                    # 헤더 (학년 및 반 번호)
                    ws.write(row_idx, 0, f"{g}학년", f_hdr)
                    for c in range(1, classes_per_grade + 1):
                        ws.write(row_idx, c, f"{g}-{c}반", f_hdr)
                        ws.set_column(c, c, 10)
                    row_idx += 1
                    
                    # 정감독 데이터
                    ws.write(row_idx, 0, "정감독", f_hdr)
                    slot_p = asgn.get((d, p), {})
                    for c in range(1, classes_per_grade + 1):
                        name_ch = slot_p.get((g, c), ("(미배정)", ""))[0]
                        ws.write(row_idx, c, name_ch, f_mi if name_ch == "(미배정)" else f_ch)
                    row_idx += 1
                    
                    # 부감독 데이터
                    ws.write(row_idx, 0, "부감독", f_hdr)
                    for c in range(1, classes_per_grade + 1):
                        name_as = slot_p.get((g, c), ("", "(미배정)"))[1]
                        ws.write(row_idx, c, name_as, f_mi if name_as == "(미배정)" else f_as)
                    row_idx += 2 # 학년 간 간격
                row_idx += 1 # 교시 간 간격

        # 통계 시트 추가
        t_stats.to_excel(writer, sheet_name="교사통계", index=False)
        p_stats.to_excel(writer, sheet_name="학부모현황", index=False)

    st.download_button(
        label="📥 Excel 결과 다운로드 (교시별 양식)",
        data=excel_buf.getvalue(),
        file_name=f"exam_schedule_v38.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )
