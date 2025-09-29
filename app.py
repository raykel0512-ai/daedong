# streamlit run app.py
# 시험 시감 자동 편성 (우선순위/제외/시각화/편집/엑셀)

from collections import defaultdict
from datetime import datetime
from io import BytesIO
import re

import pandas as pd
import streamlit as st

# 엑셀 엔진 폴백 설정
try:
    import xlsxwriter  # noqa: F401
    _excel_engine = "xlsxwriter"
except Exception:
    try:
        import openpyxl  # noqa: F401
        _excel_engine = "openpyxl"
    except Exception:
        _excel_engine = None  # 엑셀 내보내기 비활성

st.set_page_config(page_title="시험 시감 자동 편성", layout="wide")

st.title("🧮 시험 시감 자동 편성 프로그램")
st.caption(
    "일수 가변 · **하루별/학년별 교시 수 각각 설정** · 제외(시간/반/시간+반) · "
    "**정·부감독 2인 배정** · **우선순위 정책** · 시각화 **수기 편집 가능** · 엑셀 내보내기"
)

# -----------------------------
# Sidebar: 기본 설정
# -----------------------------
st.sidebar.header("기본 설정")

# 시험 일수
num_days = st.sidebar.number_input("시험 일수(일)", min_value=1, max_value=10, value=4, step=1)

# 학년/학급 구성
st.sidebar.subheader("학년/학급 구성")
num_grades = st.sidebar.number_input("학년 수", min_value=1, max_value=6, value=3, step=1)
classes_per_grade = st.sidebar.number_input("학년별 학급 수(동일)", min_value=1, max_value=30, value=8, step=1)

# 하루·학년별 교시 수
st.sidebar.subheader("하루별·학년별 교시 수 설정")
# periods_by_day_by_grade[d][g] = d일차 g학년 교시 수
periods_by_day_by_grade = []
for d in range(1, num_days + 1):
    with st.sidebar.expander(f"{d}일차 교시 수", expanded=(d == 1)):
        per_grade = []
        for g in range(1, num_grades + 1):
            per_grade.append(
                st.number_input(
                    f"{g}학년", min_value=0, max_value=10, value=2, step=1, key=f"pbdg_{d}_{g}"
                )
            )
        periods_by_day_by_grade.append(per_grade)

# 부족 인원 대응 옵션
st.sidebar.subheader("부족 인원 대응 옵션")
allow_multi_classes = st.sidebar.checkbox(
    "같은 교시 여러 교실 담당 허용(중복 배정)",
    value=False,
    help="교사가 한 교시 동안 여러 반을 맡을 수 있도록 허용합니다."
)
allow_same_person_both_roles = st.sidebar.checkbox(
    "한 반에서 정·부감독을 같은 교사가 겸임 허용",
    value=False,
    help="인원이 매우 부족할 때만 권장합니다."
)

st.sidebar.markdown("---")

# -----------------------------
# 1) 교사 명단 업로드
# -----------------------------
st.subheader("1) 교사 명단 업로드")

st.write("""
CSV 파일을 업로드하세요. 

**필수 열**
- `name`

**선택 열**
- `exclude` : 제외 규칙
- `priority` : 우선순위 (숫자, 작을수록 먼저 배정되는 쪽; 숫자가 클수록 '우선 낮음')

**제외 규칙 예시**
- 시간 제외: `D1P2`  → 1일차 2교시 제외
- 반 제외: `1-3` 또는 `C1-3`  → 1학년 3반 전체 제외
- 특정 시간+반 제외: `D1P2@1-3`  → 1일차 2교시의 1학년 3반 제외
- 여러 항목은 세미콜론(;)으로 구분: `D1P2; 2-4; D2P1@3-7`

**우선순위 정책(현 설정)**
- 기본 몫은 균등 배분
- **추가 배정(잔여 몫)과 선발 순서 모두 “우선 낮음(숫자 큼)” 우선**
  - 따라서 우선 낮은 선생님이 **정감독/총합을 더 많이** 맡게 됩니다.
- 원하시면 반대로(우선 높은 쪽이 더 많이)도 설정 가능
""")

# 샘플/템플릿 다운로드
sample_df = pd.DataFrame({
    "name": [f"교사{i:02d}" for i in range(1, 41)],
    "exclude": ["", "D1P2", "2-3", "D2P1@1-4", "", "", "3-7", "D1P1; D3P2@2-1"] + [""] * 32,
    "priority": [1, 1, 2, 2, 3, 3] + [None] * 34,
})
col_s1, col_s2 = st.columns(2)
with col_s1:
    st.download_button(
        "샘플 CSV 내려받기",
        data=sample_df.to_csv(index=False).encode("utf-8-sig"),
        file_name="sample_teachers.csv",
        mime="text/csv",
        use_container_width=True
    )
