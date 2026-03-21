# 시험 시감 자동 편성 v2

## 빠른 시작

```bash
pip install -r requirements.txt
streamlit run app.py
```

---

## Supabase 설정 (공유/협업 기능)

### 1. Supabase 프로젝트 만들기
1. https://supabase.com 가입 → 새 프로젝트 생성
2. **Project Settings → API** 에서 `URL`과 `anon public key` 복사

### 2. 테이블 생성 (SQL Editor에 붙여넣기)

```sql
-- 시험 세션
CREATE TABLE exam_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  meta jsonb DEFAULT '{}',
  created_at timestamptz DEFAULT now()
);

-- 일차별 교사 명단
CREATE TABLE day_teachers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid REFERENCES exam_sessions(id) ON DELETE CASCADE,
  day integer NOT NULL,
  teachers_json text NOT NULL,
  updated_at timestamptz DEFAULT now(),
  UNIQUE(session_id, day)
);

-- 배정 결과
CREATE TABLE assignments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid REFERENCES exam_sessions(id) ON DELETE CASCADE,
  data text NOT NULL,
  updated_at timestamptz DEFAULT now(),
  UNIQUE(session_id)
);

-- 누적 통계
CREATE TABLE cumulative_stats (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid REFERENCES exam_sessions(id) ON DELETE CASCADE,
  name text NOT NULL,
  chief_total integer DEFAULT 0,
  assistant_total integer DEFAULT 0,
  updated_at timestamptz DEFAULT now()
);

-- RLS: 인증 없이 읽기/쓰기 허용 (학교 내부용 — 필요시 제한 가능)
ALTER TABLE exam_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE day_teachers ENABLE ROW LEVEL SECURITY;
ALTER TABLE assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE cumulative_stats ENABLE ROW LEVEL SECURITY;

CREATE POLICY "allow all" ON exam_sessions FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "allow all" ON day_teachers FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "allow all" ON assignments FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "allow all" ON cumulative_stats FOR ALL USING (true) WITH CHECK (true);
```

### 3. secrets.toml 작성

`.streamlit/secrets.toml` 파일을 만들고 아래 내용 작성:

```toml
[supabase]
url = "https://xxxxxxxxxxx.supabase.co"
key = "eyJhbGciOiJIUzI1NiIs..."
```

**Streamlit Community Cloud 배포 시**:
→ 앱 설정 → **Secrets** 탭에 위 내용 그대로 붙여넣기

---

## 폴더 구조

```
├── app.py              # 메인 앱
├── scheduler.py        # 배정 알고리즘
├── db.py               # Supabase 연동
├── requirements.txt
└── .streamlit/
    └── secrets.toml    # (배포 시 Secrets 탭 사용, 커밋 X)
```

---

## CSV 형식

| 열 | 필수 | 설명 | 예시 |
|----|------|------|------|
| `name` | ✅ | 교사명 | 홍길동 |
| `role` | ❌ | `정부` / `부만` | 부만 |
| `exclude` | ❌ | 제외 규칙 (`;` 구분) | `D1P2; 1-3` |
| `extra_classes` | ❌ | 추가 감독 반 | `1-4~6` |
| `priority` | ❌ | 숫자, 작을수록 우선 | `1` |

### 제외 규칙 형식
- `D1P2` → 1일차 2교시 전체 제외
- `1-3` → 1학년 3반 전체 제외
- `D1P2@1-3` → 1일차 2교시 1학년 3반만 제외

### 추가 감독 반 (extra_classes)
- `1-4~6` → 1학년 4, 5, 6반
- `2-1,2-2` → 2학년 1, 2반

---

## 협업 방법
1. Supabase 설정 후 Streamlit Community Cloud 배포
2. **앱 URL 공유** → 동일 URL 접속 시 같은 DB 사용
3. 동일 세션 이름 선택 → 배정 결과 공유
4. 편집 후 **💾 수정 내용 DB에 저장** 클릭 → 상대방 새로고침으로 반영

---

## 주요 변경사항 (v1 → v2)
- ✅ 날별 교사 명단 분리 업로드
- ✅ `role` 열: 정부 / 부만 구분
- ✅ `extra_classes` 열: 특정 반 추가 감독
- ✅ 누적 통계: 정감독/부감독 분리, 이전+금번+합계
- ✅ Supabase 기반 저장/공유/협업
- ✅ 부족 인원 대응 옵션 제거 (코드 단순화)
- ✅ Excel 출력 서식 개선 (색상 구분)
- ✅ 버그 수정: tuple key 직렬화, 날별 배정 독립 실행
