# app.py — 시험 시감 자동 편성 v4.1
import streamlit as st, pandas as pd, re, json
from collections import defaultdict
from io import BytesIO
import gspread 
from google.oauth2.service_account import Credentials 
from scheduler import (
    build_teachers, run_assignment, compute_teacher_stats, 
    compute_parent_stats, assignments_to_df, df_to_assignments
)

# --- 페이지 설정 및 인증 ---
st.set_page_config(page_title="시험 시감 자동 편성 v4.1", layout="wide")
st.title("🧮 시험 시감 자동 편성 v4.1")
st.caption("백지연쌤 화이팅! 💪 | 명단 즉시 로드 & 클라우드 복원 시스템")

def get_gspread_client():
    try:
        if "gcp_service_account" in st.secrets:
            info = json.loads(st.secrets["gcp_service_account"])
        else:
            with open("service_account.json") as f:
                info = json.load(f)
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(info, scopes=scope)
        return gspread.authorize(creds)
    except: return None

# --- 사이드바 ---
with st.sidebar:
    st.header("⚙️ 기본 설정")
    num_days = int(st.number_input("시험 일수", 1, 10, 4))
    num_grades = int(st.number_input("학년 수", 1, 6, 3))
    classes_per_grade = int(st.number_input("학급 수", 1, 30, 8))
    
    periods_by_day_grade = []
    for d in range(1, num_days + 1):
        with st.expander(f"{d}일차", expanded=(d==1)):
            p_grade = [int(st.number_input(f"{g}학년 교시", 0, 10, 2, key=f"p_{d}_{g}")) for g in range(1, num_grades + 1)]
            periods_by_day_grade.append(p_grade)

    st.markdown("---")
    st.header("🔗 시트 서버 설정")
    raw_sheet_url = st.text_input("구글 시트 URL", placeholder="https://docs.google.com/spreadsheets/d/...")
    teacher_gid = st.text_input("교사 명단 GID", "0")
    parent_gid = st.text_input("학부모 명단 GID", "")
    save_tab_name = st.text_input("저장용 탭 이름", "저장데이터")

# --- 유틸리티 ---
def get_csv_url(url, gid):
    if not url or not gid: return None
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m: return None
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid={gid}"

@st.cache_data(ttl=60)
def load_all_data(url, t_gid, p_gid):
    t_url = get_csv_url(url, t_gid)
    p_url = get_csv_url(url, p_gid) if p_gid.strip() else None
    try: t_df = pd.read_csv(t_url) if t_url else pd.DataFrame()
    except: t_df = pd.DataFrame()
    try: p_df = pd.read_csv(p_url) if p_url else pd.DataFrame()
    except: p_df = pd.DataFrame()
    if not t_df.empty: t_df.columns = [c.strip().lower() for c in t_df.columns]
    if not p_df.empty: p_df.columns = [c.strip().lower() for c in p_df.columns]
    return t_df, p_df

# --- 상태 관리 ---
if "assignments" not in st.session_state: st.session_state["assignments"] = {}
if "all_teachers" not in st.session_state: st.session_state["all_teachers"] = []

# --- ① 명단 로드 섹션 ---
st.markdown("---")
st.subheader("① 명단 데이터 불러오기")
if st.button("🔄 시트에서 명단 새로고침", use_container_width=True):
    t_df, p_df = load_all_data(raw_sheet_url, teacher_gid, parent_gid)
    st.session_state["t_df"], st.session_state["p_df"] = t_df, p_df
    st.success("명단을 최신 상태로 불러왔습니다!")

t_df = st.session_state.get("t_df", pd.DataFrame())
p_df = st.session_state.get("p_df", pd.DataFrame())

if not t_df.empty or not p_df.empty:
    c1, c2 = st.columns(2)
    with c1: st.write("👨‍🏫 교사 명단"); st.dataframe(t_df, height=150)
    with c2: st.write("👥 학부모 명단"); st.dataframe(p_df, height=150)

# --- ② 자동 배정 및 클라우드 관리 ---
st.markdown("---")
st.subheader("② 자동 배정 및 클라우드 관리")
col_run, col_save, col_load = st.columns([2, 1, 1])

with col_run:
    if st.button("🚀 자동 배정 시작", type="primary", use_container_width=True):
        if not t_df.empty:
            teachers = build_teachers(t_df, p_df, num_days=num_days)
            st.session_state["assignments"] = run_assignment(teachers, num_days, num_grades, classes_per_grade, periods_by_day_grade)
            st.session_state["all_teachers"] = teachers
            st.success("배정 완료!")
        else: st.error("교사 명단을 먼저 불러와주세요.")

with col_save:
    if st.button("☁️ 시트에 시간표 저장", use_container_width=True):
        client = get_gspread_client()
        if client and st.session_state["assignments"]:
            try:
                sh = client.open_by_url(raw_sheet_url)
                try: ws = sh.worksheet(save_tab_name)
                except: ws = sh.add_worksheet(title=save_tab_name, rows="1000", cols="10")
                df_save = assignments_to_df(st.session_state["assignments"])
                ws.clear()
                ws.update([df_save.columns.values.tolist()] + df_save.values.tolist())
                st.success("시간표 정보가 구글 시트에 저장되었습니다!")
            except Exception as e: st.error(f"저장 실패: {e}")

