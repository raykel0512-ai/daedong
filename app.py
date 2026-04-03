# app.py — 시험 시감 자동 편성 v2.3
import json, re, pandas as pd, streamlit as st
from collections import defaultdict
from datetime import datetime
from io import BytesIO
from scheduler import Teacher, build_teachers, run_assignment, compute_stats, check_violations
import db

st.set_page_config(page_title="시험 시감 자동 편성 v2.3", layout="wide")
st.title("🧮 시험 시감 자동 편성 v2.3")
st.caption("연강 방지 · 하루 최대 횟수 제한 · 개인 시간표 조회 · 지정석 우선 배정")

supabase = db.get_client()
db_connected = supabase is not None
if not db_connected:
    st.info("💡 로컬 세션 모드 (DB 연결 없음)", icon="ℹ️")

with st.sidebar:
    st.header("⚙️ 기본 설정")
    if db_connected:
        sessions = db.list_sessions(supabase)
        selected_session_name = st.selectbox("세션 선택", ["+ 새 세션 만들기"] + [s["name"] for s in sessions])
        if selected_session_name == "+ 새 세션 만들기":
            new_name = st.text_input("새 세션 이름", placeholder="2024-1학기 기말고사")
            st.session_state["current_session_id"], st.session_state["current_session_name"] = None, new_name
        else:
            sel = next((s for s in sessions if s["name"] == selected_session_name), None)
            st.session_state["current_session_id"], st.session_state["current_session_name"] = sel["id"], sel["name"]

    num_days = int(st.number_input("시험 일수", 1, 10, 4))
    num_grades = int(st.number_input("학년 수", 1, 6, 3))
    classes_per_grade = int(st.number_input("학급 수", 1, 30, 8))
    
    st.subheader("일차별·학년별 교시 수")
    periods_by_day_grade = []
    for d in range(1, num_days + 1):
        with st.expander(f"{d}일차", expanded=(d == 1)):
            per_grade = [int(st.number_input(f"{g}학년 교시", 0, 10, 2, key=f"p_{d}_{g}")) for g in range(1, num_grades + 1)]
            periods_by_day_grade.append(per_grade)

    st.markdown("---")
    st.subheader("🚀 배정 상세 옵션")
    max_per_day = st.slider("교사 1인당 하루 최대 시감", 1, 5, 3)
    avoid_consecutive = st.checkbox("연강(연속 교시) 방지", value=True)

if "day_dfs" not in st.session_state: st.session_state["day_dfs"] = {}
if "assignments" not in st.session_state: st.session_state["assignments"] = {}
if "all_teachers" not in st.session_state: st.session_state["all_teachers"] = []

st.markdown("---")
st.subheader("① 교사 명단 업로드")
def load_gsheet(url):
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m: return None
    gid = re.search(r"gid=(\d+)", url).group(1) if "gid=" in url else "0"
    csv_url = f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid={gid}"
    try:
        df = pd.read_csv(csv_url)
        df.columns = [c.strip().lower() for c in df.columns]
        return df
    except: return None

u_tabs = st.tabs([f"{d}일차" for d in range(1, num_days + 1)])
day_teacher_dfs = []
for d_idx, utab in enumerate(u_tabs, start=1):
    with utab:
        col1, col2 = st.columns([1, 2])
        with col1:
            method = st.radio("입력 방식", ["URL", "CSV"], key=f"m_{d_idx}", horizontal=True)
            if method == "URL":
                url = st.text_input("Google Sheet URL", key=f"u_{d_idx}")
                if st.button("불러오기", key=f"b_{d_idx}"):
                    df = load_gsheet(url)
                    if df is not None: st.session_state["day_dfs"][d_idx] = df
            else:
                f = st.file_uploader("CSV", type="csv", key=f"f_{d_idx}")
                if f: st.session_state["day_dfs"][d_idx] = pd.read_csv(f)
        
        df = st.session_state["day_dfs"].get(d_idx)
        if df is not None:
            for c, d in [("role","정부"),("available",""),("exclude",""),("extra_classes",""),("priority",None)]:
                if c not in df.columns: df[c] = d
            with col2: st.dataframe(df, height=150)
            day_teacher_dfs.append(df)
        else:
            day_teacher_dfs.append(None)