with col_s2:
    empty_df = pd.DataFrame({"name": [], "exclude": [], "priority": []})
    st.download_button(
        "빈 템플릿 내려받기",
        data=empty_df.to_csv(index=False).encode("utf-8-sig"),
        file_name="teachers_template.csv",
        mime="text/csv",
        use_container_width=True
    )

uploaded = st.file_uploader("교사 명단 CSV 업로드", type=["csv"])

# CSV 로딩
if uploaded is not None:
    try:
        df_teachers = pd.read_csv(uploaded)
    except UnicodeDecodeError:
        df_teachers = pd.read_csv(uploaded, encoding="utf-8-sig")
    df_teachers.columns = [c.strip().lower() for c in df_teachers.columns]
    if "name" not in df_teachers.columns:
        st.error("CSV에 반드시 'name' 열이 있어야 합니다.")
        st.stop()
    if "exclude" not in df_teachers.columns:
        df_teachers["exclude"] = ""
    if "priority" not in df_teachers.columns:
        df_teachers["priority"] = None
else:
    st.info("샘플 데이터로 미리보기 중입니다. 실제 편성 전 CSV를 업로드하세요.")
    df_teachers = pd.DataFrame({
        "name": [f"교사{i:02d}" for i in range(1, 41)],
        "exclude": [""] * 40,
        "priority": [None] * 40,
    })

st.dataframe(df_teachers, use_container_width=True)

# -----------------------------
# 슬롯 정의
# -----------------------------
# 감독 슬롯: 각 일자에서 학년별 교시 수 중 "최대 교시"만큼 D#P# 슬롯을 생성
slots = []  # (day, period)
for d in range(1, num_days + 1):
    max_p = max([int(periods_by_day_by_grade[d - 1][g - 1]) for g in range(1, num_grades + 1)] + [0])
    for p in range(1, max_p + 1):
        slots.append((d, p))

st.markdown("---")
st.subheader("2) 제외 규칙 형식")
st.write(
    "- `D1P2` → 1일차 2교시 전체 제외  \n"
    "- `1-3` or `C1-3` → 1학년 3반 전체 시간 제외  \n"
    "- `D1P2@1-3` → 1일차 2교시의 1학년 3반만 제외"
)

# -----------------------------
# exclude 파싱 (시간 / 반 / 시간+반)
# -----------------------------
exclude_time = defaultdict(set)        # t -> {(d,p)}
exclude_class = defaultdict(set)       # t -> {(g,c)}
exclude_time_class = defaultdict(set)  # t -> {(d,p,g,c)}

class_pat = re.compile(r"^(?:C)?(\d+)-(\d+)$")

def parse_exclude_token(tok):
    tok = tok.strip()
    if not tok:
        return (None, None, None)
    # 시간+반 (예: D1P2@1-3)
    if "@" in tok:
        left, right = tok.split("@", 1)
        d, p = None, None
        right = right.strip()
        m = class_pat.match(right.replace(" ", ""))
        g, c = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        up = left.upper().replace(" ", "")
        if up.startswith("D") and "P" in up:
            try:
                d = int(up.split("P")[0].replace("D", ""))
                p = int(up.split("P")[1])
            except Exception:
                d, p = None, None
        return (d, p, (g, c))
    # 시간만 (D#P#)
    up = tok.upper().replace(" ", "")
    if up.startswith("D") and "P" in up:
        try:
            d = int(up.split("P")[0].replace("D", ""))
            p = int(up.split("P")[1])
            return (d, p, None)
        except Exception:
            return (None, None, None)
    # 반만 (1-3, C1-3)
    m = class_pat.match(up)
    if m:
        return (None, None, (int(m.group(1)), int(m.group(2))))
    return (None, None, None)

for _, row in df_teachers.iterrows():
    name = str(row["name"]).strip()
    excl_raw = str(row.get("exclude", "")).strip()
    if not excl_raw:
        continue
    for tok in [t for t in excl_raw.split(";") if t.strip()]:
        d, p, gc = parse_exclude_token(tok)
        if d and p and gc and all(gc):
            exclude_time_class[name].add((d, p, gc[0], gc[1]))
        elif d and p:
            exclude_time[name].add((d, p))
        elif gc and all(gc):
            exclude_class[name].add((gc[0], gc[1]))