with col_load:
    if st.button("📂 저장된 시간표 불러오기", use_container_width=True):
        client = get_gspread_client()
        if client:
            try:
                sh = client.open_by_url(raw_sheet_url)
                ws = sh.worksheet(save_tab_name)
                df_load = pd.DataFrame(ws.get_all_records())
                st.session_state["assignments"] = df_to_assignments(df_load)
                if not st.session_state["all_teachers"]:
                    st.session_state["all_teachers"] = build_teachers(t_df, p_df, num_days=num_days)
                st.success("클라우드에서 시간표를 복원했습니다!")
            except: st.error("저장된 데이터를 찾을 수 없습니다.")

# --- ③ 결과 확인 및 수정 ---
asgn = st.session_state.get("assignments", {})
if asgn:
    st.markdown("---")
    day_tabs = st.tabs([f"{d}일차" for d in range(1, num_days + 1)] + ["📊 통계"])
    for d in range(1, num_days + 1):
        with day_tabs[d-1]:
            d_max_p = max(int(periods_by_day_grade[d-1][g-1]) for g in range(1, num_grades+1))
            for p in range(1, d_max_p + 1):
                st.markdown(f"#### 📌 {p}교시")
                for g in range(1, num_grades + 1):
                    if int(periods_by_day_grade[d-1][g-1]) < p: continue
                    cols = [f"{g}-{c}반" for c in range(1, classes_per_grade + 1)]
                    df_v = pd.DataFrame("", index=["정감독", "부감독"], columns=cols)
                    slot = asgn.get((d, p), {})
                    for c in range(1, classes_per_grade + 1):
                        pair = slot.get((g, c), ("(미배정)", "(미배정)"))
                        df_v.loc["정감독", f"{g}-{c}반"] = pair[0] if pair[0] != "(미배정)" else ""
                        df_v.loc["부감독", f"{g}-{c}반"] = pair[1] if pair[1] != "(미배정)" else ""
                    st.write(f"**{g}학년**")
                    edited = st.data_editor(df_v, key=f"ed_{d}_{p}_{g}", use_container_width=True)
                    for row_l, row_v in edited.iterrows():
                        is_ch = "정" in row_l
                        for col_l, val in row_v.items():
                            try:
                                c_n = int(col_l.replace("반","").split("-")[1])
                                if (d, p) not in asgn: asgn[(d, p)] = {}
                                pair = list(asgn[(d, p)].get((g, c_n), ("(미배정)", "(미배정)")))
                                val_s = str(val).strip() if val else "(미배정)"
                                if is_ch: pair[0] = val_s
                                else: pair[1] = val_s
                                asgn[(d, p)][(g, c_n)] = tuple(pair)
                            except: continue
    with day_tabs[-1]:
        all_t = st.session_state.get("all_teachers", [])
        st.write("### 교사 통계"); st.dataframe(pd.DataFrame(compute_teacher_stats(asgn, all_t)), use_container_width=True)
        st.write("### 학부모 현황"); st.dataframe(pd.DataFrame(compute_parent_stats(asgn, all_t, num_days)), use_container_width=True)

    # 엑셀 다운로드 (UI와 동일한 교시별 양식)
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        wb = writer.book
        f_h = wb.add_format({"bold":True,"bg_color":"#4472C4","font_color":"white","border":1,"align":"center"})
        f_c = wb.add_format({"bg_color":"#DDEEFF","border":1,"align":"center"})
        f_a = wb.add_format({"bg_color":"#EEFFDD","border":1,"align":"center"})
        f_m = wb.add_format({"bg_color":"#FFDDDD","font_color":"#999999","border":1,"align":"center"})
        f_p = wb.add_format({"bold":True,"bg_color":"#F2F2F2","border":1})
        for d in range(1, num_days + 1):
            ws = wb.add_worksheet(f"{d}일차"); ws.set_column(0, 0, 15); row_i = 0
            d_max_p = max(int(periods_by_day_grade[d-1][g-1]) for g in range(1, num_grades+1))
            for p in range(1, d_max_p + 1):
                ws.merge_range(row_i, 0, row_i, classes_per_grade, f"[{p}교시]", f_p); row_i += 1
                for g in range(1, num_grades + 1):
                    if int(periods_by_day_grade[d-1][g-1]) < p: continue
                    ws.write(row_i, 0, f"{g}학년", f_h)
                    for c in range(1, classes_per_grade + 1): ws.write(row_i, c, f"{g}-{c}반", f_h); ws.set_column(c, c, 10)
                    row_i += 1; ws.write(row_i, 0, "정감독", f_h); slot_p = asgn.get((d, p), {})
                    for c in range(1, classes_per_grade + 1):
                        name_ch = slot_p.get((g, c), ("(미배정)", ""))[0]
                        ws.write(row_i, c, name_ch, f_m if name_ch == "(미배정)" else f_c)
                    row_i += 1; ws.write(row_i, 0, "부감독", f_h)
                    for c in range(1, classes_per_grade + 1):
                        name_as = slot_p.get((g, c), ("", "(미배정)"))[1]
                        ws.write(row_i, c, name_as, f_m if name_as == "(미배정)" else f_a)
                    row_i += 2
                row_i += 1
    st.download_button("📥 Excel 다운로드", buf.getvalue(), "schedule_v41.xlsx", use_container_width=True)
