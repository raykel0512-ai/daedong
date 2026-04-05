# app.py — 시험 시감 자동 편성 v3.1
import streamlit as st, pandas as pd, re
from collections import defaultdict
from io import BytesIO
from scheduler import build_teachers, run_assignment, compute_teacher_stats, compute_parent_stats
import db

st.set_page_config(page_title="시험 시감 자동 편성 v3.1", layout="wide")
st.title("🧮 시험 시감 자동 편성 v3.1")
st.caption("중복 배정 철저 차단 | 학부모 정감독 배정 방지 | 롤링 알고리즘 최적화")

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
    raw_sheet_url = st.text_input("구글 시트 URL", placeholder="https://docs.google.com/spreadsheets/d/...")
    teacher_gid = st.text_input("교사 명단 탭 GID", "0")
    parent_gid = st.text_input("학부모 명단 탭 GID", "")

def get_clean_csv_url(url, gid):
    if not url: return None
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m: return None
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid={gid}"

@st.cache_data(ttl=60)
def load_all_data(url, t_gid, p_gid):
    t_url = get_clean_csv_url(url, t_gid)
    p_url = get_clean_csv_url(url, p_gid) if p_gid else None
    
    # 교사 데이터 로드 및 역할 강제
    t_df = pd.read_csv(t_url) if t_url else pd.DataFrame()
    if not t_df.empty:
        t_df.columns = [c.strip().lower() for c in t_df.columns]
        t_df['role'] = "교사" # 교사 탭 데이터는 무조건 교사로 강제
    
    # 학부모 데이터 로드 및 역할 강제
    p_df = pd.read_csv(p_url) if p_url else pd.DataFrame()
    if not p_df.empty:
        p_df.columns = [c.strip().lower() for c in p_df.columns]
        p_df['role'] = "학부모" # 학부모 탭 데이터는 무조건 학부모로 강제
    
    return t_df, p_df

if "assignments" not in st.session_state: st.session_state["assignments"] = {}
if "all_teachers" not in st.session_state: st.session_state["all_teachers"] = []

st.markdown("---")
st.subheader("① 명단 데이터 확인")

if raw_sheet_url:
    t_df, p_df = load_all_data(raw_sheet_url, teacher_gid, parent_gid)
    c1, c2 = st.columns(2)
    with c1:
        st.write("👨‍🏫 **교사 명단** (정감독 가능)")
        if not t_df.empty:
            for col, val in [("available",""), ("exclude",""), ("extra_classes",""), ("priority",None)]:
                if col not in t_df.columns: t_df[col] = val
            st.dataframe(t_df, height=200)
    with c2:
        st.write("👥 **학부모 명단** (부감독 전용)")
        if not p_df.empty:
            for col, val in [("available",""), ("exclude",""), ("extra_classes",""), ("priority",None)]:
                if col not in p_df.columns: p_df[col] = val
            st.dataframe(p_df, height=200)

st.markdown("---")
st.subheader("② 자동 배정 실행")
if st.button("🚀 배정 시작", type="primary", use_container_width=True):
    if raw_sheet_url and not t_df.empty:
        combined = pd.concat([t_df, p_df], ignore_index=True)
        teachers = build_teachers(combined, num_days=num_days)
        st.session_state["all_teachers"] = teachers
        st.session_state["assignments"] = run_assignment(teachers, num_days, num_grades, classes_per_grade, periods_by_day_grade)
        st.success("배정이 완료되었습니다!")
    else: st.error("교사 명단을 먼저 불러와주세요.")

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
                # 수동 수정 반영
                for row_lbl, row_vals in edited.iterrows():
                    p_num, is_ch = int(row_lbl.split(" ")[0][1:]), "정" in row_lbl
                    for col_lbl, val in row_vals.items():
                        c_num = int(col_lbl.split("-")[1])
                        if (d, p_num) not in asgn: asgn[(d, p_num)] = {}
                        pair = list(asgn[(d, p_num)].get((g, c_num), ("(미배정)", "(미배정)")))
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
    st.download_button("📥 엑셀 다운로드", buf.getvalue(), "schedule_v31.xlsx")
