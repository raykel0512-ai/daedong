# scheduler.py — 시험 시감 자동 배정 알고리즘 v3.9
# (기존 v3.8 로직 유지 + 데이터 변환 함수 추가)

from __future__ import annotations
import re
import pandas as pd
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

# [Teacher 클래스 및 파싱 함수들 v3.8과 동일]
@dataclass
class Teacher:
    name: str
    role: str = "교사"
    priority: int = 999
    exclude_times: set = field(default_factory=set)
    exclude_classes: set = field(default_factory=set)
    exclude_time_class: set = field(default_factory=set)
    extra_classes: set = field(default_factory=set)

# [중략: parse_exclude_rules, build_teachers 등 v3.8 코드 그대로 유지]
# ... (v3.8의 모든 파싱/배정 함수들)

def run_assignment(teachers: list[Teacher], num_days, num_grades, classes_per_grade, periods_by_day_grade) -> dict:
    # [v3.8의 배정 로직 그대로 유지]
    if not teachers: return {}
    running_chief, running_asst = defaultdict(int), defaultdict(int)
    parent_daily_asst = defaultdict(lambda: defaultdict(int))
    last_idx, total_t = 0, len(teachers)
    orig_idx_map = {t.name: i for i, t in enumerate(teachers)}
    classroom_assignments = {}
    slots = []
    for d in range(1, num_days + 1):
        max_p = max((int(periods_by_day_grade[d - 1][g - 1]) for g in range(1, num_grades + 1)), default=0)
        for p in range(1, max_p + 1): slots.append((d, p))

    for (d, p) in slots:
        active_slots = [(g, c) for g in range(1, num_grades + 1) for c in range(1, classes_per_grade + 1) if int(periods_by_day_grade[d - 1][g - 1]) >= p]
        period_assigned, per_slot = set(), {}
        for (g, c) in active_slots:
            ch_name = "(미배정)"
            sorted_ch = sorted([t for t in teachers if t.role=="교사"], key=lambda t: (running_chief[t.name], t.priority, (orig_idx_map[t.name] - last_idx) % total_t))
            for t in sorted_ch:
                if t.name in period_assigned: continue
                if can_assign(t, d, p, g, c):
                    ch_name = t.name; running_chief[t.name] += 1; period_assigned.add(t.name); last_idx = (orig_idx_map[t.name]+1)%total_t; break
            per_slot[(g, c)] = [ch_name, "(미배정)"]
        for (g, c) in active_slots:
            as_name = "(미배정)"
            prev_asst = classroom_assignments.get((d, p-1), {}).get((g, c), (None, "(미배정)"))[1] if p > 1 else "(없음)"
            sorted_as = sorted(teachers, key=lambda t: (0 if t.role=="학부모" else 1, 0 if parent_daily_asst[t.name][d]<2 else 1, 1 if t.name==prev_asst else 0, running_asst[t.name], (orig_idx_map[t.name]-last_idx)%total_t))
            for t in sorted_as:
                if t.name in period_assigned: continue
                if t.name == per_slot[(g, c)][0]: continue
                if not can_assign(t, d, p, g, c): continue
                if t.role == "학부모" and parent_daily_asst[t.name][d] >= 2: continue
                as_name = t.name; running_asst[t.name] += 1; period_assigned.add(t.name); last_idx = (orig_idx_map[t.name]+1)%total_t; if t.role=="학부모": parent_daily_asst[t.name][d]+=1; break
            per_slot[(g, c)][1] = as_name
        classroom_assignments[(d, p)] = {gc: tuple(v) for gc, v in per_slot.items()}
    return classroom_assignments

# ── v3.9 추가 기능: 데이터베이스(평면 테이블) 변환 ──────────────────────────

def assignments_to_df(assignments: dict) -> pd.DataFrame:
    """배정 결과(dict)를 구글 시트 저장용(DataFrame)으로 변환"""
    data = []
    for (d, p), slots in assignments.items():
        for (g, c), (chief, asst) in slots.items():
            data.append({
                "day": d, "period": p, "grade": g, "class": c, 
                "chief": chief, "assistant": asst
            })
    return pd.DataFrame(data)

def df_to_assignments(df: pd.DataFrame) -> dict:
    """구글 시트 데이터(DataFrame)를 배정 결과(dict)로 복원"""
    assignments = {}
    for _, row in df.iterrows():
        d, p, g, c = int(row['day']), int(row['period']), int(row['grade']), int(row['class'])
        if (d, p) not in assignments: assignments[(d, p)] = {}
        assignments[(d, p)][(g, c)] = (str(row['chief']), str(row['assistant']))
    return assignments

# [이하 통계 함수 v3.8과 동일]
def compute_teacher_stats(assignments, teacher_list):
    c_chief, c_asst = defaultdict(int), defaultdict(int)
    for ps in assignments.values():
        for ch, ass in ps.values():
            if ch != "(미배정)": c_chief[ch] += 1
            if ass != "(미배정)": c_asst[ass] += 1
    rows = []
    for t in teacher_list:
        if t.role != "교사": continue
        rows.append({"이름": t.name, "우선순위": t.priority if t.priority < 999 else "-", "정감독(누적)": c_chief[t.name], "부감독(누적)": c_asst[t.name], "총합계": c_chief[t.name] + c_asst[t.name]})
    return sorted(rows, key=lambda r: (r["우선순위"] if isinstance(r["우선순위"], int) else 999, -r["정감독(누적)"]))

def compute_parent_stats(assignments, teacher_list, num_days):
    daily = defaultdict(lambda: defaultdict(int))
    for (d, p), ps in assignments.items():
        for ch, ass in ps.values():
            if ass != "(미배정)": daily[ass][d] += 1
    rows = []
    for t in teacher_list:
        if t.role != "학부모": continue
        r = {"이름": t.name, "우선순위": t.priority if t.priority < 999 else "-", "총계": sum(daily[t.name].values())}
        for d in range(1, num_days + 1): r[f"{d}일차"] = f"{daily[t.name][d]}회"
        rows.append(r)
    return rows
