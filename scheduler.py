# scheduler.py — 시험 시감 자동 배정 알고리즘 v3.6
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
    priority: int = 999       # 낮을수록 우선 (정수)
    exclude_times: set = field(default_factory=set)
    exclude_classes: set = field(default_factory=set)
    exclude_time_class: set = field(default_factory=set)
    extra_classes: set = field(default_factory=set)

_CLASS_PAT = re.compile(r"^(?:C)?(\d+)-(\d+)$")
_TIME_PAT  = re.compile(r"^D(\d+)P(\d+)$")
_DAY_PAT   = re.compile(r"^D(\d+)$")


# ── 파서 ──────────────────────────────────────────────────────

def parse_exclude_rules(raw: str, max_p: int = 10) -> tuple[set, set, set]:
    exc_t, exc_c, exc_tc = set(), set(), set()
    raw_str = str(raw).strip().upper().replace(" ", "")
    if not raw_str or raw_str.lower() in ("nan", "none", ""):
        return exc_t, exc_c, exc_tc
    for tok in raw_str.split(";"):
        if not tok:
            continue
        if "@" in tok:
            parts = tok.split("@", 1)
            m_t, m_c = _TIME_PAT.match(parts[0]), _CLASS_PAT.match(parts[1])
            if m_t and m_c:
                exc_tc.add((int(m_t.group(1)), int(m_t.group(2)),
                            int(m_c.group(1)), int(m_c.group(2))))
        else:
            m_tp = _TIME_PAT.match(tok)
            m_d  = _DAY_PAT.match(tok)
            m_c  = _CLASS_PAT.match(tok)
            if m_tp:
                exc_t.add((int(m_tp.group(1)), int(m_tp.group(2))))
            elif m_d:
                for p in range(1, max_p + 1):
                    exc_t.add((int(m_d.group(1)), p))
            elif m_c:
                exc_c.add((int(m_c.group(1)), int(m_c.group(2))))
    return exc_t, exc_c, exc_tc


def parse_available_to_exclude(raw: str, num_days: int, max_p: int = 10) -> set:
    raw_str = str(raw).strip().upper().replace(" ", "")
    if not raw_str or raw_str.lower() in ("nan", "none", ""):
        return set()
    available = set()
    for tok in raw_str.split(";"):
        if not tok:
            continue
        m_tp = _TIME_PAT.match(tok)
        m_d  = _DAY_PAT.match(tok)
        if m_tp:
            available.add((int(m_tp.group(1)), int(m_tp.group(2))))
        elif m_d:
            for p in range(1, max_p + 1):
                available.add((int(m_d.group(1)), p))
    all_slots = {(d, p) for d in range(1, num_days + 1) for p in range(1, max_p + 1)}
    return all_slots - available


def parse_extra_classes(raw: str) -> set:
    result = set()
    raw_str = str(raw).strip()
    if not raw_str or raw_str.lower() in ("nan", "none", ""):
        return result
    for tok in re.split(r"[;,]", raw_str):
        tok = tok.strip()
        range_m = re.match(r"^(\d+)-(\d+)~(\d+)$", tok)
        if range_m:
            g = int(range_m.group(1))
            for c in range(int(range_m.group(2)), int(range_m.group(3)) + 1):
                result.add((g, c))
            continue
        single_m = _CLASS_PAT.match(tok.upper().replace(" ", ""))
        if single_m:
            result.add((int(single_m.group(1)), int(single_m.group(2))))
    return result


# ── 교사 빌더 ─────────────────────────────────────────────────

