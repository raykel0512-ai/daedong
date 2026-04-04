# app.py — 시험 시감 자동 편성 v2.8
import streamlit as st, pandas as pd, re
from collections import defaultdict
from io import BytesIO
from scheduler import build_teachers, run_assignment, compute_teacher_stats, compute_parent_stats
import db

st.set_page_config(page_title="시험 시감 자동 편성 v2.8", layout="wide")
st.title("🧮 시험 시감 자동 편성 v2.8")
st.caption("롤링 배정(Rolling) | 통합 구글 시트 URL 지원 | 교사-학부모 분리")

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
    st.header("🔗 구글 시트 일괄 설정")
    base_url = st.text_input("통합 구글 시트 URL (선택사항)", placeholder="https://docs.google.com/spreadsheets/d/...")
    st.caption("여기 URL을 입력하면 각 일차별 탭에서 GID(탭번호)만 입력해 데이터를 불러올 수 있습니다.")

# ══════════════════════════════════════════════════════════════
# 상태 관리 및 유틸리티
# ══════════════════════════════════════════════════════════════
if "day_dfs" not in st.session_state: st.session_state["day_dfs"] = {}
if "assignments" not in st.session_state: st.session_state["assignments"] = {}
if "all_teachers" not in st.session_state: st.session_state["all_teachers"] = []

def get_csv_url(url, gid="0"):
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m: return None
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid={gid}"

def load_sheet(csv_url):
    try:
        df = pd.read_csv(csv_url)
        df.columns = [c.strip().lower() for c in df.columns]
        return df
    except Exception as e:
        st.error(f"데이터 로드 실패: {e}"); return None

# ══════════════════════════════════════════════════════════════
# ① 명단 업로드 섹션
# ══════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("① 시감 명단 업로드 (일차별)")
u_tabs = st.tabs([f"{d}일차" for d in range(1, num_days + 1)])
day_teacher_dfs = []

for d_idx, utab in enumerate(u_tabs, start=1):
    with utab:
        col1, col2 = st.columns([1, 2])
        with col1:
            method = st.radio(f"{d_idx}일차 방식", ["URL/GID", "CSV 업로드"], key=f"m_{d_idx}", horizontal=True)
            if method == "URL/GID":
                if base_url:
                    gid = st.text_input(f"{d_idx}일차 탭 GID", "0", key=f"gid_{d_idx}", help="시트 하단 탭 URL의 gid=... 뒤의 숫자")
                    target_url = get_csv_url(base_url, gid)
                else:
                    full_url = st.text_input(f"{d_idx}일차 전체 URL", key=f"full_url_{d_idx}")
                    target_url = get_csv_url(full_url) if full_url else None
                
                if st.button(f"🔗 {d_idx}일차 데이터 로드", key=f"btn_{d_idx}"):
                    if target_url:
                        df_res = load_sheet(target_url)
                        if df_res is not None: st.session_state["day_dfs"][d_idx] = df_res
                    else: st.warning("URL을 확인해주세요.")
            else:
                f = st.file_uploader(f"{d_idx}일차 CSV", type="csv", key=f"f_{d_idx}")
                if f:
                    df_csv = pd.read_csv(f)
                    df_csv.columns = [c.strip().lower() for c in df_csv.columns]
                    st.session_state["day_dfs"][d_idx] = df_csv

        # 미리보기
        df = st.session_state["day_dfs"].get(d_idx)
        if df is not None:
            for c, dv in [("role","교사"), ("available",""), ("exclude",""), ("extra_classes",""), ("priority",None)]:
                if c not in df.columns: df[c] = dv
            with col2: st.dataframe(df, height=180)
            day_teacher_dfs.append(df)
        else: day_teacher_dfs.append(None)

# ══════════════════════════════════════════════════════════════
# ② 자동 배정 (롤링 알고리즘)
# ══════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("② 자동 배정 시작")
if st.button("🚀 롤링(Rolling) 배정 실행", type="primary", use_container_width=True):
    all_asgn, all_t, seen = {}, [], set()
    rolling_counts = {"chief": defaultdict(int), "asst": defaultdict(int)}
    rolling_last_idx = 0
    
    for d_idx in range(1, num_days + 1):
        df = st.session_state["day_dfs"].get(d_idx)
        if df is None: continue
        tlist = build_teachers(df, num_days=num_days)
        for t in tlist:
            if t.name not in seen: all_t.append(t); seen.add(t.name)
        
        # 날짜별로 롤링 상태 상속
        res, updated_counts, updated_last_idx = run_assignment(
            tlist, 1, num_grades, classes_per_grade, 
            [periods_by_day_grade[d_idx-1]], 
            prev_counts=rolling_counts, 
            last_idx=rolling_last_idx
        )
        for (_, p), slot in res.items(): all_asgn[(d_idx, p)] = slot
        rolling_counts, rolling_last_idx = updated_counts, updated_last_idx
        
    st.session_state["assignments"], st.session_state["all_teachers"] = all_asgn, all_t
    st.success("전체 기간 롤링 배정이 완료되었습니다!")

# ══════════════════════════════════════════════════════════════
# ③ 시간표 확인 및 수정
# ══════════════════════════════════════════════════════════════
if st.session_state["assignments"]:
    st.markdown("---")
    st.subheader("③ 결과 확인 및 수동 수정")
    asgn = st.session_state["assignments"]
    v_tabs = st.tabs([f"{d}일차" for d in range(1, num_days + 1)] + ["📊 전체 통계"])
    
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
                        ch, ass = slot.get((g, c), ("", ""))
                        df_view.loc[f"P{p} 정", f"{g}-{c}"] = ch if ch != "(미배정)" else ""
                        df_view.loc[f"P{p} 부", f"{g}-{c}"] = ass if ass != "(미배정)" else ""
                st.write(f"**{g}학년**")
                edited = st.data_editor(df_view, key=f"ed_{d}_{g}", use_container_width=True)
                # 데이터 수정 반영
                for row_lbl, row_vals in edited.iterrows():
                    p_num, is_chief = int(row_lbl.split(" ")[0][1:]), "정" in row_lbl
                    for col_lbl, val in row_vals.items():
                        c_num = int(col_lbl.split("-")[1])
                        pair = list(asgn.get((d, p_num), {}).get((g, c_num), ("(미배정)", "(미배정)")))
                        val_str = str(val).strip() if val else "(미배정)"
                        if is_chief: pair[0] = val_str
                        else: pair[1] = val_str
                        if (d, p_num) not in asgn: asgn[(d, p_num)] = {}
                        asgn[(d, p_num)][(g, c_num)] = tuple(pair)

    with v_tabs[-1]:
        st.write("### 👨‍🏫 교사 누적 통계")
        st.dataframe(pd.DataFrame(compute_teacher_stats(asgn, st.session_state["all_teachers"])), use_container_width=True)
        st.write("### 👥 학부모 일일 현황 (2회 여부 체크)")
        st.dataframe(pd.DataFrame(compute_parent_stats(asgn, st.session_state["all_teachers"], num_days)), use_container_width=True)

    st.session_state["assignments"] = asgn
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        pd.DataFrame(compute_teacher_stats(asgn, st.session_state["all_teachers"])).to_excel(writer, sheet_name='교사', index=False)
        pd.DataFrame(compute_parent_stats(asgn, st.session_state["all_teachers"], num_days)).to_excel(writer, sheet_name='학부모', index=False)
    st.download_button("📥 엑셀 결과 다운로드", output.getvalue(), "schedule_final.xlsx")
