# app.py — 시험 시감 자동 편성 v2.4
import streamlit as st, pandas as pd, re
from io import BytesIO
from scheduler import build_teachers, run_assignment, compute_stats, check_violations
import db

st.set_page_config(page_title="시험 시감 자동 편성 v2.4", layout="wide")
st.title("🧮 시험 시감 자동 편성 v2.4")

supabase = db.get_client()
db_connected = supabase is not None

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

st.markdown("---")
st.subheader("① 교사 명단 업로드")
u_tabs = st.tabs([f"{d}일차" for d in range(1, num_days + 1)])
day_teacher_dfs = []
for d_idx, utab in enumerate(u_tabs, start=1):
    with utab:
        f = st.file_uploader(f"{d_idx}일차 CSV", type="csv", key=f"f_{d_idx}")
        if f: 
            df_in = pd.read_csv(f)
            df_in.columns = [c.strip().lower() for c in df_in.columns]
            st.session_state["day_dfs"][d_idx] = df_in
        
        df = st.session_state["day_dfs"].get(d_idx)
        if df is not None:
            for col, dval in [("role","정부"), ("available",""), ("exclude",""), ("extra_classes",""), ("priority",None)]:
                if col not in df.columns: df[col] = dval
            st.dataframe(df, height=150)
            day_teacher_dfs.append(df)
        else: day_teacher_dfs.append(None)

st.markdown("---")
st.subheader("② 자동 배정 실행")
if st.button("🚀 배정 시작", type="primary", use_container_width=True):
    all_asgn, all_t, seen = {}, [], set()
    for d_idx, df in enumerate(day_teacher_dfs, start=1):
        if df is None: continue
        tlist = build_teachers(df, num_days=num_days)
        for t in tlist:
            if t.name not in seen: all_t.append(t); seen.add(t.name)
        res = run_assignment(tlist, 1, num_grades, classes_per_grade, [periods_by_day_grade[d_idx-1]])
        for (_, p), slot in res.items(): all_asgn[(d_idx, p)] = slot
    st.session_state["assignments"] = all_asgn
    st.session_state["all_teachers"] = all_t
    st.success("배정 완료! 아래에서 수정 가능합니다.")

# ③ 결과 확인 및 수동 수정
if st.session_state["assignments"]:
    st.markdown("---")
    st.subheader("③ 시감 시간표 확인 및 수동 수정")
    st.info("💡 표 안의 이름을 직접 클릭하여 수정할 수 있습니다.")
    
    current_asgn = st.session_state["assignments"]
    tabs = st.tabs([f"{d}일차" for d in range(1, num_days + 1)])
    
    for d in range(1, num_days + 1):
        with tabs[d-1]:
            for g in range(1, num_grades + 1):
                p_cnt = periods_by_day_grade[d-1][g-1]
                if p_cnt == 0: continue
                
                # 표 데이터 구성
                labels = []
                for p in range(1, p_cnt + 1): labels.extend([f"P{p} 정감독", f"P{p} 부감독"])
                cols = [f"{g}-{c}반" for c in range(1, classes_per_grade + 1)]
                df_edit = pd.DataFrame("", index=labels, columns=cols)
                
                for p in range(1, p_cnt + 1):
                    slot = current_asgn.get((d, p), {})
                    for c in range(1, classes_per_grade + 1):
                        ch, ass = slot.get((g, c), ("", ""))
                        df_edit.loc[f"P{p} 정감독", f"{g}-{c}반"] = ch if ch != "(미배정)" else ""
                        df_edit.loc[f"P{p} 부감독", f"{g}-{c}반"] = ass if ass != "(미배정)" else ""
                
                st.write(f"**{g}학년**")
                edited_df = st.data_editor(df_edit, key=f"editor_{d}_{g}", use_container_width=True)
                
                # 수정된 내용 반영
                for row_idx, row in edited_df.iterrows():
                    parts = row_idx.split(" ")
                    p_num = int(parts[0][1:])
                    is_chief = "정" in parts[1]
                    for col_name, val in row.items():
                        c_num = int(col_name.split("-")[1].replace("반", ""))
                        if (d, p_num) not in current_asgn: current_asgn[(d, p_num)] = {}
                        prev_pair = list(current_asgn[(d, p_num)].get((g, c_num), ("(미배정)", "(미배정)")))
                        val_str = str(val).strip() if val else "(미배정)"
                        if is_chief: prev_pair[0] = val_str
                        else: prev_pair[1] = val_str
                        current_asgn[(d, p_num)][(g, c_num)] = tuple(prev_pair)
    
    st.session_state["assignments"] = current_asgn

    # ④ 통계
    st.markdown("---")
    st.subheader("④ 배정 통계 및 검증")
    stats = compute_stats(st.session_state["assignments"], st.session_state["all_teachers"])
    st.dataframe(pd.DataFrame(stats), use_container_width=True)
    
    # ⑤ 다운로드
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        pd.DataFrame(stats).to_excel(writer, sheet_name='통계', index=False)
    st.download_button("📥 엑셀 결과 다운로드", output.getvalue(), "exam_schedule.xlsx")
