# scheduler.py — 시험 시감 자동 배정 알고리즘 v3.2
from __future__ import annotations
import re
import pandas as pd
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Teacher:
    name: str
    role: str = "교사" 
    priority: float = 1e9
    exclude_times: set = field(default_factory=set)
    exclude_classes: set = field(default_factory=set)
    exclude_time_class: set = field(default_factory=set)
    extra_classes: set = field(default_factory=set) 

_CLASS_PAT = re.compile(r"^(?:C)?(\d+)-(\d+)$")
_TIME_PAT  = re.compile(r"^D(\d+)P(\d+)$")
_DAY_PAT   = re.compile(r"^D(\d+)$") # 날짜만 (예: D1)

def parse_exclude_rules(raw: str, max_p: int = 10) -> tuple[set, set, set]:
    """
    제외 규칙 파싱:
    - D1 : 1일차 전체 제외
    - D1P2 : 1일차 2교시 제외
    - 1-3 : 1학년 3반 전체 제외
    - D1P2@1-3 : 1일차 2교시 1학년 3반만 제외
    """
    exc_t, exc_c, exc_tc = set(), set(), set()
    raw_str = str(raw).strip()
    if not raw_str or raw_str.lower() in ("nan", "none", ""): return exc_t, exc_c, exc_tc
    
    for tok in raw_str.split(";"):
        tok = tok.strip().upper().replace(" ", "")
        if not tok: continue
        
        if "@" in tok:
            parts = tok.split("@", 1)
            m_t, m_c = _TIME_PAT.match(parts[0]), _CLASS_PAT.match(parts[1])
            if m_t and m_c: exc_tc.add((int(m_t.group(1)), int(m_t.group(2)), int(m_c.group(1)), int(m_c.group(2))))
        else:
            m_tp, m_d, m_c = _TIME_PAT.match(tok), _DAY_PAT.match(tok), _CLASS_PAT.match(tok)
            if m_tp: # D1P2 형식
                exc_t.add((int(m_tp.group(1)), int(m_tp.group(2))))
            elif m_d: # D1 형식 (그날 모든 교시 추가)
                d_num = int(m_d.group(1))
                for p in range(1, max_p + 1): exc_t.add((d_num, p))
            elif m_c: # 1-3 형식
                exc_c.add((int(m_c.group(1)), int(m_c.group(2))))
    return exc_t, exc_c, exc_tc

def parse_extra_classes(raw: str) -> set:
    result = set()
    raw_str = str(raw).strip()
    if not raw_str or raw_str.lower() in ("nan", "none", ""): return result
    for tok in re.split(r"[;,]", raw_str):
        tok = tok.strip()
        range_m = re.match(r"^(\d+)-(\d+)~(\d+)$", tok)
        if range_m:
            g = int(range_m.group(1))
            for c in range(int(range_m.group(2)), int(range_m.group(3)) + 1): result.add((g, c))
            continue
        single_m = _CLASS_PAT.match(tok.upper().replace(" ", ""))
        if single_m: result.add((int(single_m.group(1)), int(single_m.group(2))))
    return result

def build_teachers(df, num_days: int = 10) -> list[Teacher]:
    teachers = []
    if df is None or df.empty: return teachers
    for _, row in df.iterrows():
        name = str(row.get("name", "")).strip()
        if not name: continue
        role = str(row.get("role", "교사")).strip()
        try: priority = float(row.get("priority")) if pd.notnull(row.get("priority")) else 1e9
        except: priority = 1e9
        
        # 이제 exclude 열 하나로 모든 제외 규칙 처리
        exc_t, exc_c, exc_tc = parse_exclude_rules(row.get("exclude", ""))
        extra = parse_extra_classes(row.get("extra_classes", "")) 
        
        teachers.append(Teacher(name=name, role=role, priority=priority, 
                                exclude_times=exc_t, exclude_classes=exc_c, 
                                exclude_time_class=exc_tc, extra_classes=extra))
    return teachers