# -----------------------------
# 배정 알고리즘 (우선순위 기반 균등할당 + 정감독/총합 = 우선 낮음 편중)
# -----------------------------
teachers = df_teachers["name"].tolist()
if len(teachers) == 0:
    st.error("교사 명단이 비어 있습니다.")
    st.stop()

# 우선순위 정렬: priority가 낮은 숫자일수록 '우선 높음', 큰 숫자일수록 '우선 낮음'
_df = df_teachers.copy()
_df["_order"] = range(len(_df))
_df["priority_num"] = pd.to_numeric(_df.get("priority"), errors="coerce")
_df["priority_num"].fillna(1e9, inplace=True)
_df.sort_values(["priority_num", "_order"], inplace=True)
teacher_order = _df["name"].tolist()               # priority 오름차순 (우선 높은 → 낮은)
teacher_order_rev = list(reversed(teacher_order))  # priority 내림차순 (우선 낮은 → 높은)

# 전체 필요 자리 수 계산 (학급×정/부)
rooms_total = 0
for (d, p) in slots:
    active_grades = [g for g in range(1, num_grades + 1) if int(periods_by_day_by_grade[d - 1][g - 1]) >= p]
    rooms_total += len(active_grades) * classes_per_grade
chief_needed = rooms_total
assistant_needed = rooms_total
N = max(len(teacher_order), 1)

# 정/부 쿼터: 기본 균등 + 잔여는 저우선부터 채움
chief_base = chief_needed // N
chief_rem = chief_needed - chief_base * N
chief_quota = {t: chief_base for t in teacher_order}
for t in teacher_order_rev:
    if chief_rem <= 0:
        break
    chief_quota[t] += 1
    chief_rem -= 1

assistant_base = assistant_needed // N
assistant_rem = assistant_needed - assistant_base * N
assistant_quota = {t: assistant_base for t in teacher_order}
for t in teacher_order_rev:
    if assistant_rem <= 0:
        break
    assistant_quota[t] += 1
    assistant_rem -= 1

def can_use(t, d, p, g, c):
    if (d, p) in exclude_time.get(t, set()):
        return False
    if (g, c) in exclude_class.get(t, set()):
        return False
    if (d, p, g, c) in exclude_time_class.get(t, set()):
        return False
    return True

# 실제 배정
classroom_assignments = dict()  # (d,p) -> dict[(g,c)] = (chief, assistant)

for (d, p) in slots:
    active_grades = [g for g in range(1, num_grades + 1) if int(periods_by_day_by_grade[d - 1][g - 1]) >= p]
    slot_taken = set()  # 같은 교시 중복 방지(옵션으로 완화)
    per_slot = {}
    for g in active_grades:
        for c in range(1, classes_per_grade + 1):
            chief, assistant = "(미배정)", "(미배정)"
            # 1) 정감독: 저우선부터 quota>0 우선
            for t in teacher_order_rev:
                if chief != "(미배정)":
                    break
                if chief_quota.get(t, 0) <= 0:
                    continue
                if not can_use(t, d, p, g, c):
                    continue
                if (not allow_multi_classes) and (t in slot_taken):
                    continue
                chief = t
                slot_taken.add(t)
                chief_quota[t] -= 1
            # 리필(정): quota 무시, 여전히 저우선부터
            if chief == "(미배정)":
                for t in teacher_order_rev:
                    if chief != "(미배정)":
                        break
                    if not can_use(t, d, p, g, c):
                        continue
                    if (not allow_multi_classes) and (t in slot_taken):
                        continue
                    chief = t
                    slot_taken.add(t)
            # 2) 부감독: 저우선부터 quota>0 우선
            for t in teacher_order_rev:
                if assistant != "(미배정)":
                    break
                if assistant_quota.get(t, 0) <= 0:
                    continue
                if not can_use(t, d, p, g, c):
                    continue
                if (not allow_same_person_both_roles) and (t == chief):
                    continue
                if (not allow_multi_classes) and (t in slot_taken):
                    continue
                assistant = t
                slot_taken.add(t)
                assistant_quota[t] -= 1
            # 리필(부): quota 무시, 저우선부터
            if assistant == "(미배정)":
                for t in teacher_order_rev:
                    if assistant != "(미배정)":
                        break
                    if not can_use(t, d, p, g, c):
                        continue
                    if (not allow_same_person_both_roles) and (t == chief):
                        continue
                    if (not allow_multi_classes) and (t in slot_taken):
                        continue
                    assistant = t
                    slot_taken.add(t)
            per_slot[(g, c)] = (chief, assistant)
    classroom_assignments[(d, p)] = per_slot

