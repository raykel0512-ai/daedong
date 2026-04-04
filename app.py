# app.py — 시험 시감 자동 편성 v2.6
import streamlit as st, pandas as pd, re
from io import BytesIO
from scheduler import build_teachers, run_assignment, compute_teacher_stats, compute_parent_stats
import db

st.set_page_config(page_title="시험 시감 자동 편성 v2.6", layout="wide")
st.title("🧮 시험 시감 자동 편성 v2.6")
st.caption("교사(누적 통계)와 학부모(일일 횟수 체크) 역할 분리 시스템")

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
    except Exception as e:
        st.error(f"시트 로드 실패: {e}"); return None

st.markdown("---")
st.subheader("① 교사/학부모 명단 업로드")
st.info("💡 'role' 열에 **교사** 또는 **학부모**를 입력하세요. 미입력 시 '교사'로 처리됩니다.")
u_tabs = st.tabs([f"{d}일차" for d in range(1, num_days + 1)])
day_teacher_dfs = []

for d_idx, utab in enumerate(u_tabs, start=1):
    with utab:
        col_in, col_pre = st.columns([1, 2])
        with col_in:
            method = st.radio(f"{d_idx}일차 방식", ["URL", "CSV"], key=f"method_{d_idx}", horizontal=True)
            if method == "URL":
                gs_url = st.text_input("구글 시트 URL", key=f"url_{d_idx}")
                if st.button("🔗 로드", key=f"btn_url_{d_idx}"):
                    df_gs = load_gsheet(gs_url)
                    if df_gs is not None: st.session_state["day_dfs"][d_idx] = df_gs
            else:
                f = st.file_uploader(f"CSV 업로드", type="csv", key=f"f_{d_idx}")
                if f:
                    df_csv = pd.read_csv(f)
                    df_csv.columns = [c.strip().lower() for c in df_csv.columns]
                    st.session_state["day_dfs"][d_idx] = df_csv
        
        df = st.session_state["day_dfs"].get(d_idx)
        if df is not None:
            for col, dval in [("role","교사"), ("available",""), ("exclude",""), ("extra_classes",""), ("priority",None)]:
                if col not in df.columns: df[col] = dval
            with col_pre: st.dataframe(df, height=180)
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
    st.session_state["assignments"], st.session_state["all_teachers"] = all_asgn, all_t
    st.success("배정 완료!")

if st.session_state["assignments"]:
    st.markdown("---")
    st.subheader("③ 결과 확인 및 수정")
    current_asgn = st.session_state["assignments"]
    v_tabs = st.tabs([f"{d}일차 시간표" for d in range(1, num_days + 1)] + ["📊 배정 현황(통계)"])
    
    # 1~4일차 시간표 탭
    for d in range(1, num_days + 1):
        with v_tabs[d-1]:
            for g in range(1, num_grades + 1):
                p_cnt = periods_by_day_grade[d-1][g-1]
                if p_cnt == 0: continue
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
                edited_df = st.data_editor(df_edit, key=f"edit_{d}_{g}", use_container_width=True)
                # 수정 반영
                for row_label, row in edited_df.iterrows():
                    parts = row_label.split(" ")
                    p_num, is_chief = int(parts[0][1:]), "정" in parts[1]
                    for col_label, val in row.items():
                        c_num = int(col_label.split("-")[1].replace("반", ""))
                        if (d, p_num) not in current_asgn: current_asgn[(d, p_num)] = {}
                        pair = list(current_asgn[(d, p_num)].get((g, c_num), ("(미배정)", "(미배정)")))
                        val_str = str(val).strip() if val else "(미배정)"
                        if is_chief: pair[0] = val_str
                        else: pair[1] = val_str
                        current_asgn[(d, p_num)][(g, c_num)] = tuple(pair)
    
    # 통계 탭
    with v_tabs[-1]:
        st.subheader("👨‍🏫 교사 배정 통계 (누적)")
        t_stats = compute_teacher_stats(current_asgn, st.session_state["all_teachers"])
        st.dataframe(pd.DataFrame(t_stats), use_container_width=True)
        
        st.markdown("---")
        st.subheader("👥 학부모 배정 현황 (일일 체크)")
        st.caption("하루 2회 배정 여부를 확인하세요.")
        p_stats = compute_parent_stats(current_asgn, st.session_state["all_teachers"], num_days)
        st.dataframe(pd.DataFrame(p_stats), use_container_width=True)

    st.session_state["assignments"] = current_asgn
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        pd.DataFrame(compute_teacher_stats(current_asgn, st.session_state["all_teachers"])).to_excel(writer, sheet_name='교사통계', index=False)
        pd.DataFrame(compute_parent_stats(current_asgn, st.session_state["all_teachers"], num_days)).to_excel(writer, sheet_name='학부모현황', index=False)
    st.download_button("📥 엑셀 결과 다운로드", output.getvalue(), "exam_schedule_v26.xlsx")
