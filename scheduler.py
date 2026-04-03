"""
scheduler.py — 시험 시감 자동 배정 알고리즘 v2.2
변경사항:
  - available 열 지원 (가능한 날만 입력, 나머지 자동 exclude)
  - 정감독 횟수 / 부감독 횟수 각각 독립 균형 유지
  - 학부모(부만 + priority 낮음) 우선 부감독 배정
"""

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
        if not tok:
            continue
        if "@" in tok:
            left, right = tok.split("@", 1)
            left  = left.strip().upper().replace(" ", "")
            right = right.strip().upper().replace(" ", "")
            m_t = _TIME_PAT.match(left)
            m_c = _CLASS_PAT.match(right)
            if m_t and m_c:
                exc_tc.add((int(m_t.group(1)), int(m_t.group(2)),
                            int(m_c.group(1)), int(m_c.group(2))))
        else:
            up  = tok.upper().replace(" ", "")
            m_t = _TIME_PAT.match(up)
            m_c = _CLASS_PAT.match(up)
            if m_t:
                exc_t.add((int(m_t.group(1)), int(m_t.group(2))))
            elif m_c:
                exc_c.add((int(m_c.group(1)), int(m_c.group(2))))
    return exc_t, exc_c, exc_tc


def parse_available(raw: str, num_days: int, max_period: int = 10) -> set:
    """
    available 열: 가능한 날/교시만 입력 → 나머지를 exclude_times로 반환
    형식: "D1", "D2", "D1P2" 등, 세미콜론 구분
    빈 값 = 모든 날 가능 (exclude 없음)
    """
    raw = str(raw).strip()
    if not raw or raw.lower() in ("nan", "none", ""):
        return set()

    possible: set[tuple[int, int]] = set()
    for tok in raw.split(";"):
        tok = tok.strip().upper().replace(" ", "")
        if not tok:
            continue
        m_dp = _TIME_PAT.match(tok)
        m_d  = _DAY_PAT.match(tok)
        if m_dp:
            possible.add((int(m_dp.group(1)), int(m_dp.group(2))))
        elif m_d:
            d = int(m_d.group(1))
            for p in range(1, max_period + 1):
                possible.add((d, p))

    all_slots = {(d, p) for d in range(1, num_days + 1) for p in range(1, max_period + 1)}
    return all_slots - possible  # 가능하지 않은 슬롯 → exclude


def parse_extra_classes(raw: str) -> set:
    result = set()
    if not raw or str(raw).strip().lower() in ("nan", "none", ""):
        return result
    for tok in re.split(r"[;,]", raw):
        tok = tok.strip()
        if not tok:
            continue
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


def build_teachers(df, num_days: int = 10) -> list[Teacher]:
    teachers = []
    for _, row in df.iterrows():
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        role_raw = str(row.get("role", "정부")).strip()
        role = "부만" if role_raw in ("부만", "부", "assistant_only") else "정부"
        try:
            priority = float(row.get("priority") or 1e9)
        except (ValueError, TypeError):
            priority = 1e9

        exc_t, exc_c, exc_tc = parse_exclude_rules(str(row.get("exclude", "")))
        avail_exc = parse_available(str(row.get("available", "")), num_days)
        exc_t = exc_t | avail_exc
        extra = parse_extra_classes(str(row.get("extra_classes", "")))

        teachers.append(Teacher(
            name=name, role=role, priority=priority,
            exclude_times=exc_t, exclude_classes=exc_c,
            exclude_time_class=exc_tc, extra_classes=extra,
        ))
    return teachers


def can_assign(t: Teacher, d: int, p: int, g: int, c: int) -> bool:
    if (d, p) in t.exclude_times:
        return False
    if (g, c) in t.exclude_classes:
        return False
    if (d, p, g, c) in t.exclude_time_class:
        return False
    return True