# -----------------------------
# 3) 일자별 시험 시간표 (시각화) — ✍️ 수기 편집 가능
# -----------------------------
st.markdown("---")
st.subheader("3) 일자별 시험 시간표 (시각화 · 편집 가능)")

tables_original = {}
tables_edited = {}

if num_days > 0:
    tabs = st.tabs([f"{d}일차" for d in range(1, num_days + 1)])
    for d_idx, tab in enumerate(tabs, start=1):
        with tab:
            st.markdown(f"#### 📚 {d_idx}일차 학년별 시감 표 (정/부 별도 행)")
            for g in range(1, num_grades + 1):
                p_cnt = int(periods_by_day_by_grade[d_idx - 1][g - 1])
                if p_cnt <= 0:
                    continue
                cols = [f"{g}-{c}" for c in range(1, classes_per_grade + 1)]
                # 행 = 교시*2 (P1-정, P1-부, ...)
                idx = []
                for p in range(1, p_cnt + 1):
                    idx.append(f"P{p}-정")
                    idx.append(f"P{p}-부")
                table = pd.DataFrame("", index=idx, columns=cols)
                for p in range(1, p_cnt + 1):
                    per_slot = classroom_assignments.get((d_idx, p), {})
                    for c in range(1, classes_per_grade + 1):
                        chief, assistant = per_slot.get((g, c), ("", ""))
                        if chief:
                            table.loc[f"P{p}-정", f"{g}-{c}"] = chief
                        if assistant:
                            table.loc[f"P{p}-부", f"{g}-{c}"] = assistant
                tables_original[(d_idx, g)] = table.copy()
                st.markdown(f"**{g}학년** (교시수: {p_cnt}) — 셀을 클릭해 교사명을 직접 수정/추가하세요")
                tables_edited[(d_idx, g)] = st.data_editor(
                    table,
                    key=f"viz_{d_idx}_{g}",
                    num_rows="dynamic",
                    use_container_width=True,
                    hide_index=False,
                )

# 편집 결과 반영: classroom_assignments_final 구성
classroom_assignments_final = { (d,p): {} for (d,p) in slots }

for (d, g), ed in tables_edited.items():
    for idx_label, row in ed.iterrows():
        try:
            p_str, role = idx_label.split("-")
            p = int(p_str.replace("P", ""))
        except Exception:
            continue
        for col, val in row.items():
            if not isinstance(val, str):
                continue
            name = val.strip()
            if name == "":
                name = "(미배정)"
            try:
                g_str, c_str = col.split("-")
                g_check = int(g_str); c = int(c_str)
            except Exception:
                continue
            if g_check != g:
                continue
            chief, assistant = classroom_assignments_final.get((d, p), {}).get((g, c), ("(미배정)", "(미배정)"))
            if role == "정":
                chief = name
            else:
                assistant = name
            classroom_assignments_final[(d, p)][(g, c)] = (chief, assistant)

# 편집본이 전혀 없다면 자동배정 사용
if all(len(v) == 0 for v in classroom_assignments_final.values()):
    classroom_assignments_final = classroom_assignments

st.info("시각화 표에서 수정한 내용이 아래 '배정 통계·검증'과 '결과 저장(엑셀)'에 그대로 반영됩니다. (새 이름도 입력 가능)")

# -----------------------------
# 4) 배정 통계 & 검증 — 편집 반영
# -----------------------------
st.markdown("---")
st.subheader("4) 배정 통계 & 검증")

# 정/부 역할별 카운트
counts_chief = defaultdict(int)
counts_assistant = defaultdict(int)
for (d, p), per_slot in classroom_assignments_final.items():
    for (g, c), (chief, assistant) in per_slot.items():
        if isinstance(chief, str) and chief and chief != "(미배정)":
            counts_chief[chief] += 1
        if isinstance(assistant, str) and assistant and assistant != "(미배정)":
            counts_assistant[assistant] += 1

# 우선순위 맵
prio_map = {}
if "priority" in df_teachers.columns:
    try:
        prio_map = df_teachers.set_index("name")["priority"].to_dict()
    except Exception:
        prio_map = {}

# 테이블 구성 (컬럼/정렬 고정)
all_names = sorted(set(list(df_teachers["name"])) | set(counts_chief.keys()) | set(counts_assistant.keys()))
stat_rows = []
for n in all_names:
    ch = counts_chief.get(n, 0)
    asn = counts_assistant.get(n, 0)
    pr = prio_map.get(n, None)
    stat_rows.append({"priority": pr, "name": n, "정감독": ch, "부감독": asn, "total": ch + asn})
stat_df = pd.DataFrame(stat_rows)

