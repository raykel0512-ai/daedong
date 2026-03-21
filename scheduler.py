"""
scheduler.py
시험 시감 자동 배정 알고리즘
- 날별 교사 명단 지원
- 정감독 / 부감독 전용 교사 구분
- 특정 반 추가 감독 가중치
- 순번 기반 균등 배정
"""

from __future__ import annotations
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


# ──────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────

@dataclass
class Teacher:
    name: str
    role: str = "정부"          # "정부" | "부만"
    priority: float = 1e9
    exclude_times: set = field(default_factory=set)       # {(d, p), ...}
    exclude_classes: set = field(default_factory=set)     # {(g, c), ...}
    exclude_time_class: set = field(default_factory=set)  # {(d, p, g, c), ...}
    extra_classes: set = field(default_factory=set)       # {(g, c), ...}  ← 추가 감독 대상 반


@dataclass
class AssignmentSlot:
    day: int
    period: int
    grade: int
    cls: int
    chief: str = "(미배정)"
    assistant: str = "(미배정)"


# ──────────────────────────────────────────
# 제외 규칙 파서
# ──────────────────────────────────────────

_CLASS_PAT = re.compile(r"^(?:C)?(\d+)-(\d+)$")
_TIME_PAT  = re.compile(r"^D(\d+)P(\d+)$")


def parse_exclude_rules(raw: str) -> tuple[set, set, set]:
    """
    문자열 → (exclude_times, exclude_classes, exclude_time_class)
    """
    exc_t, exc_c, exc_tc = set(), set(), set()
    if not raw or str(raw).strip().lower() in ("nan", "none", ""):
        return exc_t, exc_c, exc_tc

    for tok in raw.split(";"):
        tok = tok.strip()
        if not tok:
            continue

        if "@" in tok:
            left, right = tok.split("@", 1)
            left = left.strip().upper().replace(" ", "")
            right = right.strip().upper().replace(" ", "")
            m_t = _TIME_PAT.match(left)
            m_c = _CLASS_PAT.match(right)
            if m_t and m_c:
                exc_tc.add((int(m_t.group(1)), int(m_t.group(2)),
                            int(m_c.group(1)), int(m_c.group(2))))
        else:
            up = tok.upper().replace(" ", "")
            m_t = _TIME_PAT.match(up)
            m_c = _CLASS_PAT.match(up)
            if m_t:
                exc_t.add((int(m_t.group(1)), int(m_t.group(2))))
            elif m_c:
                exc_c.add((int(m_c.group(1)), int(m_c.group(2))))

    return exc_t, exc_c, exc_tc


def parse_extra_classes(raw: str) -> set:
    """
    '1-4,1-5,1-6' 또는 '4~6' (학년 1 고정) 같은 형태 → {(g,c), ...}
    지원 형식:
      - g-c  (예: 1-4)
      - g-c1~c2  (예: 1-4~6 → 1학년 4,5,6반)
    여러 항목은 쉼표 또는 세미콜론으로 구분
    """
    result = set()
    if not raw or str(raw).strip().lower() in ("nan", "none", ""):
        return result
    for tok in re.split(r"[;,]", raw):
        tok = tok.strip()
        if not tok:
            continue
        # 범위 형식: 1-4~6
        range_m = re.match(r"^(\d+)-(\d+)~(\d+)$", tok)
        if range_m:
            g = int(range_m.group(1))
            for c in range(int(range_m.group(2)), int(range_m.group(3)) + 1):
                result.add((g, c))
            continue
        # 단일: 1-4
        single_m = _CLASS_PAT.match(tok.upper().replace(" ", ""))
        if single_m:
            result.add((int(single_m.group(1)), int(single_m.group(2))))
    return result


# ──────────────────────────────────────────
# 교사 목록 빌더
# ──────────────────────────────────────────

def build_teachers(df) -> list[Teacher]:
    """pandas DataFrame → Teacher 목록"""
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
        extra = parse_extra_classes(str(row.get("extra_classes", "")))

        teachers.append(Teacher(
            name=name,
            role=role,
            priority=priority,
            exclude_times=exc_t,
            exclude_classes=exc_c,
            exclude_time_class=exc_tc,
            extra_classes=extra,
        ))
    return teachers


# ──────────────────────────────────────────
# 배정 가능 여부 판단
# ──────────────────────────────────────────

def can_assign(t: Teacher, d: int, p: int, g: int, c: int) -> bool:
    if (d, p) in t.exclude_times:
        return False
    if (g, c) in t.exclude_classes:
        return False
    if (d, p, g, c) in t.exclude_time_class:
        return False
    return True


# ──────────────────────────────────────────
# 메인 배정 함수
# ──────────────────────────────────────────

