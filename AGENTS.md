# AGENTS.md — 주식 분석 서비스

## What This Is
멀티에이전트 주식 분석 서비스 (Phase 1 MVP).
매일 아침 보유·관심 종목을 자동 분석해 "아침 브리핑 리포트"를 생성한다.
한국/미국 혼합 포트폴리오 지원. 9개 LLM 에이전트가 역할 분담.

## Quick Start
```bash
cp .env.example .env  # ANTHROPIC_API_KEY 입력
pip install -r requirements.txt

python -m app.entrypoints.cli add NVDA --market US --name "NVIDIA"
python -m app.entrypoints.cli add 005930 --market KR --name "삼성전자"
python -m app.entrypoints.cli list
python -m app.entrypoints.cli analyze NVDA --market US
python -m app.entrypoints.cli brief

uvicorn app.entrypoints.web:app --reload   # http://localhost:8000
docker compose up --build                  # 전체 스택
```

## Architecture in 30 Seconds
```
Layer 6: entrypoints/  (cli.py | web.py | scheduler.py)
           ↑ 최상위, 모든 레이어 import 가능
Layer 5: core/pipeline.py
           ↑ 에이전트·datasource 조율, 4-레이어 파이프라인
Layer 4: core/agents/  (9개 에이전트 클래스)
           ↑ haiku(수집) → sonnet(토론) → opus(결론)
Layer 3: core/datasources/  (us.py: yfinance | kr.py: pykrx/stub)
           ↑ market 필드로 KR/US 분기
Layer 2: db/  (SQLAlchemy + SQLite WAL)
Layer 1: core/formulas.py  (순수 함수, 외부 import 없음)
Layer 0: config.py  (모든 레이어 공유)
```

## Key Files
```
app/config.py                    — Settings (pydantic-settings, .env)
app/core/formulas.py             — EV·켈리·CAPE·현금흐름 순수 함수 (Layer 1)
app/core/confidence.py           — 분석 확신도 모델 순수 함수 (Layer 1): 데이터 커버리지·신호
                                   일치도·신호 우위·점수 확신·뉴스 근거 5요소 → 0~100 점수 + 상/중/하
                                   + 근거 + 개선 힌트. _verdict 의 점수구간 확신도를 대체
app/core/translate.py            — 외신 뉴스 한국어 자동 번역 (유틸): 언어감지 + 기계번역(gtx/MyMemory)
                                   체인. LLM 미사용. 원문·원문링크 보존, 결과는 NewsItem 에 캐시
app/core/market_scan.py          — 시장 국면·섹터 모멘텀 순수 함수 (Layer 1)
app/core/trade_plan.py           — 매매 타이밍·가격대 순수 함수 (Layer 1): 이동평균·볼린저·ATR·
                                   스윙 고저 → 매수 구간/분할 추가매수/목표가/손절가/손익비. 리포트에 삽입
app/core/quant.py                — 포트폴리오 계량 분석 순수 함수 (Layer 1): 변동성·샤프·소르티노·
                                   MDD·VaR·베타·상관·분산비율·비중 최적화(동일/역변동성/리스크패리티/
                                   최소분산)·리밸런싱 백테스트. gs-quant timeseries 스타일, 외부 API 없음
app/db/models.py                 — Watchlist, AnalysisReport, NewsItem
app/db/client.py                 — SQLite + WAL 모드
app/core/datasources/us.py       — yfinance 데이터소스 (US)
app/core/datasources/kr.py       — pykrx + stub (KR, Phase 2에서 실연결)
app/core/agents/base.py          — BaseAgent 추상 클래스
app/core/agents/fundamental.py   — FundamentalAnalyst (haiku)
app/core/agents/news_sentiment.py — NewsSentimentAnalyst (haiku)
app/core/agents/macro.py         — MacroAnalyst (haiku)
app/core/agents/industry.py      — IndustryAnalyst (haiku)
app/core/agents/bull.py          — BullResearcher (sonnet)
app/core/agents/bear.py          — BearResearcher (sonnet)
app/core/agents/research_manager.py — ResearchManager (sonnet)
app/core/agents/quant_risk.py    — QuantRisk (sonnet, formulas.py 호출)
app/core/agents/chief_advisor.py — ChiefAdvisor (opus)
app/core/pipeline.py             — analyze_stock() + morning_brief()
app/entrypoints/cli.py           — Typer CLI
app/entrypoints/web.py           — FastAPI + 브리핑 HTML
app/entrypoints/scheduler.py     — APScheduler (KST 10:30 + 감시 루프)
```

