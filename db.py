"""
db.py
Supabase 연동 모듈
- 시험 세션(exam session) 단위로 데이터 저장/불러오기
- 배정 결과, 이전 누적 통계 저장
- Streamlit secrets 기반 설정

필요한 Supabase 테이블 DDL (README.md 참조):
  exam_sessions, day_teachers, assignments, cumulative_stats
"""

from __future__ import annotations
import json
import os
import streamlit as st

try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False


def get_client() -> "Client | None":
    """Supabase 클라이언트 반환. 설정 없으면 None."""
    if not SUPABASE_AVAILABLE:
        return None
    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
        return create_client(url, key)
    except Exception:
        return None


# ──────────────────────────────────────────
# 시험 세션 관리
# ──────────────────────────────────────────

def list_sessions(client) -> list[dict]:
    """저장된 시험 세션 목록 반환"""
    try:
        res = client.table("exam_sessions").select("*").order("created_at", desc=True).execute()
        return res.data or []
    except Exception as e:
        st.warning(f"세션 목록 조회 실패: {e}")
        return []


def create_session(client, name: str, meta: dict) -> str | None:
    """새 시험 세션 생성 → session_id 반환"""
    try:
        res = client.table("exam_sessions").insert({
            "name": name,
            "meta": json.dumps(meta, ensure_ascii=False),
        }).execute()
        return res.data[0]["id"] if res.data else None
    except Exception as e:
        st.error(f"세션 생성 실패: {e}")
        return None


def load_session_meta(client, session_id: str) -> dict:
    """세션 메타(기본 설정) 불러오기"""
    try:
        res = client.table("exam_sessions").select("meta").eq("id", session_id).single().execute()
        return json.loads(res.data["meta"]) if res.data else {}
    except Exception:
        return {}


def delete_session(client, session_id: str):
    try:
        client.table("exam_sessions").delete().eq("id", session_id).execute()
    except Exception as e:
        st.error(f"세션 삭제 실패: {e}")


# ──────────────────────────────────────────
# 교사 명단 (일차별)
# ──────────────────────────────────────────

def save_day_teachers(client, session_id: str, day: int, teachers_json: str):
    """일차별 교사 명단 저장 (upsert)"""
    try:
        client.table("day_teachers").upsert({
            "session_id": session_id,
            "day": day,
            "teachers_json": teachers_json,
        }, on_conflict="session_id,day").execute()
    except Exception as e:
        st.error(f"교사 명단 저장 실패 (D{day}): {e}")


def load_day_teachers(client, session_id: str, day: int) -> str | None:
    """일차별 교사 명단 불러오기 → JSON 문자열"""
    try:
        res = (client.table("day_teachers")
               .select("teachers_json")
               .eq("session_id", session_id)
               .eq("day", day)
               .single()
               .execute())
        return res.data["teachers_json"] if res.data else None
    except Exception:
        return None


# ──────────────────────────────────────────
# 배정 결과
# ──────────────────────────────────────────

def save_assignments(client, session_id: str, assignments_json: str):
    """배정 결과 저장 (세션 단위 전체 upsert)"""
    try:
        client.table("assignments").upsert({
            "session_id": session_id,
            "data": assignments_json,
        }, on_conflict="session_id").execute()
    except Exception as e:
        st.error(f"배정 결과 저장 실패: {e}")


def load_assignments(client, session_id: str) -> dict | None:
    """배정 결과 불러오기"""
    try:
        res = (client.table("assignments")
               .select("data")
               .eq("session_id", session_id)
               .single()
               .execute())
        if res.data:
            raw = json.loads(res.data["data"])
            # key 복원: JSON은 str key만 지원 → tuple 복원
            restored = {}
            for k, v in raw.items():
                d, p = map(int, k.split(","))
                inner = {}
                for k2, v2 in v.items():
                    g, c = map(int, k2.split(","))
                    inner[(g, c)] = tuple(v2)
                restored[(d, p)] = inner
            return restored
        return None
    except Exception:
        return None


def assignments_to_json(assignments: dict) -> str:
    """tuple key → str key 변환 후 직렬화"""
    serializable = {}
    for (d, p), per_slot in assignments.items():
        outer_key = f"{d},{p}"
        inner = {}
        for (g, c), (chief, asst) in per_slot.items():
            inner[f"{g},{c}"] = [chief, asst]
        serializable[outer_key] = inner
    return json.dumps(serializable, ensure_ascii=False)


# ──────────────────────────────────────────
# 누적 통계
# ──────────────────────────────────────────

def save_cumulative_stats(client, session_id: str, stats: list[dict]):
    """교사별 누적 정/부 횟수 저장"""
    try:
        rows = [
            {
                "session_id": session_id,
                "name": r["name"],
                "chief_total": r.get("정감독(합계)", 0),
                "assistant_total": r.get("부감독(합계)", 0),
            }
            for r in stats
        ]
        # 기존 삭제 후 재삽입
        client.table("cumulative_stats").delete().eq("session_id", session_id).execute()
        if rows:
            client.table("cumulative_stats").insert(rows).execute()
    except Exception as e:
        st.error(f"누적 통계 저장 실패: {e}")


def load_cumulative_stats(client, session_id: str) -> dict:
    """
    {name: {"chief": n, "assistant": m}} 형태로 반환
    이전 시험 세션 누적값을 이번 배정에 반영할 때 사용
    """
    try:
        res = (client.table("cumulative_stats")
               .select("name, chief_total, assistant_total")
               .eq("session_id", session_id)
               .execute())
        result = {}
        for row in (res.data or []):
            result[row["name"]] = {
                "chief": row["chief_total"],
                "assistant": row["assistant_total"],
            }
        return result
    except Exception:
        return {}
