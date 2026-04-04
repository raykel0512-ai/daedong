# scheduler.py — 시험 시감 자동 배정 알고리즘 v2.6
from __future__ import annotations
import re
import pandas as pd
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Teacher:
    name: str
    role: str = "교사" # "교사" 또는 "학부모"
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
        role_raw = str(row.get("role", "교사")).strip()
        # "학부모" 포함 시 학부모로 분류, 그 외는 교사
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
    prev_counts: Optional[dict] = None,
) -> dict[tuple, dict[tuple, tuple]]:
    if not teachers: return {}
    prev = prev_counts or {}
    # 누적 횟수 관리 (교사 위주)
    running_chief = {t.name: prev.get(t.name, {}).get("chief", 0) for t in teachers}
    running_asst  = {t.name: prev.get(t.name, {}).get("assistant", 0) for t in teachers}

    slots = []
    for d in range(1, num_days + 1):
        max_p = max((int(periods_by_day_grade[d - 1][g - 1]) for g in range(1, num_grades + 1)), default=0)
        for p in range(1, max_p + 1): slots.append((d, p))

    chief_pool = [t for t in teachers if t.role == "교사"] # 교사만 정감독 가능
    asst_pool = teachers # 교사 + 학부모 모두 부감독 가능
    orig_idx = {t.name: i for i, t in enumerate(teachers)}
    classroom_assignments = {}

    for (d, p) in slots:
        active_grades = [g for g in range(1, num_grades + 1) if int(periods_by_day_grade[d - 1][g - 1]) >= p]
        period_chief_cnt, period_asst_cnt = defaultdict(int), defaultdict(int)
        per_slot = {}

        for g in active_grades:
            for c in range(1, classes_per_grade + 1):
                chief_name, assistant_name = "(미배정)", "(미배정)"
                # 정감독 배정 (교사 중에서 선정)
                sorted_chiefs = sorted(chief_pool, key=lambda t: (period_chief_cnt[t.name], running_chief[t.name], t.priority, orig_idx[t.name]))
                for t in sorted_chiefs:
                    if can_assign(t, d, p, g, c):
                        chief_name = t.name
                        period_chief_cnt[t.name] += 1
                        running_chief[t.name] += 1
                        break
                # 부감독 배정 (학부모 우선 순위를 위해 정렬 시 role="학부모"가 먼저 오게 함)
                # 정렬 기준: 현재교시횟수 -> role(학부모우선) -> 누적횟수 -> 우선순위
                sorted_assts = sorted(asst_pool, key=lambda t: (period_asst_cnt[t.name], 0 if t.role=="학부모" else 1, running_asst[t.name], t.priority, orig_idx[t.name]))
                for t in sorted_assts:
                    if t.name != chief_name and can_assign(t, d, p, g, c):
                        assistant_name = t.name
                        period_asst_cnt[t.name] += 1
                        running_asst[t.name] += 1
                        break
                per_slot[(g, c)] = (chief_name, assistant_name)
        classroom_assignments[(d, p)] = per_slot
    return classroom_assignments

def compute_teacher_stats(assignments, teacher_list):
    c_chief, c_asst = defaultdict(int), defaultdict(int)
    for ps in assignments.values():
        for ch, ass in ps.values():
            if ch != "(미배정)": c_chief[ch] += 1
            if ass != "(미배정)": c_asst[ass] += 1
    
    rows = []
    for t in teacher_list:
        if t.role == "교사":
            rows.append({
                "이름": t.name,
                "우선순위": t.priority if t.priority < 1e9 else "-",
                "정감독(누적)": c_chief[t.name],
                "부감독(누적)": c_asst[t.name],
                "총합계": c_chief[t.name] + c_asst[t.name]
            })
    return rows

def compute_parent_stats(assignments, teacher_list, num_days):
    # 날짜별 횟수 체크용
    daily_counts = defaultdict(lambda: defaultdict(int)) # {이름: {일차: 횟수}}
    for (d, p), ps in assignments.items():
        for ch, ass in ps.values():
            if ass != "(미배정)": daily_counts[ass][d] += 1
    
    rows = []
    for t in teacher_list:
        if t.role == "학부모":
            row = {"이름": t.name, "우선순위": t.priority if t.priority < 1e9 else "-"}
            total = 0
            for d in range(1, num_days + 1):
                cnt = daily_counts[t.name][d]
                row[f"{d}일차"] = f"{cnt}회"
                total += cnt
            row["전체합계"] = total
            rows.append(row)
    return rows
