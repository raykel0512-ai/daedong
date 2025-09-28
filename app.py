# streamlit run app.py 로 실행하세요
# 필요 패키지: streamlit, pandas, numpy

import random
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="시험 시감 자동 편성", layout="wide")

st.title("🧮 시험 시감 자동 편성 프로그램")
st.caption("4일간(일수 가변) · **하루별 교시 수를 각각 다르게 설정 가능** · 교사 ~50명 기준 · 가용/제외시간 반영 · **순번 고정 배정** · 수작업 편집·다운로드 가능")

# -----------------------------
# Sidebar: 기본 설정
# -----------------------------
st.sidebar.header("기본 설정")
num_days = st.sidebar.number_input("시험 일수(일)", min_value=1, max_value=10, value=4)
st.sidebar.subheader("하루별 교시 수 설정")
periods_by_day = []
for d in range(1, num_days+1):
    periods_by_day.append(
        st.sidebar.number_input(f"{d}일차 교시 수", min_value=1, max_value=10, value=2, step=1, key=f"pbd_{d}")
    )

proctors_per_slot = st.sidebar.number_input("슬롯당 필요한 감독 교사 수", min_value=1, max_value=30, value=2, help="한 교시(슬롯)마다 필요한 시감 교사 수")
# 순번 고정 모드: 시드/랜덤 사용 안 함

st.sidebar.markdown("---")

# -----------------------------
# 데이터 업로드 & 템플릿
# -----------------------------
st.subheader("1) 교사 명단 업로드")
st.write(
    "CSV 파일을 업로드하세요. 최소 열: `name`. 선택 열: `exclude` (예: `D1P2; D3P3`), `weight` (배정 가중치, 기본 1)."
)

col_tmpl1, col_tmpl2 = st.columns([1,1])
with col_tmpl1:
    if st.button("샘플 CSV 내려받기"):
        sample = pd.DataFrame({
            "name": [f"교사{i:02d}" for i in range(1, 11)],
            "exclude": ["", "D1P2", "D2P2;D3P1", "", "D1P1;D4P2", "", "D3P2", "", "", "D2P1"],
            "weight": [1,1,1,1,1,1,1,1,1,1],
        })
        st.download_button("sample_teachers.csv 저장", data=sample.to_csv(index=False).encode("utf-8-sig"), file_name="sample_teachers.csv", mime="text/csv")

with col_tmpl2:
    if st.button("빈 템플릿 내려받기"):
        empty = pd.DataFrame({"name": [], "exclude": [], "weight": []})
        st.download_button("teachers_template.csv 저장", data=empty.to_csv(index=False).encode("utf-8-sig"), file_name="teachers_template.csv", mime="text/csv")

uploaded = st.file_uploader("교사 명단 CSV 업로드", type=["csv"]) 

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
    if "weight" not in df_teachers.columns:
        df_teachers["weight"] = 1
    df_teachers["weight"] = pd.to_numeric(df_teachers["weight"], errors="coerce").fillna(1).clip(lower=0.1)
else:
    st.info("샘플 데이터로 미리보기 중입니다. 실제 편성 전 CSV를 업로드하세요.")
    df_teachers = pd.DataFrame({
        "name": [f"교사{i:02d}" for i in range(1, 21)],
        "exclude": ["", "D1P2", "D2P2;D3P1", "", "D1P1;D4P2", "", "D3P2", "", "", "D2P1", "", "", "D1P1", "", "D4P2", "", "", "D3P1", "", ""],
        "weight": [1]*20,
    })

st.dataframe(df_teachers, use_container_width=True)

# -----------------------------
# 슬롯 정의 & 제외 파싱
# -----------------------------
slots = []  # (day, period) 튜플 리스트
for d in range(1, num_days+1):
    for p in range(1, int(periods_by_day[d-1])+1):
        slots.append((d, p))

slot_labels = [f"D{d}P{p}" for d,p in slots]
st.markdown("---")
st.subheader("2) 제외 시간 형식")
st.write("각 교사의 `exclude` 칸에 **세미콜론(;)로 구분**하여 `D<일>P<교시>` 형식으로 입력합니다. 예: `D1P2; D3P1`.")

teacher_exclude = {}
for _, row in df_teachers.iterrows():
    name = str(row["name"]).strip()
    excl_raw = str(row.get("exclude", "")).strip()
    exclusions = set()
    if excl_raw:
        for tok in [t.strip() for t in excl_raw.split(";") if t.strip()]:
            if tok.upper().startswith("D") and "P" in tok.upper():
                try:
                    # Normalize e.g., D1P2
                    upper = tok.upper().replace(" ", "")
                    d_idx = upper.find("D")
                    p_idx = upper.find("P")
                    d = int(upper[d_idx+1:p_idx])
                    p = int(upper[p_idx+1:])
                    exclusions.add((d, p))
                except Exception:
                    pass
    teacher_exclude[name] = exclusions

