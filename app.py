# app.py — 시험 시감 자동 편성 v3.2
import streamlit as st, pandas as pd, re
from collections import defaultdict
from io import BytesIO
from scheduler import build_teachers, run_assignment, compute_teacher_stats, compute_parent_stats
import db

st.set_page_config(page_title="시험 시감 자동 편성 v3.2", layout="wide")
st.title("🧮 시험 시감 자동 편성 v3.2")
st.caption("제외 규칙 통합(D1, D1P2) | 중복 원천 차단 | 롤링 배정 강화")

# ══════════════════════════════════════════════════════════════
# 사이드바 설정
# ══════════════════════════════════════════════════════════════
with st.sidebar:
    st.header("⚙️ 기본 설정")
    num_days = st.number_input("시험 일수", 1, 10, 4)
    num_grades = st.number_input("학년 수", 1, 6, 3)
    classes_per_grade = st.number_input("학급 수", 1, 30, 8)
    
    st.subheader("일차별·학년별 교시 수")
    periods_by_day_grade = []
    for d in range(1, num_days + 1):
        with st.expander(f"{d}일차"):
            p_grade = [int(st.number_input(f"{g}학년 교시", 0, 10, 2, key=f"p_{d}_{g}")) for g in range(1, num_grades + 1)]
            periods_by_day_grade.append(p_grade)

    st.markdown("---")
    st.header("🔗 구글 시트 URL 설정")
    raw_sheet_url = st.text_input("구글 시트 URL")
    teacher_gid = st.text_input("교사 명단 탭 GID", "0")
    parent_gid = st.text_input("학부모 명단 탭 GID", "")

# ══════════════════════════════════════════════════════════════
# 데이터 로드 로직
# ══════════════════════════════════════════════════════════════
def get_clean_csv_url(url, gid):
    if not url or not gid: return None
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m: return None
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid={gid}"

@st.cache_data(ttl=60)
def load_all_data(url, t_gid, p_gid):
    t_url, p_url = get_clean_csv_url(url, t_gid), get_clean_csv_url(url, p_gid) if p_gid else None
    t_df = pd.read_csv(t_url) if t_url else pd.DataFrame()
    p_df = pd.read_csv(p_url) if p_url else pd.DataFrame()
    for df, role in [(t_df, "교사"), (p_df, "학부모")]:
        if not df.empty:
            df.columns = [c.strip().lower() for c in df.columns]
            df['role'] = role
    return t_df, p_df

if "assignments" not in st.session_state: st.session_state["assignments"] = {}
if "all_teachers" not in st.session_state: st.session_state["all_teachers"] = []

# ══════════════════════════════════════════════════════════════
# ① 데이터 확인 (UI 단순화: exclude 중심으로 표시)
# ══════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("① 명단 데이터 확인")

if raw_sheet_url:
    t_df, p_df = load_all_data(raw_sheet_url, teacher_gid, parent_gid)
    c1, c2 = st.columns(2)
    with c1:
        st.write("👨‍🏫 **교사 명단** (exclude 예시: D1, D2P1, 1-3)")
        if not t_df.empty:
            for col in ["exclude", "extra_classes", "priority"]:
                if col not in t_df.columns: t_df[col] = ""
            st.dataframe(t_df[["name", "exclude", "extra_classes", "priority"]], height=200)
    with c2:
        st.write("👥 **학부모 명단** (exclude 예시: D3, 1-5)")
        if not p_df.empty:
            for col in ["exclude", "extra_classes", "priority"]:
                if col not in p_df.columns: p_df[col] = ""
            st.dataframe(p_df[["name", "exclude", "extra_classes", "priority"]], height=200)

# ══════════════════════════════════════════════════════════════
# ② 자동 배정
# ══════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("② 자동 배정 실행")
if st.button("🚀 배정 시작", type="primary", use_container_width=True):
    if not t_df.empty:
        combined = pd.concat([t_df, p_df], ignore_index=True)
        teachers = build_teachers(combined, num_days=num_days)
        st.session_state["all_teachers"] = teachers
        st.session_state["assignments"] = run_assignment(teachers, num_days, num_grades, classes_per_grade, periods_by_day_grade)
        st.success("배정 완료!")
    else: st.error("명단을 먼저 로드해주세요.")

# ══════════════════════════════════════════════════════════════
# ③ 결과 확인 및 수정
# ══════════════════════════════════════════════════════════════
asgn = st.session_state.get("assignments", {})
if asgn:
    st.markdown("---")
    st.subheader("③ 결과 확인 및 수동 수정")
    v_tabs = st.tabs([f"{d}일차" for d in range(1, num_days + 1)] + ["📊 통계"])
    for d in range(1, num_days + 1):
        with v_tabs[d-1]:
            for g in range(1, num_grades + 1):
                p_cnt = periods_by_day_grade[d-1][g-1]
                if p_cnt == 0: continue
                idx = []
                for p in range(1, p_cnt+1): idx.extend([f"P{p} 정", f"P{p} 부"])
                cols = [f"{g}-{c}" for c in range(1, classes_per_grade + 1)]
                df_view = pd.DataFrame("", index=idx, columns=cols)
                for p in range(1, p_cnt+1):
                    slot = asgn.get((d, p), {})
                    for c in range(1, classes_per_grade + 1):
                        pair = slot.get((g, c), ("(미배정)", "(미배정)"))
                        df_view.loc[f"P{p} 정", f"{g}-{c}"] = pair[0] if pair[0] != "(미배정)" else ""
                        df_view.loc[f"P{p} 부", f"{g}-{c}"] = pair[1] if pair[1] != "(미배정)" else ""
                st.write(f"**{g}학년**")
                edited = st.data_editor(df_view, key=f"editor_{d}_{g}", use_container_width=True)
                # 수정 반영 로직
                for row_lbl, row_vals in edited.iterrows():
                    p_num, is_ch = int(row_lbl.split(" ")[0][1:]), "정" in row_lbl
                    for col_lbl, val in row_vals.items():
                        c_num = int(col_lbl.split("-")[1])
                        pair = list(asgn.get((d, p_num), {}).get((g, c_num), ("(미배정)", "(미배정)")))
                        val_str = str(val).strip() if val else "(미배정)"
                        if is_ch: pair[0] = val_str
                        else: pair[1] = val_str
                        asgn[(d, p_num)][(g, c_num)] = tuple(pair)
    with v_tabs[-1]:
        st.write("### 👨‍🏫 교사 통계")
        st.dataframe(pd.DataFrame(compute_teacher_stats(asgn, st.session_state["all_teachers"])), use_container_width=True)
        st.write("### 👥 학부모 현황")
        st.dataframe(pd.DataFrame(compute_parent_stats(asgn, st.session_state["all_teachers"], num_days)), use_container_width=True)

    st.session_state["assignments"] = asgn
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as wr:
        pd.DataFrame(compute_teacher_stats(asgn, st.session_state["all_teachers"])).to_excel(wr, sheet_name='교사', index=False)
        pd.DataFrame(compute_parent_stats(asgn, st.session_state["all_teachers"], num_days)).to_excel(wr, sheet_name='학부모', index=False)
    st.download_button("📥 엑셀 다운로드", buf.getvalue(), "schedule_v32.xlsx")
