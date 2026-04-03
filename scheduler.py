# scheduler.py — 시험 시감 자동 배정 알고리즘 v2.3
# 변경사항: 연강 방지, 하루 최대 횟수 제한, 지정석(extra_classes) 최우선 배정 로직 추가

from __future__ import annotations
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Teacher:
    name: str
    role: str = "정부"
    priority: float = 1e9
    exclude_times: set = field(default_factory=set)
    exclude_classes: set = field(default_factory=set)
    exclude_time_class: set = field(default_factory=set)
    extra_classes: set = field(default_factory=set)

_CLASS_PAT = re.compile(r"^(?:C)?(\d+)-(\d+)$")
_TIME_PAT  = re.compile(r"^D(\d+)P(\d+)$")
_DAY_PAT   = re.compile(r"^D(\d+)$")

def parse_exclude_rules(raw: str) -> tuple[set, set, set]:
    exc_t, exc_c, exc_tc = set(), set(), set()
    if not raw or str(raw).strip().lower() in ("nan", "none", ""):
        return exc_t, exc_c, exc_tc
    for tok in raw.split(";"):
        tok = tok.strip()
        if not tok: continue
        if "@" in tok:
            left, right = tok.split("@", 1)
            left = left.strip().upper().replace(" ", "")
            right = right.strip().upper().replace(" ", "")
            m_t = _TIME_PAT.match(left)
            m_c = _CLASS_PAT.match(right)
            if m_t and m_c:
                exc_tc.add((int(m_t.group(1)), int(m_t.group(2)), int(m_c.group(1)), int(m_c.group(2))))
        else:
            up = tok.upper().replace(" ", "")
            m_t = _TIME_PAT.match(up)
            m_c = _CLASS_PAT.match(up)
            if m_t: exc_t.add((int(m_t.group(1)), int(m_t.group(2))))
            elif m_c: exc_c.add((int(m_c.group(1)), int(m_c.group(2))))
    return exc_t, exc_c, exc_tc

def parse_available(raw: str, num_days: int, max_period: int = 10) -> set:
    raw = str(raw).strip()
    if not raw or raw.lower() in ("nan", "none", ""): return set()
    possible: set[tuple[int, int]] = set()
    for tok in raw.split(";"):
        tok = tok.strip().upper().replace(" ", "")
        if not tok: continue
        m_dp = _TIME_PAT.match(tok)
        m_d  = _DAY_PAT.match(tok)
        if m_dp: possible.add((int(m_dp.group(1)), int(m_dp.group(2))))
        elif m_d:
            d = int(m_d.group(1))
            for p in range(1, max_period + 1): possible.add((d, p))
    all_slots = {(d, p) for d in range(1, num_days + 1) for p in range(1, max_period + 1)}
    return all_slots - possible

def parse_extra_classes(raw: str) -> set:
    result = set()
    if not raw or str(raw).strip().lower() in ("nan", "none", ""): return result
    for tok in re.split(r"[;,]", raw):
        tok = tok.strip()
        if not tok: continue
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
    for _, row in df.iterrows():
        name = str(row.get("name", "")).strip()
        if not name: continue
        role_raw = str(row.get("role", "정부")).strip()
        role = "부만" if role_raw in ("부만", "부", "assistant_only") else "정부"
        try: priority = float(row.get("priority") or 1e9)
        except: priority = 1e9
        exc_t, exc_c, exc_tc = parse_exclude_rules(str(row.get("exclude", "")))
        avail_exc = parse_available(str(row.get("available", "")), num_days)
        exc_t |= avail_exc
        extra = parse_extra_classes(str(row.get("extra_classes", "")))
        teachers.append(Teacher(name=name, role=role, priority=priority, exclude_times=exc_t, exclude_classes=exc_c, exclude_time_class=exc_tc, extra_classes=extra))
    return teachers

def can_assign(t: Teacher, d: int, p: int, g: int, c: int) -> bool:
    if (d, p) in t.exclude_times: return False
    if (g, c) in t.exclude_classes: return False
    if (d, p, g, c) in t.exclude_time_class: return False
    return True

