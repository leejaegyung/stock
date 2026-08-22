# Architecture Layers

```
Layer 6: app/entrypoints/        ← 최상위. 모든 레이어 import 가능.
           cli.py | web.py | scheduler.py
              ↑
Layer 5: app/core/pipeline.py    ← 오케스트레이터. 에이전트·데이터소스 조율.
              ↑
Layer 4: app/core/agents/        ← LLM 에이전트 9종. datasources, db, config 사용.
              ↑
Layer 3: app/core/datasources/   ← 시장별 데이터 어댑터 (us.py, kr.py). db, config 사용.
              ↑
Layer 2: app/db/                 ← SQLite/SQLAlchemy. config만 import 가능.
              ↑
Layer 1: app/core/formulas.py    ← 순수 함수. 외부 앱 import 절대 없음.
Layer 0: app/config.py           ← 설정. 모든 레이어에서 import 가능.
```

## 규칙 요약

| 레이어 | 파일 | Import 가능 |
|--------|------|-------------|
| 0 | app/config.py | (없음) |
| 1 | app/core/formulas.py | (없음 — 순수 Python 표준 라이브러리만) |
| 2 | app/db/ | Layer 0 |
| 3 | app/core/datasources/ | Layer 0, 1, 2 |
| 4 | app/core/agents/ | Layer 0, 1, 2, 3 |
| 5 | app/core/pipeline.py | Layer 0, 1, 2, 3, 4 |
| 6 | app/entrypoints/ | Layer 0–5 전부 |

## 위반 시

`tests/test_architecture.py`가 CI에서 실패합니다.  
의도적 예외는 `KNOWN_VIOLATIONS` 리스트에 추가하되, 리스트는 줄어들기만 해야 합니다 (ratchet).

## 에이전트 파이프라인 흐름

```
[입력: ticker + date]
        │
        ▼ (Layer 3 datasources — 병렬)
 ┌──────────────────────────────────────┐
 │ get_financials / get_news /          │
 │ get_macro_data / get_peers           │
 └──────────────────────────────────────┘
        │
        ▼ (Layer 4 agents — haiku, 병렬)
 FundamentalAnalyst | NewsSentimentAnalyst
 MacroAnalyst       | IndustryAnalyst
        │
        ▼ (Layer 4 agents — sonnet, 순차)
 BullResearcher ⇄ BearResearcher (2라운드)
        │
        ▼ ResearchManager (sonnet)
        │
        ▼ (Layer 1 formulas — 코드 계산)
 expected_value() + half_kelly()
        │
        ▼ QuantRisk (sonnet) — 해석만
        │
        ▼ ChiefAdvisor (opus)
        │
 [출력: 아침 브리핑 마크다운]
```
