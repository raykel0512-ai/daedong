# app.py — 시험 시감 자동 편성 v2.9
import streamlit as st, pandas as pd, re
from collections import defaultdict
from io import BytesIO
from scheduler import build_teachers, run_assignment, compute_teacher_stats, compute_parent_stats
import db

st.set_page_config(page_title="시험 시감 자동 편성 v2.9", layout="wide")
st.title("🧮 시험 시감 자동 편성 v2.9")
st.caption("중복 배정 완벽 방지 | 교사/학부모 통합 탭 관리 | 정감독 횟수 균형 최적화")

# ══════════════════════════════════════════════════════════════
# 사이드바 설정
# ══════════════════════════════════════════════════════════════
with st.sidebar:
    st.header("⚙️ 기본 설정")
    num_days = int(st.number_input("시험 일수", 1, 10, 4))
    num_grades = int(st.number_input("학년 수", 1, 6, 3))
    classes_per_grade = int(st.number_input("학급 수", 1, 30, 8))
    
    st.subheader("일차별·학년별 교시 수")
    periods_by_day_grade = []
    for d in range(1, num_days + 1):
        with st.expander(f"{d}일차"):
            p_grade = [int(st.number_input(f"{g}학년 교시", 0, 10, 2, key=f"p_{d}_{g}")) for g in range(1, num_grades + 1)]
            periods_by_day_grade.append(p_grade)

    st.markdown("---")
    st.header("🔗 구글 시트 URL")
    sheet_url = st.text_input("구글 시트 URL", placeholder="https://docs.google.com/spreadsheets/d/...")
    teacher_gid = st.text_input("교사 명단 탭 GID", "0")
    parent_gid = st.text_input("학부모 명단 탭 GID", "")

# ══════════════════════════════════════════════════════════════
# 명단 로드 함수
# ══════════════════════════════════════════════════════════════
def get_csv_url(url, gid):
    if not url or not gid: return None
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m: return None
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid={gid}"

def load_data():
    t_url = get_csv_url(sheet_url, teacher_gid)
    p_url = get_csv_url(sheet_url, parent_gid)
    
    t_df = pd.read_csv(t_url) if t_url else pd.DataFrame()
    p_df = pd.read_csv(p_url) if p_url else pd.DataFrame()
    
    for df in [t_df, p_df]:
        if not df.empty: df.columns = [c.strip().lower() for c in df.columns]
    return t_df, p_df

# ══════════════════════════════════════════════════════════════
# ① 명단 확인
# ══════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("① 명단 데이터 확인")

if sheet_url:
    t_df, p_df = load_data()
    col1, col2 = st.columns(2)
    with col1:
        st.write("👨‍🏫 **교사 명단 (탭1)**")
        if not t_df.empty:
            for c, dv in [("role","교사"), ("available",""), ("exclude",""), ("extra_classes",""), ("priority",None)]:
                if c not in t_df.columns: t_df[c] = dv
            st.dataframe(t_df, height=250)
        else: st.warning("교사 데이터를 불러올 수 없습니다. GID를 확인하세요.")
    with col2:
        st.write("👥 **학부모 명단 (탭2)**")
        if not p_df.empty:
            for c, dv in [("role","학부모"), ("available",""), ("exclude",""), ("extra_classes",""), ("priority",None)]:
                if c not in p_df.columns: p_df[c] = dv
            st.dataframe(p_df, height=250)
        else: st.info("학부모 데이터가 없거나 GID가 입력되지 않았습니다.")

# ══════════════════════════════════════════════════════════════
# ② 자동 배정
# ══════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("② 자동 배정 실행")
if st.button("🚀 전체 일정 자동 배정 시작", type="primary", use_container_width=True):
    if t_df.empty:
        st.error("교사 명단이 필요합니다.")
    else:
        # 교사와 학부모 합치기
        combined_df = pd.concat([t_df, p_df], ignore_index=True)
        all_teachers = build_teachers(combined_df, num_days=num_days)
        
        # 배정 알고리즘 실행
        assignments = run_assignment(
            all_teachers, num_days, num_grades, classes_per_grade, periods_by_day_grade
        )
        
        st.session_state["assignments"] = assignments
        st.session_state["all_teachers"] = all_teachers
        st.success("배정이 완료되었습니다! 중복 검사 및 횟수 균형이 반영되었습니다.")

# ══════════════════════════════════════════════════════════════
# ③ 결과 및 수정
# ══════════════════════════════════════════════════════════════
if st.session_state.get("assignments"):
    asgn = st.session_state["assignments"]
    v_tabs = st.tabs([f"{d}일차" for d in range(1, num_days + 1)] + ["📊 통계"])
    
    for d in range(1, num_days + 1):
        with v_tabs[d-1]:
            for g in range(1, num_grades + 1):
                p_cnt = periods_by_day_grade[d-1][g-1]
                if p_cnt == 0: continue
                idx_labels = []
                for p in range(1, p_cnt + 1): idx_labels.extend([f"P{p} 정", f"P{p} 부"])
                cols = [f"{g}-{c}" for c in range(1, classes_per_grade + 1)]
                df_view = pd.DataFrame("", index=idx_labels, columns=cols)
                
                for p in range(1, p_cnt + 1):
                    slot = asgn.get((d, p), {})
                    for c in range(1, classes_per_grade + 1):
                        ch, ass = slot.get((g, c), ("(미배정)", "(미배정)"))
                        df_view.loc[f"P{p} 정", f"{g}-{c}"] = ch if ch != "(미배정)" else ""
                        df_view.loc[f"P{p} 부", f"{g}-{c}"] = ass if ass != "(미배정)" else ""
                
                st.write(f"**{g}학년**")
                edited = st.data_editor(df_view, key=f"ed_{d}_{g}", use_container_width=True)
                # 수정 반영
                for row_lbl, row_vals in edited.iterrows():
                    p_num, is_chief = int(row_lbl.split(" ")[0][1:]), "정" in row_lbl
                    for col_lbl, val in row_vals.items():
                        c_num = int(col_lbl.split("-")[1])
                        pair = list(asgn.get((d, p_num), {}).get((g, c_num), ("(미배정)", "(미배정)")))
                        val_str = str(val).strip() if val else "(미배정)"
                        if is_chief: pair[0] = val_str
                        else: pair[1] = val_str
                        asgn[(d, p_num)][(g, c_num)] = tuple(pair)

    with v_tabs[-1]:
        st.write("### 👨‍🏫 교사 누적 통계 (정감독 횟수 확인)")
        st.dataframe(pd.DataFrame(compute_teacher_stats(asgn, st.session_state["all_teachers"])), use_container_width=True)
        st.write("### 👥 학부모 일일 현황")
        st.dataframe(pd.DataFrame(compute_parent_stats(asgn, st.session_state["all_teachers"], num_days)), use_container_width=True)

    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        pd.DataFrame(compute_teacher_stats(asgn, st.session_state["all_teachers"])).to_excel(writer, sheet_name='교사', index=False)
        pd.DataFrame(compute_parent_stats(asgn, st.session_state["all_teachers"], num_days)).to_excel(writer, sheet_name='학부모', index=False)
    st.download_button("📥 엑셀 다운로드", output.getvalue(), "schedule_v29.xlsx")