def build_teachers(t_df, p_df, num_days: int = 10) -> list[Teacher]:
    teachers = []
    if not t_df.empty:
        for _, row in t_df.iterrows():
            name = str(row.get("name", "")).strip()
            if not name:
                continue
            exc_t, exc_c, exc_tc = parse_exclude_rules(row.get("exclude", ""))
            extra = parse_extra_classes(row.get("extra_classes", ""))
            try:
                raw_p = row.get("priority")
                prio = int(float(raw_p)) if pd.notnull(raw_p) else 999
            except:
                prio = 999
            teachers.append(Teacher(
                name=name, role="교사", priority=prio,
                exclude_times=exc_t, exclude_classes=exc_c,
                exclude_time_class=exc_tc, extra_classes=extra,
            ))
    if not p_df.empty:
        for _, row in p_df.iterrows():
            name = str(row.get("name", "")).strip()
            if not name:
                continue
            exc_t = parse_available_to_exclude(row.get("available", ""), num_days)
            extra = parse_extra_classes(row.get("extra_classes", ""))
            try:
                raw_p = row.get("priority")
                prio = int(float(raw_p)) if pd.notnull(raw_p) else 999
            except:
                prio = 999
            teachers.append(Teacher(
                name=name, role="학부모", priority=prio,
                exclude_times=exc_t, extra_classes=extra,
            ))
    return teachers


# ── 배정 가능 여부 ────────────────────────────────────────────

def can_assign(t: Teacher, d: int, p: int, g: int, c: int) -> bool:
    if (d, p) in t.exclude_times:
        return False
    if (g, c) in t.exclude_classes:
        return False
    if (d, p, g, c) in t.exclude_time_class:
        return False
    return True


# ── 메인 배정 ────────────────────────────────────────────────

def run_assignment(
    teachers: list[Teacher],
    num_days: int,
    num_grades: int,
    classes_per_grade: int,
    periods_by_day_grade: list[list[int]],
) -> dict:
    """
    배정 우선순위 (v3.6):
    [정감독]
      1. 제외 조건 준수
      2. priority 그룹 내 균등 배정
         - 같은 priority끼리 묶어서, 낮은 priority 그룹이 먼저 채워짐
         - 그룹 내에서는 누적 정감독 횟수 적은 사람 우선

    [부감독]
      1. 제외 조건 준수
      2. 학부모 우선 → 가능한 날 최대 2회/일 채우기
      3. 교사 → 정감독 적게 한 사람 먼저 (총합계 균등)
    """
    if not teachers:
        return {}

    # 누적 카운트
    running_chief: dict[str, int] = defaultdict(int)
    running_asst:  dict[str, int] = defaultdict(int)

    # 학부모 일별 부감독 횟수 추적 (하루 최대 2회 목표)
    parent_daily_asst: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))

    chief_pool = [t for t in teachers if t.role == "교사"]
    asst_pool  = teachers

    # priority 그룹별 정렬 목록 (정감독용)
    priority_groups: dict[int, list[Teacher]] = defaultdict(list)
    for t in chief_pool:
        priority_groups[t.priority].append(t)
    sorted_priorities = sorted(priority_groups.keys())

    # 슬롯 생성
    slots: list[tuple[int, int]] = []
    for d in range(1, num_days + 1):
        max_p = max(
            (int(periods_by_day_grade[d - 1][g - 1]) for g in range(1, num_grades + 1)),
            default=0,
        )
        for p in range(1, max_p + 1):
            slots.append((d, p))

    classroom_assignments: dict = {}

    for (d, p) in slots:
        active_slots = [
            (g, c)
            for g in range(1, num_grades + 1)
            for c in range(1, classes_per_grade + 1)
            if int(periods_by_day_grade[d - 1][g - 1]) >= p
        ]
        per_slot: dict = {}

        # ── 정감독 배정 ──────────────────────────────────────
        # priority 낮은 그룹부터 순서대로, 그룹 내에서는 누적 횟수 적은 순
        # 교시 내에서는 한 교사가 한 반만 담당 (중복 없음)
        period_chief_assigned: set[str] = set()

        for (g, c) in active_slots:
            ch_name = "(미배정)"
            for prio_val in sorted_priorities:
                group = priority_groups[prio_val]
                candidates = sorted(
                    group,
                    key=lambda t: (running_chief[t.name], t.name),
                )
                for t in candidates:
                    if t.name in period_chief_assigned:
                        continue
                    if not can_assign(t, d, p, g, c):
                        continue
                    ch_name = t.name
                    running_chief[t.name] += 1
                    period_chief_assigned.add(t.name)
                    break
                if ch_name != "(미배정)":
                    break

            # 못 채웠으면 중복 허용해서라도 채우기 (인원 부족 대비)
            if ch_name == "(미배정)":
                for prio_val in sorted_priorities:
                    group = priority_groups[prio_val]
                    candidates = sorted(
                        group,
                        key=lambda t: (running_chief[t.name], t.name),
                    )
                    for t in candidates:
                        if not can_assign(t, d, p, g, c):
                            continue
                        ch_name = t.name
                        running_chief[t.name] += 1
                        break
                    if ch_name != "(미배정)":
                        break

            per_slot[(g, c)] = [ch_name, "(미배정)"]

        # ── 부감독 배정 ──────────────────────────────────────
        # 교시 내 한 사람이 여러 반 부감독 가능 (인원 부족 대비)
        period_asst_cnt: dict[str, int] = defaultdict(int)

        for (g, c) in active_slots:
            ch_name = per_slot[(g, c)][0]
            as_name = "(미배정)"

            # 후보 정렬:
            #   학부모: (0, 하루 부감독 2회 미달 여부 역수, 일별 횟수, 누적)
            #   교사:   (1, 정감독 누적 적은 순, 부감독 누적)
            def asst_sort_key(t: Teacher) -> tuple:
                if t.role == "학부모":
                    daily_cnt = parent_daily_asst[t.name][d]
                    under_2   = 0 if daily_cnt < 2 else 1  # 2회 미만이면 우선
                    return (0, under_2, daily_cnt, running_asst[t.name], t.name)
                else:
                    # 교사: 정감독 적게 한 사람 먼저 (총합계 균등)
                    return (1, running_chief[t.name], running_asst[t.name], t.name)

            candidates = sorted(asst_pool, key=asst_sort_key)
            for t in candidates:
                if t.name == ch_name:
                    continue
                if not can_assign(t, d, p, g, c):
                    continue
                # 학부모는 하루 최대 2회 강제 제한
                if t.role == "학부모" and parent_daily_asst[t.name][d] >= 2:
                    continue
                as_name = t.name
                running_asst[t.name] += 1
                period_asst_cnt[t.name] += 1
                if t.role == "학부모":
                    parent_daily_asst[t.name][d] += 1
                break

            per_slot[(g, c)][1] = as_name

        classroom_assignments[(d, p)] = {gc: tuple(v) for gc, v in per_slot.items()}

    return classroom_assignments


