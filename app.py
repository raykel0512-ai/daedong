# streamlit run app.py 로 실행하세요
# 필요 패키지: streamlit, pandas, numpy

import random
from collections import defaultdict
from datetime import datetime

import pandas as pd
import streamlit as st

st.set_page_config(page_title="시험 시감 자동 편성", layout="wide")

st.title("🧮 시험 시감 자동 편성 프로그램")
st.caption("4일간(일수 가변) · 하루별 교시 수를 각각 다르게 설정 가능 · 교사 ~50명 기준 · 가용/제외시간 반영 · **순번 고정 배정** · 수작업 편집·다운로드 가능")

# -----------------------------
# Sidebar: 기본 설정
# -----------------------------
st.sidebar.header("기본 설정")
num_days = st.sidebar.number_input("시험 일수(일)", min_value=1, max_value=10, value=4)

st.sidebar.subheader("학년/학급 구성")
num_grades = st.sidebar.number_input("학년 수", min_value=1, max_value=6, value=3)
classes_per_grade = st.sidebar.number_input("학년별 학급 수(동일)", min_value=1, max_value=20, value=8)

st.sidebar.subheader("하루별·학년별 교시 수 설정")
# periods_by_day_by_grade[d][g] = d일차 g학년 교시 수
periods_by_day_by_grade = []
for d in range(1, num_days+1):
    with st.sidebar.expander(f"{d}일차 교시 수", expanded=(d==1)):
        per_grade = []
        for g in range(1, num_grades+1):
            per_grade.append(
                st.number_input(f"{g}학년", min_value=0, max_value=10, value=2, step=1, key=f"pbdg_{d}_{g}")
            )
        periods_by_day_by_grade.append(per_grade)

proctors_per_slot = st.sidebar.number_input("슬롯당 필요한 감독 교사 수", min_value=1, max_value=30, value=2, help="한 교시(슬롯)마다 필요한 시감 교사 수")
