# scheduler.py — 시험 시감 자동 배정 알고리즘 v5.0
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
    priority: int = 999
    exclude_times: set = field(default_factory=set)
    exclude_classes: set = field(default_factory=set)
    exclude_time_class: set = field(default_factory=set)
    extra_classes: set = field(default_factory=set) 
    specific_excludes: set = field(default_factory=set) # 복도감독용

_CLASS_PAT = re.compile(r"^(?:C)?(\d+)-(\d+)$")
_TIME_PAT  = re.compile(r"^D(\d+)P(\d+)$")
_DAY_PAT   = re.compile(r"^D(\d+)$")

def parse_exclude_rules(raw: str, max_p: int = 10) -> tuple[set, set, set, set]:
    exc_t, exc_c, exc_tc, spec_t = set(), set(), set(), set()
    raw_str = str(raw).strip().upper().replace(" ", "")
    if not raw_str or raw_str.lower() in ("nan", "none", ""): return exc_t, exc_c, exc_tc, spec_t
    for tok in raw_str.split(";"):
        if not tok: continue
        if "@" in tok:
            parts = tok.split("@", 1)
            m_t, m_c = _TIME_PAT.match(parts[0]), _CLASS_PAT.match(parts[1])
            if m_t and m_c: exc_tc.add((int(m_t.group(1)), int(m_t.group(2)), int(m_c.group(1)), int(m_c.group(2))))
        else:
            m_tp, m_d, m_c = _TIME_PAT.match(tok), _DAY_PAT.match(tok), _CLASS_PAT.match(tok)
            if m_tp:
                d_val, p_val = int(m_tp.group(1)), int(m_tp.group(2))
                exc_t.add((d_val, p_val)); spec_t.add((d_val, p_val))
            elif m_d:
                d_n = int(m_d.group(1))
                for p in range(1, max_p + 1): exc_t.add((d_n, p))
            elif m_c:
                exc_c.add((int(m_c.group(1)), int(m_c.group(2))))
    return exc_t, exc_c, exc_tc, spec_t

def parse_available_to_exclude(raw: str, num_days: int, max_p: int = 10) -> set:
    raw_str = str(raw).strip().upper().replace(" ", "")
    if not raw_str or raw_str.lower() in ("nan", "none", ""): return set()
    available = set()
    for tok in raw_str.split(";"):
        if not tok: continue
        m_tp, m_d = _TIME_PAT.match(tok), _DAY_PAT.match(tok)
        if m_tp: available.add((int(m_tp.group(1)), int(m_tp.group(2))))
        elif m_d:
            d_n = int(m_d.group(1))
            for p in range(1, max_p + 1): available.add((d_n, p))
    return {(d, p) for d in range(1, num_days + 1) for p in range(1, max_p + 1)} - available

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
            exc_t, exc_c, exc_tc, spec_t = parse_exclude_rules(row.get("exclude", ""))
            extra = parse_extra_classes(row.get("extra_classes", ""))
            try: prio = int(float(row.get("priority"))) if pd.notnull(row.get("priority")) else 999
            except: prio = 999
            teachers.append(Teacher(name=name, role="교사", priority=prio, exclude_times=exc_t, exclude_classes=exc_c, exclude_time_class=exc_tc, extra_classes=extra, specific_excludes=spec_t))
    if not p_df.empty:
        for _, row in p_df.iterrows():
            name = str(row.get("name", "")).strip()
            if not name: continue
            exc_t = parse_available_to_exclude(row.get("available", ""), num_days)
            extra = parse_extra_classes(row.get("extra_classes", ""))
            try: prio = int(float(row.get("priority"))) if pd.notnull(row.get("priority")) else 999
            except: prio = 999
            teachers.append(Teacher(name=name, role="학부모", priority=prio, exclude_times=exc_t, extra_classes=extra))
    return teachers

def can_assign(t: Teacher, d: int, p: int, g: int, c: int) -> bool:
    if (d, p) in t.exclude_times: return False
    if (g, c) in t.exclude_classes: return False
    if (d, p, g, c) in t.exclude_time_class: return False
    if (g, c) in t.extra_classes: return False
    return True