# ── 통계 ─────────────────────────────────────────────────────

def compute_teacher_stats(assignments: dict, teacher_list: list[Teacher]) -> list[dict]:
    c_chief: dict[str, int] = defaultdict(int)
    c_asst:  dict[str, int] = defaultdict(int)
    for ps in assignments.values():
        for ch, ass in ps.values():
            if ch  != "(미배정)": c_chief[ch] += 1
            if ass != "(미배정)": c_asst[ass] += 1
    rows = []
    for t in teacher_list:
        if t.role != "교사":
            continue
        rows.append({
            "이름":        t.name,
            "우선순위":    t.priority if t.priority < 999 else "-",
            "정감독(누적)": c_chief[t.name],
            "부감독(누적)": c_asst[t.name],
            "총합계":      c_chief[t.name] + c_asst[t.name],
        })
    rows.sort(key=lambda r: (
        r["우선순위"] if isinstance(r["우선순위"], int) else 999,
        -r["정감독(누적)"],
    ))
    return rows


def compute_parent_stats(assignments: dict, teacher_list: list[Teacher], num_days: int) -> list[dict]:
    daily: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for (d, p), ps in assignments.items():
        for ch, ass in ps.values():
            if ass != "(미배정)":
                daily[ass][d] += 1
    rows = []
    for t in teacher_list:
        if t.role != "학부모":
            continue
        r = {
            "이름":     t.name,
            "우선순위": t.priority if t.priority < 999 else "-",
            "총계":     sum(daily[t.name].values()),
        }
        for d in range(1, num_days + 1):
            r[f"{d}일차"] = f"{daily[t.name][d]}회"
        rows.append(r)
    return rows