def can_assign(t: Teacher, d: int, p: int, g: int, c: int) -> bool:
    if (d, p) in t.exclude_times: return False
    if (g, c) in t.exclude_classes: return False
    if (d, p, g, c) in t.exclude_time_class: return False
    if (g, c) in t.extra_classes: return False 
    return True

def run_assignment(teachers, num_days, num_grades, classes_per_grade, periods_by_day_grade) -> dict:
    if not teachers: return {}
    running_chief, running_asst = defaultdict(int), defaultdict(int)
    last_idx, total_t = 0, len(teachers)
    orig_idx_map = {t.name: i for i, t in enumerate(teachers)}
    classroom_assignments = {}
    
    slots = []
    for d in range(1, num_days + 1):
        max_p = max((int(periods_by_day_grade[d - 1][g - 1]) for g in range(1, num_grades + 1)), default=0)
        for p in range(1, max_p + 1): slots.append((d, p))

    chief_pool = [t for t in teachers if t.role == "교사"]
    asst_pool = teachers

    for (d, p) in slots:
        active_slots = [(g, c) for g in range(1, num_grades + 1) for c in range(1, classes_per_grade + 1) if int(periods_by_day_grade[d - 1][g - 1]) >= p]
        assigned_in_period = set()
        per_slot = {}

        # 1. 정감독
        for (g, c) in active_slots:
            ch_name = "(미배정)"
            sorted_ch = sorted(chief_pool, key=lambda t: (running_chief[t.name], (orig_idx_map[t.name] - last_idx) % total_t, t.priority))
            for t in sorted_ch:
                if t.name in assigned_in_period: continue
                if can_assign(t, d, p, g, c):
                    ch_name = t.name
                    running_chief[t.name] += 1
                    assigned_in_period.add(t.name)
                    last_idx = (orig_idx_map[t.name] + 1) % total_t
                    break
            per_slot[(g, c)] = [ch_name, "(미배정)"]

        # 2. 부감독
        for (g, c) in active_slots:
            as_name = "(미배정)"
            sorted_as = sorted(asst_pool, key=lambda t: (0 if t.role == "학부모" else 1, running_asst[t.name], (orig_idx_map[t.name] - last_idx) % total_t, t.priority))
            for t in sorted_as:
                if t.name in assigned_period: continue # 이 오타 수정: assigned_in_period
                if t.name in assigned_in_period: continue
                if can_assign(t, d, p, g, c):
                    as_name = t.name
                    running_asst[t.name] += 1
                    assigned_in_period.add(t.name)
                    last_idx = (orig_idx_map[t.name] + 1) % total_t
                    break
            per_slot[(g, c)][1] = as_name

        classroom_assignments[(d, p)] = {gc: tuple(pair) for gc, pair in per_slot.items()}
    return classroom_assignments

def compute_teacher_stats(assignments, teacher_list):
    c_chief, c_asst = defaultdict(int), defaultdict(int)
    for ps in assignments.values():
        for ch, ass in ps.values():
            if ch != "(미배정)": c_chief[ch] += 1
            if ass != "(미배정)": c_asst[ass] += 1
    return [{"이름": t.name, "우선순위": t.priority if t.priority < 1e9 else "-", "정감독(누적)": c_chief[t.name], "부감독(누적)": c_asst[t.name], "총합계": c_chief[t.name] + c_asst[t.name]} for t in teacher_list if t.role == "교사"]

def compute_parent_stats(assignments, teacher_list, num_days):
    daily = defaultdict(lambda: defaultdict(int))
    for (d, p), ps in assignments.items():
        for ch, ass in ps.values():
            if ass != "(미배정)": daily[ass][d] += 1
    rows = []
    for t in teacher_list:
        if t.role == "학부모":
            r = {"이름": t.name, "우선순위": t.priority if t.priority < 1e9 else "-"}
            for d in range(1, num_days + 1): r[f"{d}일차"] = f"{daily[t.name][d]}회"
            rows.append(r)
    return rows