st.markdown("---")
st.subheader("② 자동 배정 실행")
if st.button("🚀 배정 시작", type="primary", use_container_width=True):
    all_asgn, all_t, seen = {}, [], set()
    prev_counts = db.load_cumulative_stats(supabase, st.session_state.get("current_session_id")) if db_connected else {}
    
    for d_idx, df in enumerate(day_teacher_dfs, start=1):
        if df is None: continue
        tlist = build_teachers(df, num_days=num_days)
        for t in tlist:
            if t.name not in seen: all_t.append(t); seen.add(t.name)
        res = run_assignment(tlist, 1, num_grades, classes_per_grade, [periods_by_day_grade[d_idx-1]], prev_counts, max_per_day, avoid_consecutive)
        for (_, p), slot in res.items(): all_asgn[(d_idx, p)] = slot
    
    st.session_state["assignments"], st.session_state["all_teachers"] = all_asgn, all_t
    if db_connected and st.session_state.get("current_session_id"):
        db.save_assignments(supabase, st.session_state["current_session_id"], db.assignments_to_json(all_asgn))
    st.success("배정 완료!")

asgn = st.session_state["assignments"]
if asgn:
    v_tabs = st.tabs(["일차별 시간표", "교사별 개인 시간표", "통계 및 검증"])
    
    with v_tabs[0]: # 일차별 시간표
        for d in range(1, num_days + 1):
            st.markdown(f"### {d}일차")
            for g in range(1, num_grades + 1):
                p_cnt = periods_by_day_grade[d-1][g-1]
                if p_cnt == 0: continue
                rows = []
                for p in range(1, p_cnt + 1):
                    for role_idx, role_name in enumerate(["정감독", "부감독"]):
                        r_data = {"교시/역할": f"P{p} {role_name}"}
                        for c in range(1, classes_per_grade + 1):
                            val = asgn.get((d, p), {}).get((g, c), ("(미배정)","(미배정)"))[role_idx]
                            r_data[f"{g}-{c}"] = val if val != "(미배정)" else ""
                        rows.append(r_data)
                st.write(f"**{g}학년**")
                st.dataframe(pd.DataFrame(rows).set_index("교시/역할"), use_container_width=True)

    with v_tabs[1]: # 교사별 개인 시간표
        t_names = sorted([t.name for t in st.session_state["all_teachers"]])
        sel_t = st.selectbox("교사 선택", t_names)
        if sel_t:
            p_rows = []
            for d in range(1, num_days + 1):
                p_data = {"일차": f"{d}일차"}
                for p in range(1, 5):
                    p_data[f"{p}교시"] = ""
                    for (gg, cc), (ch, ass) in asgn.get((d, p), {}).items():
                        if ch == sel_t: p_data[f"{p}교시"] = f"★정({gg}-{cc})"
                        elif ass == sel_t: p_data[f"{p}교시"] = f"부({gg}-{cc})"
                p_rows.append(p_data)
            st.table(pd.DataFrame(p_rows).set_index("일차"))

    with v_tabs[2]: # 통계
        stats = compute_stats(asgn, st.session_state["all_teachers"])
        st.dataframe(pd.DataFrame(stats), use_container_width=True)
        viols = check_violations(asgn, {t.name: t for t in st.session_state["all_teachers"]})
        if viols: st.error(f"위반 사항 {len(viols)}건 발생"); st.dataframe(viols)
        else: st.success("제외 조건 위반 없음")

    # 엑셀 다운로드
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        pd.DataFrame(compute_stats(asgn, st.session_state["all_teachers"])).to_excel(writer, sheet_name='통계')
    st.download_button("📥 전체 결과 엑셀 다운로드", output.getvalue(), "schedule.xlsx", "application/vnd.ms-excel")