def run_assignment(
    teachers: list[Teacher],
    num_days: int,
    num_grades: int,
    classes_per_grade: int,
    periods_by_day_grade: list[list[int]],   # [day-1][grade-1] → int
    prev_counts: Optional[dict] = None,       # {name: {"chief": n, "assistant": m}}
) -> dict[tuple, dict[tuple, tuple]]:
    """
    Returns:
        classroom_assignments[(d, p)][(g, c)] = (chief_name, assistant_name)
    """

    if not teachers:
        return {}

    # ── 누적 카운트 초기화 ──
    prev = prev_counts or {}
    running_chief = {t.name: prev.get(t.name, {}).get("chief", 0) for t in teachers}
    running_asst  = {t.name: prev.get(t.name, {}).get("assistant", 0) for t in teachers}

    # ── 슬롯 정의 ──
    slots: list[tuple[int, int]] = []
    for d in range(1, num_days + 1):
        max_p = max((int(periods_by_day_grade[d - 1][g - 1]) for g in range(1, num_grades + 1)), default=0)
        for p in range(1, max_p + 1):
            slots.append((d, p))

    # ── 정감독 가능 / 부감독 가능 목록 분리 ──
    chief_eligible = [t for t in teachers if t.role == "정부"]
    asst_eligible  = teachers  # 정부 + 부만 모두 부감독 가능

    # ── 교사 정렬 키 (타이브레이크) ──
    orig_idx = {t.name: i for i, t in enumerate(teachers)}
    _MAX_PRIO = 1e9

    def sort_key_chief(t: Teacher):
        # 총 감독 횟수 적은 사람 우선 → priority 큰(저우선) 사람 → 원 순서
        prio_inv = min(t.priority, _MAX_PRIO)  # inf 방지
        return (running_chief[t.name] + running_asst[t.name],
                -prio_inv,
                orig_idx[t.name])

    def sort_key_asst(t: Teacher):
        prio_inv = min(t.priority, _MAX_PRIO)
        return (running_chief[t.name] + running_asst[t.name],
                -prio_inv,
                orig_idx[t.name])

    classroom_assignments: dict[tuple, dict[tuple, tuple]] = {}

    for (d, p) in slots:
        active_grades = [
            g for g in range(1, num_grades + 1)
            if int(periods_by_day_grade[d - 1][g - 1]) >= p
        ]
        slot_taken: set[str] = set()
        per_slot: dict[tuple, tuple] = {}

        for g in active_grades:
            for c in range(1, classes_per_grade + 1):
                chief_name    = "(미배정)"
                assistant_name = "(미배정)"

                # ── 정감독 배정 ──
                for t in sorted(chief_eligible, key=sort_key_chief):
                    if t.name in slot_taken:
                        continue
                    if not can_assign(t, d, p, g, c):
                        continue
                    chief_name = t.name
                    slot_taken.add(t.name)
                    running_chief[t.name] += 1
                    break

                # ── 부감독 배정 ──
                for t in sorted(asst_eligible, key=sort_key_asst):
                    if t.name in slot_taken:
                        continue
                    if t.name == chief_name:
                        continue
                    if not can_assign(t, d, p, g, c):
                        continue
                    assistant_name = t.name
                    slot_taken.add(t.name)
                    running_asst[t.name] += 1
                    break

                per_slot[(g, c)] = (chief_name, assistant_name)

        classroom_assignments[(d, p)] = per_slot

    return classroom_assignments


# ──────────────────────────────────────────
# 통계 계산
# ──────────────────────────────────────────

def compute_stats(
    assignments: dict,
    teacher_list: list[Teacher],
    prev_counts: Optional[dict] = None,
) -> list[dict]:
    """
    각 교사별 정/부 횟수 집계 (이번 배정 + 이전 누적)
    """
    prev = prev_counts or {}
    counts_chief: dict[str, int] = defaultdict(int)
    counts_asst:  dict[str, int] = defaultdict(int)

    for per_slot in assignments.values():
        for (chief, asst) in per_slot.values():
            if chief and chief != "(미배정)":
                counts_chief[chief] += 1
            if asst and asst != "(미배정)":
                counts_asst[asst] += 1

    rows = []
    all_names = sorted(
        {t.name for t in teacher_list}
        | set(counts_chief.keys())
        | set(counts_asst.keys())
    )

    for name in all_names:
        ch_prev  = prev.get(name, {}).get("chief", 0)
        asst_prev = prev.get(name, {}).get("assistant", 0)
        ch_now   = counts_chief.get(name, 0)
        asst_now = counts_asst.get(name, 0)
        t_obj    = next((t for t in teacher_list if t.name == name), None)
        rows.append({
            "name":       name,
            "role":       t_obj.role if t_obj else "정부",
            "priority":   t_obj.priority if t_obj else None,
            "정감독(이전)": ch_prev,
            "정감독(금번)": ch_now,
            "정감독(합계)": ch_prev + ch_now,
            "부감독(이전)": asst_prev,
            "부감독(금번)": asst_now,
            "부감독(합계)": asst_prev + asst_now,
            "총합계":      ch_prev + ch_now + asst_prev + asst_now,
        })

    return rows


# ──────────────────────────────────────────
# 제외 위반 검사
# ──────────────────────────────────────────

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