# ideal = 전체 배정합 / 교사 수 (공통 기준)
_total = stat_df["total"].sum() if not stat_df.empty else 0
_ideal = round(_total / max(len(stat_df), 1), 2)
stat_df["ideal"] = _ideal

# 정렬 고정: priority(숫자변환, NaN→큰값) → name
stat_df["_prio_num"] = pd.to_numeric(stat_df["priority"], errors="coerce").fillna(1e9)
stat_df = stat_df.sort_values(["_prio_num", "name"], ascending=[True, True]).drop(columns=["_prio_num"])

# 컬럼 순서 고정
desired_cols = ["priority", "name", "정감독", "부감독", "total", "ideal"]
stat_df = stat_df.reindex(columns=desired_cols)

st.dataframe(stat_df, use_container_width=True)

# 제외 위반 검사
violations = []
for (d, p), per_slot in classroom_assignments_final.items():
    for (g, c), (chief, assistant) in per_slot.items():
        for role, t in [("chief", chief), ("assistant", assistant)]:
            if isinstance(t, str) and t and t != "(미배정)":
                if (d, p) in exclude_time.get(t, set()) or (g, c) in exclude_class.get(t, set()) or (d, p, g, c) in exclude_time_class.get(t, set()):
                    violations.append({"day": d, "period": p, "grade": g, "class": c, "role": role, "name": t})
if violations:
    st.error("제외 시간/반 위반 건이 있습니다. 아래 목록을 확인해 수정하세요.")
    st.dataframe(pd.DataFrame(violations))
else:
    st.success("제외 조건 위반 없음 ✅")

# -----------------------------
# 5) 결과 저장 (시각화 형식: Excel)
# -----------------------------
st.markdown("---")
st.subheader("5) 결과 저장 (시각화 형식: Excel)")

excel_buf = BytesIO()
if _excel_engine is None:
    st.error("엑셀 엔진(xlsxwriter/openpyxl)을 사용할 수 없어 Excel 내보내기를 비활성화했습니다. requirements.txt에 패키지를 추가해 주세요.")
else:
    with pd.ExcelWriter(excel_buf, engine=_excel_engine) as writer:
        # 일자별 시트
        for d in range(1, num_days + 1):
            ws_name = f"D{d}"
            start_row = 0
            for g in range(1, num_grades + 1):
                p_cnt = int(periods_by_day_by_grade[d - 1][g - 1])
                if p_cnt <= 0:
                    continue
                idx = []
                for p in range(1, p_cnt + 1):
                    idx.append(f"P{p}-정")
                    idx.append(f"P{p}-부")
                cols = [f"{g}-{c}" for c in range(1, classes_per_grade + 1)]
                table = pd.DataFrame("", index=idx, columns=cols)
                for p in range(1, p_cnt + 1):
                    per_slot = classroom_assignments_final.get((d, p), {})
                    for c in range(1, classes_per_grade + 1):
                        chief, assistant = per_slot.get((g, c), ("", ""))
                        if chief:
                            table.loc[f"P{p}-정", f"{g}-{c}"] = chief
                        if assistant:
                            table.loc[f"P{p}-부", f"{g}-{c}"] = assistant
                # 학년 제목 + 표
                title_df = pd.DataFrame({f"{g}학년 (교시수:{p_cnt})": []})
                title_df.to_excel(writer, sheet_name=ws_name, startrow=start_row, index=False)
                start_row += 1
                table.to_excel(writer, sheet_name=ws_name, startrow=start_row)
                start_row += len(table) + 2  # 간격

        # 통계 시트 (원하는 컬럼 순서)
        stat_df.to_excel(writer, sheet_name="Statistics", index=False)
        # 위반 시트(있을 때만)
        if violations:
            pd.DataFrame(violations).to_excel(writer, sheet_name="Violations", index=False)

    st.download_button(
        label=f"시각화 엑셀 다운로드 (.xlsx) [{_excel_engine}]",
        data=excel_buf.getvalue(),
        file_name=f"exam_schedule_visual_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

st.markdown(
    """
---
### 사용 팁
- **수기 편집**: 시각화 표(Pn-정/부)에서 텍스트를 바꾸면 통계/검증/엑셀에 반영됩니다. (새 교사명도 입력 가능)
- **우선순위**: 현재는 '우선 낮음(숫자 큼)'에게 정감독·총합이 더 많이 가도록 설정되어 있습니다.
- **옵션**: 같은 교시 중복/겸임은 가능한 OFF로 두고 배정 품질을 본 뒤, 빈칸이 생길 때만 ON으로 전환을 권장합니다.
"""
)

