# scheduler.py — 시험 시감 자동 배정 알고리즘 v3.7
from __future__ import annotations
import re
import pandas as pd
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Teacher:
    name: str
    role: str = "교사"        # "교사" | "학부모"
    priority: int = 999       # 낮을수록 우선
    exclude_times: set = field(default_factory=set)
    exclude_classes: set = field(default_factory=set)
    exclude_time_class: set = field(default_factory=set)
    extra_classes: set = field(default_factory=set)

_CLASS_PAT = re.compile(r"^(?:C)?(\d+)-(\d+)$")
_TIME_PAT  = re.compile(r"^D(\d+)P(\d+)$")
_DAY_PAT   = re.compile(r"^D(\d+)$")

def parse_exclude_rules(raw: str, max_p: int = 10) -> tuple[set, set, set]:
    exc_t, exc_c, exc_tc = set(), set(), set()
    raw_str = str(raw).strip().upper().replace(" ", "")
    if not raw_str or raw_str.lower() in ("nan", "none", ""): return exc_t, exc_c, exc_tc
    for tok in raw_str.split(";"):
        if not tok: continue
        if "@" in tok:
            parts = tok.split("@", 1)
            m_t, m_c = _TIME_PAT.match(parts[0]), _CLASS_PAT.match(parts[1])
            if m_t and m_c: exc_tc.add((int(m_t.group(1)), int(m_t.group(2)), int(m_c.group(1)), int(m_c.group(2))))
        else:
            m_tp, m_d, m_c = _TIME_PAT.match(tok), _DAY_PAT.match(tok), _CLASS_PAT.match(tok)
            if m_tp: exc_t.add((int(m_tp.group(1)), int(m_tp.group(2))))
            elif m_d:
                for p in range(1, max_p + 1): exc_t.add((int(m_d.group(1)), p))
            elif m_c: exc_c.add((int(m_c.group(1)), int(m_c.group(2))))
    return exc_t, exc_c, exc_tc

def parse_available_to_exclude(raw: str, num_days: int, max_p: int = 10) -> set:
    raw_str = str(raw).strip().upper().replace(" ", "")
    if not raw_str or raw_str.lower() in ("nan", "none", ""): return set()
    available = set()
    for tok in raw_str.split(";"):
        if not tok: continue
        m_tp, m_d = _TIME_PAT.match(tok), _DAY_PAT.match(tok)
        if m_tp: available.add((int(m_tp.group(1)), int(m_tp.group(2))))
        elif m_d:
            for p in range(1, max_p + 1): available.add((int(m_d.group(1)), p))
    all_slots = {(d, p) for d in range(1, num_days + 1) for p in range(1, max_p + 1)}
    return all_slots - available

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

def build_teachers(t_df, p_df, num_days: int = 10) -> list[Teacher]:
    teachers = []
    if not t_df.empty:
        for _, row in t_df.iterrows():
            name = str(row.get("name", "")).strip()
            if not name: continue
            exc_t, exc_c, exc_tc = parse_exclude_rules(row.get("exclude", ""))
            extra = parse_extra_classes(row.get("extra_classes", ""))
            try:
                raw_p = row.get("priority")
                prio = int(float(raw_p)) if pd.notnull(raw_p) else 999
            except: prio = 999
            teachers.append(Teacher(name=name, role="교사", priority=prio, exclude_times=exc_t, exclude_classes=exc_c, exclude_time_class=exc_tc, extra_classes=extra))
    if not p_df.empty:
        for _, row in p_df.iterrows():
            name = str(row.get("name", "")).strip()
            if not name: continue
            exc_t = parse_available_to_exclude(row.get("available", ""), num_days)
            extra = parse_extra_classes(row.get("extra_classes", ""))
            try:
                raw_p = row.get("priority")
                prio = int(float(raw_p)) if pd.notnull(raw_p) else 999
            except: prio = 999
            teachers.append(Teacher(name=name, role="학부모", priority=prio, exclude_times=exc_t, extra_classes=extra))
    return teachers

def can_assign(t: Teacher, d: int, p: int, g: int, c: int) -> bool:
    if (d, p) in t.exclude_times: return False
    if (g, c) in t.exclude_classes: return False
    if (d, p, g, c) in t.exclude_time_class: return False
    return True

def run_assignment(teachers: list[Teacher], num_days, num_grades, classes_per_grade, periods_by_day_grade) -> dict:
    if not teachers: return {}
    
    running_chief = defaultdict(int)
    running_asst = defaultdict(int)
    parent_daily_asst = defaultdict(lambda: defaultdict(int))
    
    last_idx, total_t = 0, len(teachers)
    orig_idx_map = {t.name: i for i, t in enumerate(teachers)}
    
    chief_pool = [t for t in teachers if t.role == "교사"]
    asst_pool = teachers

    slots = []
    for d in range(1, num_days + 1):
        max_p = max((int(periods_by_day_grade[d - 1][g - 1]) for g in range(1, num_grades + 1)), default=0)
        for p in range(1, max_p + 1): slots.append((d, p))

    classroom_assignments = {}

    for (d, p) in slots:
        active_slots = [(g, c) for g in range(1, num_grades + 1) for c in range(1, classes_per_grade + 1) if int(periods_by_day_grade[d - 1][g - 1]) >= p]
        per_slot, period_assigned = {}, set()

        # 1. 정감독 배정 (우선순위 반영 균등 배정)
        for (g, c) in active_slots:
            ch_name = "(미배정)"
            # 정렬 기준: 1.정감독 횟수 2.우선순위(번호작은순) 3.롤링 순서
            sorted_chiefs = sorted(chief_pool, key=lambda t: (running_chief[t.name], t.priority, (orig_idx_map[t.name] - last_idx) % total_t))
            
            for t in sorted_chiefs:
                if t.name in period_assigned: continue
                if not can_assign(t, d, p, g, c): continue
                ch_name = t.name
                running_chief[t.name] += 1
                period_assigned.add(t.name)
                last_idx = (orig_idx_map[t.name] + 1) % total_t
                break
            per_slot[(g, c)] = [ch_name, "(미배정)"]

        # 2. 부감독 배정
        for (g, c) in active_slots:
            ch_name, as_name = per_slot[(g, c)][0], "(미배정)"
            # 정렬 기준: 1.역할(학부모우선) 2.하루 2회미만 학부모 3.누적 횟수 4.롤링
            def asst_key(t: Teacher):
                if t.role == "학부모":
                    u2 = 0 if parent_daily_asst[t.name][d] < 2 else 1
                    return (0, u2, running_asst[t.name], (orig_idx_map[t.name] - last_idx) % total_t)
                else:
                    # 교사는 정감독+부감독 총합 균등 목표
                    return (1, 0, running_chief[t.name] + running_asst[t.name], (orig_idx_map[t.name] - last_idx) % total_t)

            sorted_assts = sorted(asst_pool, key=asst_key)
            for t in sorted_assts:
                if t.name in period_assigned: continue
                if t.name == ch_name: continue
                if not can_assign(t, d, p, g, c): continue
                if t.role == "학부모" and parent_daily_asst[t.name][d] >= 2: continue
                
                as_name = t.name
                running_asst[t.name] += 1
                period_assigned.add(t.name)
                if t.role == "학부모": parent_daily_asst[t.name][d] += 1
                last_idx = (orig_idx_map[t.name] + 1) % total_t
                break
            per_slot[(g, c)][1] = as_name

        classroom_assignments[(d, p)] = {gc: tuple(v) for gc, v in per_slot.items()}
    return classroom_assignments

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