# -----------------------------
# 배정 알고리즘 (순번 고정 · 라운드로빈)
# 목표:
#  - 각 슬롯당 proctors_per_slot 명 배정
#  - 개인 제외 시간 준수
#  - 업로드된 교사 순서를 그대로 따라 순번 배정 (랜덤/가중치 미사용)

teachers = df_teachers["name"].tolist()
assignments = defaultdict(list)  # slot_label -> [names]
load = defaultdict(int)  # name -> assigned count

if len(teachers) == 0:
    st.error("교사 명단이 비어 있습니다.")
    st.stop()

# 순번 커서 (다음 배정 시작 위치)
cursor = 0
N = len(teachers)

for (d, p) in slots:
    label = f"D{d}P{p}"
    picked = []
    checked = 0  # 무한루프 방지
    while len(picked) < proctors_per_slot and checked < N * 2:
        t = teachers[cursor % N]
        cursor += 1
        checked += 1
        # 제외 시간/중복 체크
        if (d, p) in teacher_exclude.get(t, set()):
            continue
        if t in picked:
            continue
        picked.append(t)
        load[t] += 1
    assignments[label] = picked

# 배정 결과 테이블 생성
rows = []
for (d, p) in slots:
    label = f"D{d}P{p}"
    row = {"slot": label}
    people = assignments[label]
    # 고정 컬럼 수(최대 proctors_per_slot)에 맞춰 채우기
    for i in range(proctors_per_slot):
        row[f"proctor_{i+1}"] = people[i] if i < len(people) else "(미배정)"
    rows.append(row)

schedule_df = pd.DataFrame(rows)

# 미배정 경고
unfilled = (schedule_df == "(미배정)").sum().sum()
if unfilled > 0:
    st.warning(f"일부 슬롯에 미배정 인원이 있습니다: {unfilled} 자리. '슬롯당 필요한 인원 수'를 줄이거나 제외 조건을 완화해 주세요.")

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
# 현재 편집 상태 기준으로 카운트 재계산
assigned_names = []
for c in [c for c in edited.columns if c.startswith("proctor_")]:
    assigned_names += [v for v in edited[c].tolist() if isinstance(v, str) and v and v != "(미배정)"]

counts = pd.Series(assigned_names).value_counts().rename_axis("name").reset_index(name="assigned_count")
# 모든 교사 포함되도록 병합
all_counts = pd.DataFrame({"name": teachers}).merge(counts, how="left", on="name").fillna({"assigned_count": 0})
# 이상 배정 수(이론치): 총 필요 인원 / 교사 수
ideal_per_teacher = round((len(slots) * proctors_per_slot) / max(len(teachers), 1), 2)
all_counts["ideal"] = ideal_per_teacher
all_counts = all_counts.sort_values("assigned_count", ascending=False)

c1, c2 = st.columns([1,1])
with c1:
    st.write("교사별 배정 현황")
    st.dataframe(all_counts, use_container_width=True)
with c2:
    st.write("제외 조건 위반 여부 샘플 검사")
    violations = []
    slot_map = {row["slot"]: row for _, row in edited.iterrows()}
    for slot_label, row in slot_map.items():
        d = int(slot_label.split("P")[0].replace("D", ""))
        p = int(slot_label.split("P")[1])
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
st.subheader("5) 결과 저장")
fn = st.text_input("파일명", value=f"exam_proctoring_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")
st.download_button("CSV로 다운로드", data=edited.to_csv(index=False).encode("utf-8-sig"), file_name=fn, mime="text/csv")

st.markdown("""
---
### 사용 팁
- `weight`를 이용해 특정 교사를 조금 더/덜 배정할 수 있습니다. (예: 담임, 업무 담당자 등 고려)
- 배정 후 표에서 직접 이름을 바꿔 수작업 조정 가능합니다.
- 제외 입력 예시: `D1P2; D3P1` → 1일 2교시, 3일 1교시 배정 제외.
- 좌측 사이드바의 시드를 바꾸면 다른 공정 분배 결과를 얻을 수 있습니다.
- 필요 인원이 너무 많아 미배정이 생기면: (1) 슬롯당 인원 수를 줄이거나, (2) 제외를 완화하거나, (3) 교사 수를 늘려주세요.
"""
)

