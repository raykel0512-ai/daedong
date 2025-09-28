# streamlit run app.py
# 시험 시감 자동 편성 (순번 고정 / 학년·일자별 교시 / 학급 단위 2인 배정: 정·부감독 / 제외: 시간·반·시간+반 / 시각화 중심)

from collections import defaultdict
from datetime import datetime
import re

import pandas as pd
import streamlit as st

st.set_page_config(page_title="시험 시감 자동 편성", layout="wide")

st.title("🧮 시험 시감 자동 편성 프로그램")
st.caption(
    "일수 가변 · **하루별/학년별 교시 수 각각 설정 가능** · 교사 ~50명 기준 · "
    "가용/제외시간 반영 · **순번 고정 배정** · **학급별 2인(정·부감독) 자동 배정** · 인쇄/다운로드용 정리"
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

# 부족 인원 대응 옵션
st.sidebar.subheader("부족 인원 대응 옵션")
allow_multi_classes = st.sidebar.checkbox("같은 교시 여러 교실 담당 허용(중복 배정)", value=False, help="교사가 한 교시 동안 여러 반을 맡을 수 있도록 허용합니다.")
allow_same_person_both_roles = st.sidebar.checkbox("한 반에서 정·부감독을 같은 교사가 겸임 허용", value=False, help="인원이 매우 부족할 때만 권장합니다.")
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

st.sidebar.markdown("---")

# -----------------------------
# 데이터 업로드 & 템플릿
# -----------------------------
st.subheader("1) 교사 명단 업로드")
st.write(
    "CSV 파일을 업로드하세요. **필수 열**: `name`. **선택 열**: `exclude`.\n"
    "- 시간 제외: `D1P2`\n"
    "- 반 제외(모든 시간): `1-3` 또는 `C1-3` (1학년 3반)\n"
    "- 특정 시간+반 제외: `D1P2@1-3` (1일차 2교시의 1학년 3반 제외)\n"
    "여러 항목은 세미콜론(;)으로 구분하세요. 예: `D1P2; 2-4; D2P1@3-7`"
)

# 샘플/템플릿 다운로드
sample_df = pd.DataFrame({
    "name": [f"교사{i:02d}" for i in range(1, 41)],
    "exclude": ["", "D1P2", "2-3", "D2P1@1-4", "", "", "3-7", "D1P1; D3P2@2-1"] + [""] * 32,
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
        "name": [f"교사{i:02d}" for i in range(1, 41)],
        "exclude": [""] * 40,
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
    "- `D1P2` → 1일차 2교시 전체 제외\n"
    "- `1-3` or `C1-3` → 1학년 3반 전체 시간 제외\n"
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
# 배정 알고리즘 (순번 고정 · 라운드로빈)
# -----------------------------
teachers = df_teachers["name"].tolist()
if len(teachers) == 0:
    st.error("교사 명단이 비어 있습니다.")
    st.stop()

# 학급 단위 2인 배정: (정감독, 부감독)
# 원칙: 같은 교시에는 한 교사가 한 교실만 맡음(옵션으로 완화 가능), exclude(시간/반/시간+반) 준수, 순번 고정
classroom_assignments = dict()  # (d,p) -> dict[(g,c)] = (chief, assistant)
class_cursor = 0
N = len(teachers)

for (d, p) in slots:
    active_grades = [g for g in range(1, num_grades + 1) if int(periods_by_day_by_grade[d - 1][g - 1]) >= p]
    slot_taken = set()  # 이 교시에 이미 배정된 교사 (중복 방지)
    per_slot = {}
    for g in active_grades:
        for c in range(1, classes_per_grade + 1):
            pair = []
            checked = 0
            # 1차: 기본 규칙 하에서 선발
            while len(pair) < 2 and checked < N * 6:
                t = teachers[class_cursor % N]
                class_cursor += 1
                checked += 1
                if (d, p) in exclude_time.get(t, set()):
                    continue
                if (g, c) in exclude_class.get(t, set()):
                    continue
                if (d, p, g, c) in exclude_time_class.get(t, set()):
                    continue
                if (not allow_multi_classes) and (t in slot_taken):
                    continue
                if (not allow_same_person_both_roles) and (t in pair):
                    continue
                pair.append(t)
                slot_taken.add(t)
            # 2차: 미배정 백필(옵션에 따라 제약 완화)
            refill_checked = 0
            while len(pair) < 2 and refill_checked < N * 6:
                t = teachers[class_cursor % N]
                class_cursor += 1
                refill_checked += 1
                if (d, p) in exclude_time.get(t, set()):
                    continue
                if (g, c) in exclude_class.get(t, set()):
                    continue
                if (d, p, g, c) in exclude_time_class.get(t, set()):
                    continue
                # 백필 단계에서는 allow_multi_classes/allow_same_person_both_roles 옵션을 반영하여 완화
                if (not allow_same_person_both_roles) and (len(pair) == 1 and t == pair[0]):
                    # 같은 반에서 정/부를 같은 교사가 겸임 금지 시
                    # 단, 다른 교실에서 이미 맡았더라도 allow_multi_classes가 True면 허용
                    if (not allow_multi_classes) and (t in slot_taken):
                        continue
                # 겸임 허용이면 같은 사람 두 번도 허용
                pair.append(t)
                slot_taken.add(t)
            chief = pair[0] if len(pair) > 0 else "(미배정)"
            assistant = pair[1] if len(pair) > 1 else "(미배정)"
            per_slot[(g, c)] = (chief, assistant)
    classroom_assignments[(d, p)] = per_slot

# -----------------------------
# 3) 일자별 시험 시간표 (시각화)
# -----------------------------
st.markdown("---")
st.subheader("3) 일자별 시험 시간표 (시각화)")

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
                # 행을 교시*2로 만들어 정/부를 분리 표기 (P1-정, P1-부, P2-정, P2-부 ...)
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
                st.markdown(f"**{g}학년** (교시수: {p_cnt})")
                st.dataframe(table, use_container_width=True)

# -----------------------------
# 4) 배정 통계 & 검증 (옵션)
# -----------------------------
st.markdown("---")
st.subheader("4) 배정 통계 & 검증")

assign_list = []
for (d, p), per_slot in classroom_assignments.items():
    for (g, c), (chief, assistant) in per_slot.items():
        if chief and chief != "(미배정)":
            assign_list.append(chief)
        if assistant and assistant != "(미배정)":
            assign_list.append(assistant)

if assign_list:
    counts = pd.Series(assign_list).value_counts().rename_axis("name").reset_index(name="assigned_count")
    total_needed = len(assign_list)
    ideal = round(total_needed / max(len(df_teachers["name"].tolist()), 1), 2)
    counts["ideal"] = ideal
    st.dataframe(counts, use_container_width=True)
else:
    st.info("배정 결과가 없습니다. 설정을 확인하세요.")

violations = []
for (d, p), per_slot in classroom_assignments.items():
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
# 5) 결과 저장 (CSV)
# -----------------------------
st.markdown("---")
st.subheader("5) 결과 저장")

flat_rows = []
for (d, p), per_slot in classroom_assignments.items():
    for (g, c), (chief, assistant) in per_slot.items():
        flat_rows.append({
            "day": d, "period": p, "grade": g, "class": c,
            "chief": chief, "assistant": assistant
        })
flat_df = pd.DataFrame(flat_rows)

fn = st.text_input("파일명", value=f"exam_proctoring_classes_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")
st.download_button(
    "학급 배정 CSV 다운로드",
    data=flat_df.to_csv(index=False).encode("utf-8-sig"),
    file_name=fn,
    mime="text/csv"
)

st.markdown(
    """
---
### 사용 팁
- 교사 순서를 CSV의 `name` 열에서 원하는 순서로 정렬해 업로드하면, 그 순서대로 라운드로빈 배정됩니다.
- 제외 입력 예시: `D1P2`(시간), `1-3`(반), `D1P2@1-3`(시간+반). 세미콜론(;)으로 여러 개 입력 가능.
- 학년별 교시 수가 다르면, 해당 일의 **시험이 있는 학년**만 시각화/배정에 포함됩니다.
- 한 교시에는 동일 교사가 여러 교실에 배정되지 않도록 자동 방지됩니다.
"""
)