def run_assignment(
    teachers: list[Teacher],
    num_days: int,
    num_grades: int,
    classes_per_grade: int,
    periods_by_day_grade: list[list[int]],
    prev_counts: Optional[dict] = None,
    max_per_day: int = 3,
    avoid_consecutive: bool = True
) -> dict[tuple, dict[tuple, tuple]]:
    if not teachers: return {}
    prev = prev_counts or {}
    running_chief = {t.name: prev.get(t.name, {}).get("chief", 0) for t in teachers}
    running_asst  = {t.name: prev.get(t.name, {}).get("assistant", 0) for t in teachers}
    daily_counts = defaultdict(int)

    slots = []
    for d in range(1, num_days + 1):
        max_p = max((int(periods_by_day_grade[d - 1][g - 1]) for g in range(1, num_grades + 1)), default=0)
        for p in range(1, max_p + 1): slots.append((d, p))

    chief_pool = [t for t in teachers if t.role == "정부"]
    asst_pool = teachers
    orig_idx = {t.name: i for i, t in enumerate(teachers)}
    classroom_assignments: dict[tuple, dict[tuple, tuple]] = {}

    for (d, p) in slots:
        active_grades = [g for g in range(1, num_grades + 1) if int(periods_by_day_grade[d - 1][g - 1]) >= p]
        period_chief_cnt, period_asst_cnt = defaultdict(int), defaultdict(int)
        per_slot = {}

        # 연강 방지용: 직전 교시 투입 멤버 확인
        prev_assigned = set()
        if p > 1 and (d, p-1) in classroom_assignments:
            for c_name, a_name in classroom_assignments[(d, p-1)].values():
                if c_name != "(미배정)": prev_assigned.add(c_name)
                if a_name != "(미배정)": prev_assigned.add(a_name)

        for g in active_grades:
            for c in range(1, classes_per_grade + 1):
                chief_name, assistant_name = "(미배정)", "(미배정)"

                # 정감독 정렬
                def key_chief(t: Teacher):
                    is_extra = 0 if (g, c) in t.extra_classes else 1
                    over_limit = 1 if daily_counts[(d, t.name)] >= max_per_day else 0
                    consecutive = 1 if avoid_consecutive and t.name in prev_assigned else 0
                    return (is_extra, over_limit, consecutive, period_chief_cnt[t.name], running_chief[t.name], t.priority, orig_idx[t.name])

                for t in sorted(chief_pool, key=key_chief):
                    if not can_assign(t, d, p, g, c): continue
                    if period_chief_cnt[t.name] > 0: continue
                    chief_name = t.name
                    period_chief_cnt[t.name] += 1
                    running_chief[t.name] += 1
                    daily_counts[(d, t.name)] += 1
                    break

                # 부감독 정렬
                def key_asst(t: Teacher):
                    is_extra = 0 if (g, c) in t.extra_classes else 1
                    over_limit = 1 if daily_counts[(d, t.name)] >= max_per_day else 0
                    consecutive = 1 if avoid_consecutive and t.name in prev_assigned else 0
                    return (is_extra, over_limit, consecutive, period_asst_cnt[t.name], running_asst[t.name], t.priority, orig_idx[t.name])

                for t in sorted(asst_pool, key=key_asst):
                    if t.name == chief_name: continue
                    if not can_assign(t, d, p, g, c): continue
                    if period_asst_cnt[t.name] > 0: continue
                    assistant_name = t.name
                    period_asst_cnt[t.name] += 1
                    running_asst[t.name] += 1
                    daily_counts[(d, t.name)] += 1
                    break
                
                per_slot[(g, c)] = (chief_name, assistant_name)
        classroom_assignments[(d, p)] = per_slot
    return classroom_assignments

def compute_stats(assignments, teacher_list, prev_counts=None):
    prev = prev_counts or {}
    c_chief, c_asst = defaultdict(int), defaultdict(int)
    for ps in assignments.values():
        for ch, ass in ps.values():
            if ch != "(미배정)": c_chief[ch] += 1
            if ass != "(미배정)": c_asst[ass] += 1
    
    all_names = sorted({t.name for t in teacher_list} | set(c_chief.keys()) | set(c_asst.keys()))
    rows = []
    for name in all_names:
        ch_p, as_p = prev.get(name, {}).get("chief", 0), prev.get(name, {}).get("assistant", 0)
        ch_n, as_n = c_chief.get(name, 0), c_asst.get(name, 0)
        t_obj = next((t for t in teacher_list if t.name == name), None)
        rows.append({
            "name": name, "role": t_obj.role if t_obj else "정부", "priority": t_obj.priority if t_obj else None,
            "정감독(이전)": ch_p, "정감독(금번)": ch_n, "정감독(합계)": ch_p + ch_n,
            "부감독(이전)": as_p, "부감독(금번)": as_n, "부감독(합계)": as_p + as_n,
            "총합계": ch_p + ch_n + as_p + as_n
        })
    return rows

def check_violations(assignments, teacher_map):
    violations = []
    for (d, p), ps in assignments.items():
        for (g, c), (ch, ass) in ps.items():
            for role, name in [("정감독", ch), ("부감독", ass)]:
                if name != "(미배정)":
                    t = teacher_map.get(name)
                    if t and not can_assign(t, d, p, g, c):
                        violations.append({"일차": d, "교시": p, "학년": g, "반": c, "역할": role, "교사": name})
    return violations
