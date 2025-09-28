# streamlit run app.py
# 시험 시감 자동 편성 (순번 고정 / 학년별-일자별 교시 수 / 슬롯당 인원 / 제외 반영 / 편집·다운로드 / 시각화)

from collections import defaultdict
from datetime import datetime

import pandas as pd
import streamlit as st

st.set_page_config(page_title="시험 시감 자동 편성", layout="wide")

st.title("🧮 시험 시감 자동 편성 프로그램")
st.caption(
    "일수 가변 · **하루별/학년별 교시 수 각각 설정 가능** · 교사 ~50명 기준 · "
    "가용/제외시간 반영 · **순번 고정 배정** · 수작업 편집·다운로드 가능"
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
classes_per_grade = st.sidebar.number_input("학년별 학급 수(동일)", min_value=1, max_value=20, value=8, step=1)

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

# 슬롯당 필요한 감독 교사 수 (기본 2명)
proctors_per_slot = st.sidebar.number_input(
    "슬롯당 필요한 감독 교사 수",
    min_value=1, max_value=30, value=2,
    help="한 교시(슬롯)마다 필요한 시감 교사 수"
)

st.sidebar.markdown("---")

# -----------------------------
# 데이터 업로드 & 템플릿
# -----------------------------
st.subheader("1) 교사 명단 업로드")
st.write(
    "CSV 파일을 업로드하세요. **필수 열**: `name`. **선택 열**: `exclude` (예: `D1P2; D3P1`).\n"
    "※ `exclude`는 해당 슬롯 배정을 제외합니다."
)

# 샘플/템플릿 다운로드
sample_df = pd.DataFrame({
    "name": [f"교사{i:02d}" for i in range(1, 11)],
    "exclude": ["", "D1P2", "D2P2; D3P1", "", "D1P1; D4P2", "", "D3P2", "", "", "D2P1"],
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
    empty_df = pd.DataFrame({"name": [], "exclude": []})
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
else:
    st.info("샘플 데이터로 미리보기 중입니다. 실제 편성 전 CSV를 업로드하세요.")
    df_teachers = pd.DataFrame({
        "name": [f"교사{i:02d}" for i in range(1, 21)],
        "exclude": [
            "", "D1P2", "D2P2; D3P1", "", "D1P1; D4P2", "", "D3P2", "", "", "D2P1",
            "", "", "D1P1", "", "D4P2", "", "", "D3P1", "", ""
        ],
    })

st.dataframe(df_teachers, use_container_width=True)

# -----------------------------
# 슬롯 정의 & 제외 파싱
# -----------------------------
# 감독 슬롯: 각 일자에서 학년별 교시 수 중 "최대 교시"만큼 D#P# 슬롯을 생성
slots = []  # (day, period)
for d in range(1, num_days + 1):
    max_p = max([int(periods_by_day_by_grade[d - 1][g - 1]) for g in range(1, num_grades + 1)] + [0])
    for p in range(1, max_p + 1):
        slots.append((d, p))

slot_labels = [f"D{d}P{p}" for d, p in slots]

st.markdown("---")
st.subheader("2) 제외 시간 형식")
st.write("각 교사의 `exclude` 칸에 **세미콜론(;)로 구분**하여 `D<일>P<교시>` 형식으로 입력합니다. 예: `D1P2; D3P1`.")

# exclude 파싱
teacher_exclude = {}
for _, row in df_teachers.iterrows():
    name = str(row["name"]).strip()
    excl_raw = str(row.get("exclude", "")).strip()
    exclusions = set()
    if excl_raw:
        for tok in [t.strip() for t in excl_raw.split(";") if t.strip()]:
            up = tok.upper().replace(" ", "")
            if up.startswith("D") and "P" in up:
                try:
                    d_idx = up.find("D")
                    p_idx = up.find("P")
                    d = int(up[d_idx + 1:p_idx])
                    p = int(up[p_idx + 1:])
                    exclusions.add((d, p))
                except Exception:
                    pass
    teacher_exclude[name] = exclusions

# -----------------------------
# 배정 알고리즘 (순번 고정 · 라운드로빈)
# -----------------------------
teachers = df_teachers["name"].tolist()
assignments = defaultdict(list)   # slot_label -> [names]
load = defaultdict(int)          # name -> assigned count

if len(teachers) == 0:
    st.error("교사 명단이 비어 있습니다.")
    st.stop()

cursor = 0
N = len(teachers)

for (d, p) in slots:
    label = f"D{d}P{p}"
    picked = []
    checked = 0
    while len(picked) < proctors_per_slot and checked < N * 2:
        t = teachers[cursor % N]
        cursor += 1
        checked += 1
        if (d, p) in teacher_exclude.get(t, set()):
            continue
        if t in picked:
            continue
        picked.append(t)
        load[t] += 1
    assignments[label] = picked

# 배정 결과 테이블
rows = []
for (d, p) in slots:
    label = f"D{d}P{p}"
    row = {"slot": label}
    people = assignments[label]
    for i in range(proctors_per_slot):
        row[f"proctor_{i + 1}"] = people[i] if i < len(people) else "(미배정)"
    rows.append(row)
schedule_df = pd.DataFrame(rows)

# 미배정 경고
unfilled = (schedule_df == "(미배정)").sum().sum()
if unfilled > 0:
    st.warning(
        f"일부 슬롯에 미배정 인원이 있습니다: {unfilled} 자리. "
        f"'슬롯당 필요한 인원 수'를 줄이거나 제외 조건을 완화해 주세요."
    )

st.markdown("---")
st.subheader("3) 자동 배정 결과 (편집 가능)")
edited = st.data_editor(
    schedule_df,
    use_container_width=True,
    num_rows="dynamic",
    hide_index=True,
    key="schedule_editor",
)

st.markdown("---")
st.subheader("4) 배정 통계 & 검증")

# 현재 편집 상태 기준 카운트
assigned_names = []
for c in [c for c in edited.columns if c.startswith("proctor_")]:
    assigned_names += [v for v in edited[c].tolist() if isinstance(v, str) and v and v != "(미배정)"]

counts = pd.Series(assigned_names).value_counts().rename_axis("name").reset_index(name="assigned_count")
all_counts = pd.DataFrame({"name": teachers}).merge(counts, how="left", on="name").fillna({"assigned_count": 0})
ideal_per_teacher = round((len(slots) * proctors_per_slot) / max(len(teachers), 1), 2)
all_counts["ideal"] = ideal_per_teacher
all_counts = all_counts.sort_values("assigned_count", ascending=False)

c1, c2 = st.columns([1, 1])
with c1:
    st.write("교사별 배정 현황")
    st.dataframe(all_counts, use_container_width=True)
with c2:
    st.write("제외 조건 위반 여부 샘플 검사")
    violations = []
    slot_map = {row["slot"]: row for _, row in edited.iterrows()}
    for slot_label, row in slot_map.items():
        try:
            d = int(slot_label.split("P")[0].replace("D", ""))
            p = int(slot_label.split("P")[1])
        except Exception:
            continue
        for c in [c for c in edited.columns if c.startswith("proctor_")]:
            t = row[c]
            if isinstance(t, str) and t and t != "(미배정)":
                if (d, p) in teacher_exclude.get(t, set()):
                    violations.append({"slot": slot_label, "column": c, "name": t})
    if violations:
        st.error("제외 시간 위반 건이 있습니다. 아래 목록을 확인해 수정하세요.")
        st.dataframe(pd.DataFrame(violations))
    else:
        st.success("제외 시간 위반 없음 ✅")

st.markdown("---")

# -----------------------------
# 5) 일자별 시험 시간표(시각화) & 배정 요약
# -----------------------------
st.subheader("5) 일자별 시험 시간표(시각화)")

if num_days > 0:
    tabs = st.tabs([f"{d}일차" for d in range(1, num_days + 1)])
    for d_idx, tab in enumerate(tabs, start=1):
        with tab:
            st.markdown(f"#### 📚 {d_idx}일차 학년별 시간표")
            for g in range(1, num_grades + 1):
                p_cnt = int(periods_by_day_by_grade[d_idx - 1][g - 1])
                if p_cnt <= 0:
                    continue
                st.markdown(f"**{g}학년 (교시수: {p_cnt})**")
                # 열: g-1 ~ g-classes_per_grade 학급, 행: 1~p_cnt 교시
                cols = [f"{g}-{c}" for c in range(1, classes_per_grade + 1)]
                timetable_df = pd.DataFrame("", index=[f"P{p}" for p in range(1, p_cnt + 1)], columns=cols)
                st.dataframe(timetable_df, use_container_width=True)
            st.markdown("**👥 감독 교사 배정 요약**")
            day_rows = edited[edited["slot"].str.startswith(f"D{d_idx}P")]
            st.dataframe(day_rows.reset_index(drop=True), use_container_width=True)

st.markdown("---")
st.subheader("6) 결과 저장")
fn = st.text_input("파일명", value=f"exam_proctoring_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")
st.download_button(
    "CSV로 다운로드",
    data=edited.to_csv(index=False).encode("utf-8-sig"),
    file_name=fn,
    mime="text/csv"
)

st.markdown(
    """
---
### 사용 팁
- 교사 순서를 CSV의 `name` 열에서 원하는 순서로 정렬해 업로드하면, 그 순서대로 라운드로빈 배정됩니다.
- 제외 입력 예시: `D1P2; D3P1` → 1일 2교시, 3일 1교시 배정 제외.
- 필요 인원이 너무 많아 미배정이 생기면: (1) 슬롯당 인원 수를 줄이거나, (2) 제외를 완화하거나, (3) 교사 수를 늘려주세요.
"""
)