## Agent Pipeline
```
[입력: ticker + date]
        │
        ▼ ThreadPoolExecutor (병렬, haiku)
  FundamentalAnalyst | NewsSentimentAnalyst | MacroAnalyst | IndustryAnalyst
        │
        ▼ 토론 2라운드 (sonnet)
  BullResearcher ⇄ BearResearcher → ResearchManager
        │
        ▼ 계량 검증 (sonnet + formulas.py)
  QuantRisk (EV·켈리 계산은 코드, 해석만 LLM)
        │
        ▼ 최종 결론 (opus)
  ChiefAdvisor → 매수/매도/보유/추가매수 + 아침 브리핑 섹션
```

## Sacred Rules
1. **LLM은 해석만 — 계산은 반드시 formulas.py.** QuantRisk가 EV/켈리를 LLM에 맡기면 안 된다.
2. **market 필드(KR/US)로 datasource 분기.** `_get_datasource(market)` 참조.
3. **에이전트는 pipeline.py 경유, 직접 통신 금지.**
4. **레이어 역방향 import 금지 — test_architecture.py가 CI에서 강제.**
5. **JSON 파싱은 항상 try/except + fallback.**

## Environment Variables
```
ANTHROPIC_API_KEY=...
DB_PATH=data/stock_analyst.db
HAIKU_MODEL=claude-haiku-4-5-20251001
SONNET_MODEL=claude-sonnet-4-6
OPUS_MODEL=claude-opus-4-8
BRIEF_CRON=0 7 * * *
WATCHER_INTERVAL_MIN=15
```

## Running Tests
```bash
pytest tests/test_formulas.py -v       # 항상 가능 (순수 함수)
pytest tests/test_quant.py -v          # 항상 가능 (포트폴리오 계량 분석)
pytest tests/test_architecture.py -v  # 항상 가능 (파일 스캔)
pytest tests/ -m "not live" -v        # API key 없이 전체
pytest tests/ -m live -v              # API key 있을 때
```

## DB 초기화 & 커밋 안전장치
- **DB 는 절대 git 에 올라가지 않는다.** `data/`, `*.db*`, `.env` 는 `.gitignore` 대상.
  앱 기동 시 `create_all_tables()` 가 빈 스키마를 자동 생성하므로 커밋할 필요가 없다.
- **훅 설치** (최초 1회): `sh scripts/install_hooks.sh` → pre-commit + **pre-push** 에
  `scripts/guard_secrets.py` 가 걸려, DB 파일 / `.env` / API 키가 섞여 push 되면 차단한다.
- **로컬 DB 비우기**: `python scripts/reset_db.py` (모든 행 삭제, 스키마 유지).
  `--yes` 무확인, `--keep-keys` 는 `user_api_key` 보존.

## Garbage Collection
```bash
python scripts/gc_run_all.py   # 전체 GC 실행
```
매주 월요일 09:00 UTC에 `.github/workflows/gc.yml`이 자동 실행 → GitHub Issue.

## Docker
```bash
docker compose up --build
# 브라우저: http://localhost
# CLI: docker compose exec app python -m app.entrypoints.cli list
```

## Adding a New Agent
1. `app/core/agents/`에 파일 생성 (`BaseAgent` 상속)
2. `model = settings.haiku_model | sonnet_model | opus_model`
3. `system_prompt` 프로퍼티 + `run()` 메서드 구현
4. `pipeline.py`에 통합
5. `docs/architecture/LAYERS.md` 다이어그램 업데이트
6. `tests/test_architecture.py` — KNOWN_VIOLATIONS 갱신 불필요 (레이어 준수 시)

## Roadmap
- Phase 1 (현재): US 실연결(yfinance), KR stub, CLI + 웹 MVP
- Phase 2: KR DART/Naver 실연결, FRED API 거시지표
- Phase 3: watchlist DB CLI 고도화, 스케줄러 안정화
- Phase 4: 실시간 뉴스 감시 루프 고도화, 알림(메신저)
- Phase 5: 계층별 모델 최적화, reflection 단계, 웹소켓 스트림