def run_assignment(teachers: list[Teacher], num_days, num_grades, classes_per_grade, periods_by_day_grade) -> dict:
    if not teachers: return {}
    running_chief, running_asst = defaultdict(int), defaultdict(int)
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
        period_assigned, per_slot = set(), {}
        for (g, c) in active_slots:
            ch_name = "(미배정)"
            sorted_ch = sorted(chief_pool, key=lambda t: (running_chief[t.name], t.priority, (orig_idx_map[t.name] - last_idx) % total_t))
            for t in sorted_ch:
                if t.name in period_assigned: continue
                if not can_assign(t, d, p, g, c): continue
                ch_name = t.name; running_chief[t.name] += 1; period_assigned.add(t.name); last_idx = (orig_idx_map[t.name] + 1) % total_t; break
            per_slot[(g, c)] = [ch_name, "(미배정)"]
        for (g, c) in active_slots:
            as_name = "(미배정)"
            prev_asst = classroom_assignments.get((d, p-1), {}).get((g, c), (None, "(미배정)"))[1] if p > 1 else "(없음)"
            def as_key(t):
                pen = 1 if t.name == prev_asst else 0
                if t.role == "학부모":
                    u2 = 0 if parent_daily_asst[t.name][d] < 2 else 1
                    return (0, u2, running_asst[t.name], pen, (orig_idx_map[t.name] - last_idx) % total_t)
                else:
                    return (1, running_asst[t.name], running_chief[t.name], pen, (orig_idx_map[t.name] - last_idx) % total_t)
            sorted_as = sorted(asst_pool, key=as_key)
            for t in sorted_as:
                if t.name in period_assigned: continue
                if t.name == per_slot[(g, c)][0]: continue
                if not can_assign(t, d, p, g, c): continue
                if t.role == "학부모" and parent_daily_asst[t.name][d] >= 2: continue
                as_name = t.name; running_asst[t.name] += 1; period_assigned.add(t.name); last_idx = (orig_idx_map[t.name] + 1) % total_t; 
                if t.role == "학부모": parent_daily_asst[t.name][d] += 1
                break
            per_slot[(g, c)][1] = as_name
        classroom_assignments[(d, p)] = {gc: tuple(v) for gc, v in per_slot.items()}
    return classroom_assignments

def compute_teacher_stats(assignments, teacher_list):
    c_chief, c_asst = defaultdict(int), defaultdict(int)
    all_names_in_table = set()
    for ps in assignments.values():
        for ch, ass in ps.values():
            if ch != "(미배정)": c_chief[ch] += 1; all_names_in_table.add(ch)
            if ass != "(미배정)": c_asst[ass] += 1; all_names_in_table.add(ass)
    parent_names = {t.name for t in teacher_list if t.role == "학부모"}
    teacher_map = {t.name: t for t in teacher_list if t.role == "교사"}
    for t in teacher_list:
        if t.role == "교사": all_names_in_table.add(t.name)
    rows = []
    for name in sorted(all_names_in_table):
        if name in parent_names: continue
        t_obj = teacher_map.get(name)
        prio = t_obj.priority if t_obj and t_obj.priority < 999 else "-"
        corridor_count = len(t_obj.specific_excludes) if t_obj else 0
        rows.append({"이름": name, "우선순위": prio, "정감독": c_chief[name], "부감독": c_asst[name], "복도감독": corridor_count, "합계": c_chief[name] + c_asst[name] + corridor_count})
    return sorted(rows, key=lambda x: (str(x["우선순위"]) if x["우선순위"] != "-" else "999", -x["정감독"]))

def compute_parent_stats(assignments, teacher_list, num_days):
    daily = defaultdict(lambda: defaultdict(int))
    for (d, p), ps in assignments.items():
        for ch, ass in ps.values():
            if ass != "(미배정)": daily[ass][d] += 1
    rows = []
    for t in teacher_list:
        if t.role == "학부모":
            r = {"이름": t.name, "합계": sum(daily[t.name].values())}
            for d in range(1, num_days + 1): r[f"{d}일차"] = f"{daily[t.name][d]}회"
            rows.append(r)
    return rows

def assignments_to_df(assignments: dict) -> pd.DataFrame:
    data = []
    for (d, p), slots in assignments.items():
        for (g, c), (chief, asst) in slots.items():
            data.append({"day": d, "period": p, "grade": g, "class": c, "chief": chief, "assistant": asst})
    return pd.DataFrame(data)

def df_to_assignments(df: pd.DataFrame) -> dict:
    assignments = {}
    for _, row in df.iterrows():
        d, p, g, c = int(row['day']), int(row['period']), int(row['grade']), int(row['class'])
        if (d, p) not in assignments: assignments[(d, p)] = {}
        assignments[(d, p)][(g, c)] = (str(row['chief']), str(row['assistant']))
    return assignments
