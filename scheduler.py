# scheduler.py — 시험 시감 자동 배정 알고리즘 v3.0
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
    extra_classes: set = field(default_factory=set) # 기피 학급

_CLASS_PAT = re.compile(r"^(?:C)?(\d+)-(\d+)$")
_TIME_PAT  = re.compile(r"^D(\d+)P(\d+)$")
_DAY_PAT   = re.compile(r"^D(\d+)$")

def parse_exclude_rules(raw: str) -> tuple[set, set, set]:
    exc_t, exc_c, exc_tc = set(), set(), set()
    raw_str = str(raw).strip()
    if not raw_str or raw_str.lower() in ("nan", "none", ""): return exc_t, exc_c, exc_tc
    for tok in raw_str.split(";"):
        tok = tok.strip()
        if not tok: continue
        if "@" in tok:
            parts = tok.split("@", 1)
            left, right = parts[0].strip().upper().replace(" ", ""), parts[1].strip().upper().replace(" ", "")
            m_t, m_c = _TIME_PAT.match(left), _CLASS_PAT.match(right)
            if m_t and m_c: exc_tc.add((int(m_t.group(1)), int(m_t.group(2)), int(m_c.group(1)), int(m_c.group(2))))
        else:
            up = tok.upper().replace(" ", "")
            m_t, m_c = _TIME_PAT.match(up), _CLASS_PAT.match(up)
            if m_t: exc_t.add((int(m_t.group(1)), int(m_t.group(2))))
            elif m_c: exc_c.add((int(m_c.group(1)), int(m_c.group(2))))
    return exc_t, exc_c, exc_tc

def parse_available(raw: str, num_days: int, max_period: int = 10) -> set:
    raw_str = str(raw).strip()
    if not raw_str or raw_str.lower() in ("nan", "none", ""): return set()
    possible = set()
    for tok in raw_str.split(";"):
        tok = tok.strip().upper().replace(" ", "")
        m_dp, m_d = _TIME_PAT.match(tok), _DAY_PAT.match(tok)
        if m_dp: possible.add((int(m_dp.group(1)), int(m_dp.group(2))))
        elif m_d:
            d = int(m_d.group(1))
            for p in range(1, max_period + 1): possible.add((d, p))
    all_slots = {(d, p) for d in range(1, num_days + 1) for p in range(1, max_period + 1)}
    return all_slots - possible

def parse_extra_classes(raw: str) -> set:
    result = set()
    raw_str = str(raw).strip()
    if not raw_str or raw_str.lower() in ("nan", "none", ""): return result
    for tok in re.split(r"[;,]", raw_str):
        tok = tok.strip()
        range_m = re.match(r"^(\d+)-(\d+)~(\d+)$", tok)
        if range_m:
            g = int(range_m.group(1)); [result.add((g, c)) for c in range(int(range_m.group(2)), int(range_m.group(3)) + 1)]
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
        role_raw = str(row.get("role", "교사")).strip()
        role = "학부모" if "학부모" in role_raw else "교사"
        try: priority = float(row.get("priority")) if pd.notnull(row.get("priority")) else 1e9
        except: priority = 1e9
        exc_t, exc_c, exc_tc = parse_exclude_rules(row.get("exclude", ""))
        avail_exc = parse_available(row.get("available", ""), num_days)
        exc_t |= avail_exc
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

def run_assignment(
    teachers: list[Teacher],
    num_days: int,
    num_grades: int,
    classes_per_grade: int,
    periods_by_day_grade: list[list[int]],
) -> dict:
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
        active_slots = []
        for g in range(1, num_grades + 1):
            if int(periods_by_day_grade[d - 1][g - 1]) >= p:
                for c in range(1, classes_per_grade + 1): active_slots.append((g, c))
        
        assigned_in_this_period = set() # 한 사람이 같은 교시 여러 반 감독 금지
        per_slot_results = {}

        # 1. 정감독 배정 (교사만, 횟수 균형 우선)
        for (g, c) in active_slots:
            chief_name = "(미배정)"
            sorted_chiefs = sorted(chief_pool, key=lambda t: (running_chief[t.name], t.priority, (orig_idx_map[t.name] - last_idx) % total_t))
            for t in sorted_chiefs:
                if t.name in assigned_in_this_period: continue
                if can_assign(t, d, p, g, c):
                    chief_name = t.name
                    running_chief[t.name] += 1
                    assigned_in_this_period.add(t.name)
                    last_idx = orig_idx_map[t.name]
                    break
            per_slot_results[(g, c)] = [chief_name, "(미배정)"]

        # 2. 부감독 배정 (학부모 우선, 중복 금지)
        for (g, c) in active_slots:
            asst_name = "(미배정)"
            sorted_assts = sorted(asst_pool, key=lambda t: (0 if t.role == "학부모" else 1, running_asst[t.name], t.priority, (orig_idx_map[t.name] - last_idx) % total_t))
            for t in sorted_assts:
                if t.name in assigned_in_this_period: continue
                if can_assign(t, d, p, g, c):
                    asst_name = t.name
                    running_asst[t.name] += 1
                    assigned_in_this_period.add(t.name)
                    last_idx = orig_idx_map[t.name]
                    break
            per_slot_results[(g, c)][1] = asst_name

        classroom_assignments[(d, p)] = {gc: tuple(pair) for gc, pair in per_slot_results.items()}
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
