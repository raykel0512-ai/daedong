# app.py — 시험 시감 자동 편성 v2.7
import streamlit as st, pandas as pd, re
from io import BytesIO
from scheduler import build_teachers, run_assignment, compute_teacher_stats, compute_parent_stats
import db

st.set_page_config(page_title="시험 시감 자동 편성 v2.7", layout="wide")
st.title("🧮 시험 시감 자동 편성 v2.7")
st.caption("롤링(Rolling) 알고리즘 적용: 어제 마지막 감독자 다음 사람부터 오늘 감독 시작")

with st.sidebar:
    st.header("⚙️ 기본 설정")
    num_days = int(st.number_input("시험 일수", 1, 10, 4))
    num_grades = int(st.number_input("학년 수", 1, 6, 3))
    classes_per_grade = int(st.number_input("학급 수", 1, 30, 8))
    
    st.subheader("일차별·학년별 교시 수")
    periods_by_day_grade = []
    for d in range(1, num_days + 1):
        with st.expander(f"{d}일차"):
            per_grade = [int(st.number_input(f"{g}학년 교시", 0, 10, 2, key=f"p_{d}_{g}")) for g in range(1, num_grades + 1)]
            periods_by_day_grade.append(per_grade)

if "day_dfs" not in st.session_state: st.session_state["day_dfs"] = {}
if "assignments" not in st.session_state: st.session_state["assignments"] = {}
if "all_teachers" not in st.session_state: st.session_state["all_teachers"] = []

def load_gsheet(url):
    try:
        m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
        if not m: return None
        sheet_id, gid_m = m.group(1), re.search(r"gid=(\d+)", url)
        gid = gid_m.group(1) if gid_m else "0"
        csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
        df = pd.read_csv(csv_url)
        df.columns = [c.strip().lower() for c in df.columns]
        return df
    except: return None

st.markdown("---")
st.subheader("① 명단 업로드")
u_tabs = st.tabs([f"{d}일차" for d in range(1, num_days + 1)])
day_teacher_dfs = []
for d_idx, utab in enumerate(u_tabs, start=1):
    with utab:
        col1, col2 = st.columns([1, 2])
        with col1:
            method = st.radio(f"{d_idx}일차", ["URL", "CSV"], key=f"m_{d_idx}", horizontal=True)
            if method == "URL":
                url = st.text_input("URL", key=f"u_{d_idx}")
                if st.button("🔗 로드", key=f"b_{d_idx}"):
                    df = load_gsheet(url)
                    if df is not None: st.session_state["day_dfs"][d_idx] = df
            else:
                f = st.file_uploader("CSV", type="csv", key=f"f_{d_idx}")
                if f:
                    df = pd.read_csv(f)
                    df.columns = [c.strip().lower() for c in df.columns]
                    st.session_state["day_dfs"][d_idx] = df
        df = st.session_state["day_dfs"].get(d_idx)
        if df is not None:
            for c, dv in [("role","교사"), ("available",""), ("exclude",""), ("extra_classes",""), ("priority",None)]:
                if c not in df.columns: df[c] = dv
            with col2: st.dataframe(df, height=180)
            day_teacher_dfs.append(df)
        else: day_teacher_dfs.append(None)

st.markdown("---")
st.subheader("② 자동 배정 실행")
if st.button("🚀 배정 시작", type="primary", use_container_width=True):
    all_asgn, all_t, seen = {}, [], set()
    current_counts = {"chief": defaultdict(int), "asst": defaultdict(int)}
    last_idx = 0 # 롤링 시작점
    
    for d_idx in range(1, num_days + 1):
        df = st.session_state["day_dfs"].get(d_idx)
        if df is None: continue
        tlist = build_teachers(df, num_days=num_days)
        for t in tlist:
            if t.name not in seen: all_t.append(t); seen.add(t.name)
        
        # 일차별로 누적 횟수와 마지막 인덱스를 넘겨줌 (롤링 핵심)
        res, updated_counts, updated_last_idx = run_assignment(
            tlist, 1, num_grades, classes_per_grade, 
            [periods_by_day_grade[d_idx-1]], 
            prev_counts=current_counts,
            last_idx=last_idx
        )
        for (_, p), slot in res.items(): all_asgn[(d_idx, p)] = slot
        current_counts = updated_counts # 횟수 업데이트
        last_idx = updated_last_idx # 마지막 인덱스 업데이트
        
    st.session_state["assignments"], st.session_state["all_teachers"] = all_asgn, all_t
    st.success("롤링 배정 완료!")

if st.session_state["assignments"]:
    st.markdown("---")
    st.subheader("③ 시간표 및 통계")
    asgn = st.session_state["assignments"]
    v_tabs = st.tabs([f"{d}일차" for d in range(1, num_days + 1)] + ["📊 통계"])
    for d in range(1, num_days + 1):
        with v_tabs[d-1]:
            for g in range(1, num_grades + 1):
                p_cnt = periods_by_day_grade[d-1][g-1]
                if p_cnt == 0: continue
                labels = []
                for p in range(1, p_cnt + 1): labels.extend([f"P{p} 정", f"P{p} 부"])
                cols = [f"{g}-{c}" for c in range(1, classes_per_grade + 1)]
                df_edit = pd.DataFrame("", index=labels, columns=cols)
                for p in range(1, p_cnt + 1):
                    slot = asgn.get((d, p), {})
                    for c in range(1, classes_per_grade + 1):
                        ch, ass = slot.get((g, c), ("", ""))
                        df_edit.loc[f"P{p} 정", f"{g}-{c}"] = ch if ch != "(미배정)" else ""
                        df_edit.loc[f"P{p} 부", f"{g}-{c}"] = ass if ass != "(미배정)" else ""
                st.write(f"**{g}학년**")
                edited = st.data_editor(df_edit, key=f"e_{d}_{g}", use_container_width=True)
                for row_label, row in edited.iterrows():
                    p_num = int(row_label.split(" ")[0][1:])
                    is_chief = "정" in row_label
                    for col_label, val in row.items():
                        c_num = int(col_label.split("-")[1])
                        pair = list(asgn.get((d, p_num), {}).get((g, c_num), ("(미배정)", "(미배정)")))
                        val_str = str(val).strip() if val else "(미배정)"
                        if is_chief: pair[0] = val_str
                        else: pair[1] = val_str
                        if (d, p_num) not in asgn: asgn[(d, p_num)] = {}
                        asgn[(d, p_num)][(g, c_num)] = tuple(pair)
    with v_tabs[-1]:
        st.write("### 교사 누적 통계")
        st.dataframe(pd.DataFrame(compute_teacher_stats(asgn, st.session_state["all_teachers"])), use_container_width=True)
        st.write("### 학부모 일일 현황")
        st.dataframe(pd.DataFrame(compute_parent_stats(asgn, st.session_state["all_teachers"], num_days)), use_container_width=True)
    st.session_state["assignments"] = asgn
    out = BytesIO()
    with pd.ExcelWriter(out, engine='xlsxwriter') as wr:
        pd.DataFrame(compute_teacher_stats(asgn, st.session_state["all_teachers"])).to_excel(wr, sheet_name='교사', index=False)
        pd.DataFrame(compute_parent_stats(asgn, st.session_state["all_teachers"], num_days)).to_excel(wr, sheet_name='학부모', index=False)
    st.download_button("📥 엑셀 다운로드", out.getvalue(), "schedule.xlsx")