def run_assignment(
    teachers: list[Teacher],
    num_days: int,
    num_grades: int,
    classes_per_grade: int,
    periods_by_day_grade: list[list[int]],
    prev_counts: Optional[dict] = None,
) -> dict[tuple, dict[tuple, tuple]]:
    if not teachers:
        return {}

    prev = prev_counts or {}
    # 정/부 횟수 분리 관리
    running_chief = {t.name: prev.get(t.name, {}).get("chief", 0)     for t in teachers}
    running_asst  = {t.name: prev.get(t.name, {}).get("assistant", 0) for t in teachers}

    slots: list[tuple[int, int]] = []
    for d in range(1, num_days + 1):
        max_p = max((int(periods_by_day_grade[d - 1][g - 1])
                     for g in range(1, num_grades + 1)), default=0)
        for p in range(1, max_p + 1):
            slots.append((d, p))

    chief_pool = [t for t in teachers if t.role == "정부"]
    asst_pool  = teachers  # 정부 + 부만 모두 부감독 가능

    orig_idx = {t.name: i for i, t in enumerate(teachers)}
    _MAX_P = 1e9

    def key_chief(t: Teacher, cnt: dict) -> tuple:
        """정감독: 이 교시 정감독 횟수 → 누적 정감독 횟수 → priority → 원 순서"""
        return (
            cnt[t.name],
            running_chief[t.name],
            min(t.priority, _MAX_P),
            orig_idx[t.name],
        )

    def key_asst(t: Teacher, cnt: dict) -> tuple:
        """부감독: 이 교시 부감독 횟수 → 누적 부감독 횟수 → priority(학부모 우선) → 원 순서"""
        return (
            cnt[t.name],
            running_asst[t.name],
            min(t.priority, _MAX_P),
            orig_idx[t.name],
        )

    classroom_assignments: dict[tuple, dict[tuple, tuple]] = {}

    for (d, p) in slots:
        active_grades = [
            g for g in range(1, num_grades + 1)
            if int(periods_by_day_grade[d - 1][g - 1]) >= p
        ]
        period_chief_cnt: dict[str, int] = defaultdict(int)
        period_asst_cnt:  dict[str, int] = defaultdict(int)
        per_slot: dict[tuple, tuple] = {}

        for g in active_grades:
            for c in range(1, classes_per_grade + 1):
                chief_name     = "(미배정)"
                assistant_name = "(미배정)"

                # 정감독 배정 (부만 제외)
                for t in sorted(chief_pool, key=lambda t, cnt=period_chief_cnt: key_chief(t, cnt)):
                    if not can_assign(t, d, p, g, c):
                        continue
                    chief_name = t.name
                    period_chief_cnt[t.name] += 1
                    running_chief[t.name] += 1
                    break

                # 부감독 배정 (학부모 priority 낮으므로 먼저 들어옴)
                for t in sorted(asst_pool, key=lambda t, cnt=period_asst_cnt: key_asst(t, cnt)):
                    if t.name == chief_name:
                        continue
                    if not can_assign(t, d, p, g, c):
                        continue
                    assistant_name = t.name
                    period_asst_cnt[t.name] += 1
                    running_asst[t.name] += 1
                    break

                per_slot[(g, c)] = (chief_name, assistant_name)

        classroom_assignments[(d, p)] = per_slot

    return classroom_assignments


def compute_stats(
    assignments: dict,
    teacher_list: list[Teacher],
    prev_counts: Optional[dict] = None,
) -> list[dict]:
    prev = prev_counts or {}
    counts_chief: dict[str, int] = defaultdict(int)
    counts_asst:  dict[str, int] = defaultdict(int)

    for per_slot in assignments.values():
        for (chief, asst) in per_slot.values():
            if chief and chief != "(미배정)":
                counts_chief[chief] += 1
            if asst and asst != "(미배정)":
                counts_asst[asst] += 1

    all_names = sorted(
        {t.name for t in teacher_list}
        | set(counts_chief.keys())
        | set(counts_asst.keys())
    )
    rows = []
    for name in all_names:
        ch_prev   = prev.get(name, {}).get("chief", 0)
        asst_prev = prev.get(name, {}).get("assistant", 0)
        ch_now    = counts_chief.get(name, 0)
        asst_now  = counts_asst.get(name, 0)
        t_obj     = next((t for t in teacher_list if t.name == name), None)
        rows.append({
            "name":        name,
            "role":        t_obj.role     if t_obj else "정부",
            "priority":    t_obj.priority if t_obj else None,
            "정감독(이전)": ch_prev,
            "정감독(금번)": ch_now,
            "정감독(합계)": ch_prev + ch_now,
            "부감독(이전)": asst_prev,
            "부감독(금번)": asst_now,
            "부감독(합계)": asst_prev + asst_now,
            "총합계":       ch_prev + ch_now + asst_prev + asst_now,
        })
    return rows


def check_violations(
    assignments: dict,
    teacher_map: dict[str, Teacher],
) -> list[dict]:
    violations = []
    for (d, p), per_slot in assignments.items():
        for (g, c), (chief, asst) in per_slot.items():
            for role_label, name in [("정감독", chief), ("부감독", asst)]:
                if name and name != "(미배정)":
                    t = teacher_map.get(name)
                    if t and not can_assign(t, d, p, g, c):
                        violations.append({
                            "일차": d, "교시": p, "학년": g, "반": c,
                            "역할": role_label, "교사": name,
                        })
    return violations
