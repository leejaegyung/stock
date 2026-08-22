# 멀티에이전트 주식 분석 서비스 — 설계 기준서 (v0.1)

> 바이브코딩 시작용 스펙 문서.
> Claude Code / Cursor 등에 이 파일을 컨텍스트로 넣고 `이 스펙대로 구현해줘`로 착수하는 것을 전제로 작성됨.

---

## 1. 프로젝트 개요

**목표**: 매일 아침 보유·관심 종목과 시장 상황을 자동으로 분석해 하나의 "아침 브리핑 리포트"로 받아보는 서비스.

**핵심 아이디어**: 기존에 쓰던 "50년 경력 투자자" 단일 프롬프트를, 증권사 리서치 조직처럼 **역할을 나눈 여러 에이전트**로 분해한다. 각 에이전트가 자기 영역만 깊게 파고, 이후 강세론자(Bull)와 약세론자(Bear)가 토론해서 편향을 상쇄한 뒤, 계량 검증을 거쳐 최종 투자의견을 낸다.

**설계 근거 (논문)**:
- **TradingAgents** (arXiv 2412.20138) — 애널리스트 팀 → 리서치 팀(Bull/Bear 토론) → 리스크 팀 → 매니저의 파이프라인. **본 서비스의 뼈대**. 단, 매매 실행 단계는 "투자의견 종합"으로 대체.
- **FinVision** (ACM ICAIF'24) — 역할별 전문 에이전트 + reflection(자기검증) 모듈이 성능에 크게 기여. → **검증(Critic) 단계** 채택 근거.
- **FinCon** (NeurIPS'24) — conceptual verbal reinforcement로 리스크 통제. → 리스크 팀 설계 참고.
- **LLMs in equity markets** (Front. AI 2025, 84개 연구 리뷰) — 응용/기법 분류 전반 참고.

**설계 철학 3원칙**:
1. **역할 분리** — 한 에이전트가 모든 걸 하지 않는다. 수집/해석/판단을 나눈다.
2. **의도적 반대 의견** — Bull과 Bear를 강제로 붙여 확증편향을 막는다.
3. **계량 검증** — LLM의 서술적 판단을 기대값·켈리·밸류에이션 숫자로 반드시 크로스체크한다.

---

## 2. 시스템 아키텍처

```
[입력: 종목 리스트 + 당일 날짜]
        │
        ▼
┌─────────────────────────────────────────────┐
│  Layer 1 · 애널리스트 팀 (병렬 실행)           │
│  ┌──────────┬──────────┬──────────┬────────┐ │
│  │ 펀더멘털  │ 뉴스/센티│ 거시경제  │ 산업/  │ │
│  │ 애널리스트│ 애널리스트│ 애널리스트│ 경쟁사 │ │
│  └──────────┴──────────┴──────────┴────────┘ │
│         각자 구조화된 분석 노트 산출            │
└─────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│  Layer 2 · 리서치 팀 (토론)                   │
│    Bull 리서처  ⇄  Bear 리서처  (N라운드)      │
│              ▼                                │
│        리서치 매니저 (토론 종합 → 균형 관점)    │
└─────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│  Layer 3 · 계량/리스크 팀                     │
│   밸류에이션(PER·ROE·CAPE) + 기대값·켈리 계산  │
│   → 정성적 결론을 숫자로 검증                  │
└─────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│  Layer 4 · 최종 투자자문 (Chief Advisor)      │
│   매수 / 매도 / 보유 / 추가매수 결론           │
│   + 아침 브리핑 포맷으로 종합                  │
└─────────────────────────────────────────────┘
        │
        ▼
[출력: 아침 브리핑 리포트(md/html)]
```

**실행 모드**:
- **개별 종목 심층 분석** — 특정 종목 요청 시 전체 파이프라인 실행.
- **아침 브리핑** — 보유 종목 전체 + 당일 시장 이슈를 요약 버전으로 매일 실행.

---

## 3. 에이전트 명세

> 각 에이전트는 독립 프롬프트 + 전용 도구(툴)를 가진다. 아래는 구현 시 그대로 시스템 프롬프트 골격으로 쓸 수 있게 작성함.

### 3.1 펀더멘털 애널리스트 (Fundamental Analyst)

- **역할**: 기업 재무 건전성과 밸류에이션의 원천 데이터를 뽑는다. 판단은 하지 않고 **사실과 지표**를 정리한다.
- **필수 산출**:
  - 최근 실적 발표 요약 (매출/영업이익/EPS, 전년·전분기 대비)
  - **PER, ROE** (동종 업계·과거 5년 밴드와 비교)
  - **현금흐름 3분류**: 영업활동 / 투자활동 / 재무활동 (아래 §4.3 패턴 판정)
  - 부채비율, 배당수익률, 예상 이익성장률
- **입력**: 종목 티커
- **도구**: 재무데이터 API, 최근 공시/실적자료
- **출력 형식**: 구조화 JSON (숫자 위주, 서술 최소)

### 3.2 뉴스/센티먼트 애널리스트 (News & Sentiment Analyst)

- **역할**: 당일 기준 시장 이슈와 종목별 최근 뉴스를 수집·요약하고, 촉매(catalyst)/저해(headwind) 요인으로 분류한다.
- **필수 산출**:
  - 당일 증권시장 전반 이슈 (지수·섹터 동향)
  - 종목별 최근 뉴스 헤드라인 + 3줄 요약
  - 각 뉴스의 **촉매 / 중립 / 저해** 태깅 및 근거
  - 정부 정책·규제 이슈 (해당 종목/섹터)
- **입력**: 종목 리스트, 당일 날짜
- **도구**: 웹 검색, 뉴스 API
- **주의**: 오래된 기사 배제(날짜 필터 필수), 출처 명시

### 3.3 거시경제 애널리스트 (Macro Analyst)

- **역할**: 시장 전체의 판을 읽는다. 개별 종목이 아니라 **환경**을 분석한다.
- **필수 산출**:
  - 금리·인플레이션·환율 등 핵심 거시 지표 현황
  - **CAPE(Shiller P/E) 분석** → 미국 시장 버블 수준 판정 (§4.4)
  - **한국 vs 미국 개인 구매력 비교** (PPP, 빅맥지수 등)
  - **한국 vs 미국 물가지수(CPI) 비교**
  - 거시 트렌드 코멘트 (AI, 전기화, 소비심리 등)
- **입력**: 당일 날짜
- **도구**: 웹 검색, 거시경제 데이터 소스(FRED 등)

### 3.4 산업/경쟁사 애널리스트 (Industry & Competitor Analyst)

- **역할**: 대상 기업의 업계 내 경쟁적 위치를 평가하고 경쟁사와 재무를 비교한다.
- **필수 산출**:
  - 산업 구조 및 성장성 개요
  - **경쟁사 2~3곳과 핵심 지표 비교표** (PER·ROE·매출성장·마진)
  - 대상 기업의 상대적 강점/약점 (해자, 점유율)
- **입력**: 종목 티커
- **도구**: 재무데이터 API, 웹 검색

### 3.5 Bull 리서처 (강세론자)

- **역할**: 애널리스트 4명의 노트를 근거로 **매수 논리를 최대한 강하게** 구성한다.
- **규칙**: 반드시 §3.1~3.4의 데이터에 근거. 근거 없는 낙관 금지. Bear의 반박에 재반박한다.

### 3.6 Bear 리서처 (약세론자)

- **역할**: 동일 데이터로 **매도/회피 논리를 최대한 강하게** 구성한다.
- **규칙**: 리스크·고평가·저해 요인을 파고든다. Bull의 주장에 반박한다.

### 3.7 리서치 매니저 (Research Manager)

- **역할**: Bull/Bear 토론을 N라운드(기본 2) 진행시킨 뒤 **균형 잡힌 관점**으로 종합한다.
- **필수 산출**: 양측 핵심 논점 요약 + 어느 쪽이 더 설득력 있는지 판정 + 그 이유.

### 3.8 계량/리스크 팀 (Quant & Risk)

- **역할**: 앞선 정성 판단을 **숫자로 검증**한다. 여기서 결론이 뒤집힐 수 있다.
- **필수 산출**:
  - **기대값** 계산 (§4.1)
  - **켈리 공식** 기반 권장 비중 (§4.2, 하프켈리 적용)
  - 밸류에이션 종합 판정 (PER·ROE·CAPE 결합)
  - 손절/목표가 시나리오
- **입력**: 리서치 매니저 결론 + 펀더멘털 수치
- **주의**: 승률·손익비는 **가정임을 명시**하고 근거를 남긴다.

### 3.9 최종 투자자문 (Chief Advisor)

- **역할**: 전체를 종합해 **매수 / 매도 / 보유 / 추가매수** 중 하나로 결론 내고, 아침 브리핑 포맷으로 출력한다.
- **필수 산출**: 결론 + 확신도(상/중/하) + 핵심 근거 3줄 + 유의사항.

---

## 4. 분석 방법론 (공식 명세)

> LLM에게 계산을 맡기지 말 것. **코드로 계산**하고 LLM은 해석만 하도록 분리하는 것을 권장.

### 4.1 기대값 (Expected Value)

```
기대값 = (이익확률 × 예상이익) − (손실확률 × 예상손실)
```
- 기대값 > 0 → 통계적으로 유리한 베팅
- 이익/손실 확률과 예상 폭은 애널리스트 데이터 + Bull/Bear 토론 근거로 산정하고 **가정을 반드시 명시**

### 4.2 켈리 공식 (Kelly Criterion)

```
F = P − (1 − P) / R
```
- `F` = 권장 투자 비중 (전체 자본 대비)
- `P` = 승률 (이길 확률)
- `R` = 손익비 (예상이익 ÷ 예상손실)
- **주의**: 순수 켈리는 공격적임 → 실무에서는 **하프켈리(F/2)** 권장. F ≤ 0 이면 진입 금지 신호.

### 4.3 현금흐름 패턴 판정

세 활동의 부호 조합으로 기업 상태를 읽는다.

| 영업 | 투자 | 재무 | 해석 |
|------|------|------|------|
| + | − | − | **우량 성숙기** (본업 흑자, 투자하며 부채상환·배당) |
| + | − | + | 성장기 (본업 흑자, 외부자금 조달해 공격적 투자) |
| + | + | − | 자산 매각 후 부채상환 (구조조정 신호일 수 있음) |
| − | + | + | **위험** (본업 적자를 자산매각·차입으로 메움) |

### 4.4 CAPE (Shiller P/E) — 시장 버블 판정

```
CAPE = 현재 실질 주가지수 / 최근 10년 평균 실질 EPS
```
- 경기 변동을 평활화한 밸류에이션 지표
- 역사적 평균 대비 현재값 위치로 **미국 시장 고평가/버블 수준**을 판정
- 개별 종목이 아닌 **시장 전체 리스크 컨텍스트**로 사용

### 4.5 PER / ROE 가치 판단

- **저PER + 고ROE** = 이상적 (저평가 우량주 후보)
- **고PER** 은 이익성장률(PEG)로 정당화되는지 확인
- 반드시 **동종 업계·과거 밴드와 상대 비교** (절대값만으로 판단 금지)

---

## 5. 데이터 소스 (구현 시 결정)

> **관심종목이 한국·미국 혼합**이므로, 종목의 `market` 필드(`KR`/`US`)에 따라 데이터 소스를 분기한다. 이건 아키텍처의 핵심 제약이다 — 하나의 API로 양쪽을 다 커버할 수 없다.

### 5.1 미국(US) 종목

| 용도 | 후보 |
|------|------|
| 재무제표/PER/ROE/현금흐름 | yfinance, Alpha Vantage, FMP |
| 실시간 뉴스/동향 | Finnhub, Polygon, NewsAPI + 웹 검색 |
| 시세 | yfinance, Polygon |

### 5.2 한국(KR) 종목

| 용도 | 후보 |
|------|------|
| 재무제표/PER/ROE/현금흐름 | 네이버 금융, KRX 정보데이터시스템, DART(공시) API |
| 실시간 뉴스/동향 | 네이버 뉴스 검색, 한경/연합인포맥스 등 + 웹 검색 |
| 시세 | 네이버 금융, KRX, pykrx 라이브러리 |

### 5.3 시장 공통(거시)

| 용도 | 후보 |
|------|------|
| 미국 거시지표/CPI | FRED API |
| 한국 거시지표/CPI | 한국은행 ECOS API, 통계청(KOSIS) |
| CAPE | Shiller 데이터셋 / multpl.com |
| 구매력(PPP·빅맥) | World Bank / The Economist |

> **주의점**: (1) 미국·한국 회계기준·공시 주기가 달라 PER/ROE 산출 방식이 상이 → 시장별 어댑터로 정규화. (2) 시차 — 한국은 KST 장마감 후, 미국은 다음날 새벽. 아침 브리핑 실행 시각을 KST 아침으로 두면 미국은 전일 종가, 한국은 전일 마감 기준이 자연스럽다.

> 무료 티어로 시작하고, 레이트리밋·유료 전환은 로드맵 후반에 결정.

### 5.4 관심종목(Watchlist) 관리

관심종목 추가/삭제가 서비스의 핵심 일상 동작이다. DB 테이블로 영속화한다.

```sql
-- SQLite
CREATE TABLE watchlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,          -- 예: 'NVDA', '005930'
    name        TEXT NOT NULL,          -- 예: 'NVIDIA', '삼성전자'
    market      TEXT NOT NULL,          -- 'US' | 'KR'
    added_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(ticker, market)
);
```

- **추가 CLI 예시**: `add NVDA --market US` / `add 005930 --market KR`
- `market` 값에 따라 §5.1~5.2 데이터 소스가 자동 분기됨
- 아침 브리핑은 이 테이블 전체를 순회 실행

---

## 5.5 실시간 뉴스 & 동향 모니터링

> 관심종목의 **실시간 뉴스/동향**은 별도 파이프라인으로 다룬다. 전체 4계층 분석(비용·시간 큼)을 매번 돌리지 않고, 가벼운 감시 → 필요 시 심층 분석 트리거 구조로 설계한다.

### 감시 루프 (Watcher)

```python
def watch_loop(watchlist, interval_min=15):
    for stock in watchlist:
        src = news_source_for(stock.market)   # KR/US 분기
        items = src.fetch_latest(stock.ticker, since=last_check)
        for it in items:
            impact = quick_classify(it)        # 저렴한 모델: 촉매/중립/저해
            if impact in ("촉매", "저해"):
                alert(stock, it, impact)        # 즉시 알림
            store(stock, it, impact)            # 타임라인 축적
```

- **경량 분류**: 뉴스 1건당 저렴/빠른 모델로 촉매·중립·저해 + 한줄 요약만. 전체 파이프라인은 돌리지 않는다.
- **알림 트리거**: 촉매/저해로 분류되거나, 특정 종목에 뉴스가 급증하면 알림(메신저/메일). 원하면 그 종목만 전체 심층분석 자동 실행.
- **폴링 vs 스트리밍**: 무료 시작 단계는 N분 간격 폴링. 이후 Finnhub/Polygon 웹소켓 등 실시간 스트림으로 승급 가능.
- **중복 제거**: 같은 기사 재알림 방지 위해 URL/제목 해시로 dedupe.

### 실시간 동향 카드 (아침 브리핑에 삽입)

```markdown
## 🔔 관심종목 실시간 동향 (최근 24h)
### {티커} — {회사명} [{KR|US}]
- 🟢/🔴/⚪ {한줄 요약}  ({출처}, {시각})
- 주가 동향: {전일 대비 %}, 거래량 {특이사항}
```

> 주의: 실시간이라도 아침 브리핑 본문의 심층 분석과는 **레이어를 분리**한다. 감시 루프는 "무슨 일이 있었나"만, 심층 분석(§3)은 "그래서 어떻게 하나"를 담당.

---

## 6. 출력 포맷 — 아침 브리핑

```markdown
# 📈 아침 브리핑 — {날짜}

## 🌍 오늘의 시장
- 지수/섹터 동향: ...
- 거시 코멘트 (금리·환율·CPI): ...
- CAPE 판정: {수치} → {저평가/적정/고평가/버블}
- 한미 구매력·물가 비교: ...

## 📊 보유 종목별 브리핑
### {티커} — {회사명}
- **결론**: 🟢매수 / 🔴매도 / 🟡보유 / 🔵추가매수  (확신도: 상/중/하)
- 핵심 뉴스: ...
- 밸류에이션: PER {} · ROE {} · 현금흐름 {패턴}
- 경쟁사 비교: ...
- 기대값: {값}  |  켈리 권장비중: {값}(하프켈리)
- Bull vs Bear 요약: ...
- 유의사항: ...

## ⚠️ 오늘의 주의 신호
- ...
```

---

## 7. 워크플로우 (의사코드)

```python
def analyze_stock(ticker, date):
    # Layer 1: 병렬 수집
    fund   = fundamental_analyst(ticker)
    news   = news_analyst(ticker, date)
    macro  = macro_analyst(date)              # 종목 무관, 캐싱 가능
    peers  = industry_analyst(ticker)

    context = merge(fund, news, macro, peers)

    # Layer 2: 토론 (N라운드)
    debate = bull_bear_debate(context, rounds=2)
    balanced = research_manager(debate)

    # Layer 3: 계량 검증 (코드로 계산)
    ev     = expected_value(P, gain, loss)
    kelly  = kelly_fraction(P, R) / 2         # 하프켈리
    valuation = judge_valuation(fund, macro.cape)

    # Layer 4: 최종 결론
    return chief_advisor(balanced, ev, kelly, valuation)

def morning_brief(portfolio, date):
    macro = macro_analyst(date)               # 1회만
    return [analyze_stock(t, date) for t in portfolio], macro
```

**모델 배치 전략 (TradingAgents 참고)**: 단순 데이터 수집·요약 에이전트는 저렴/빠른 모델, 토론·최종판단은 고성능 모델로 나눠 비용 최적화.

---

## 8. 기술 스택 (확정)

> **1인 사용 전용 서비스**. 동시성·확장성보다 개발 속도와 운영 단순함을 우선한다.

- **언어**: **Python** — 참고 논문 구현체(TradingAgents/FinVision)와 동일 생태계. 금융 데이터(yfinance, pykrx)·에이전트 오케스트레이션(LangGraph) 라이브러리가 모두 Python에 있어 재사용이 쉽다.
- **오케스트레이션**: LangGraph (또는 직접 구현). 에이전트 그래프·상태 관리에 적합.
- **DB**: **SQLite (WAL 모드)** — 서버 프로세스 없이 파일 하나로 운영. 백업은 파일 복사. 1인 워크로드(아침 배치 + 감시 루프)엔 충분.
  - `PRAGMA journal_mode=WAL;` 필수 — §5.5 감시 루프가 브리핑과 동시에 DB에 쓸 때 잠금 병목 방지.
  - ORM은 SQLAlchemy 권장 (추후 PostgreSQL 이관 대비).
- **LLM**: Claude API (계층별 모델 분리 — Haiku/Sonnet/Opus 혼용).
- **계산 로직**: 공식(§4)은 **순수 함수로 코드 구현**, LLM은 해석만.
- **스케줄러**: cron 또는 APScheduler로 매일 아침(KST) 자동 실행.
- **웹 계층**: **nginx**(리버스 프록시 + 정적 서빙) + **FastAPI**(앱 서버). 아침 브리핑 html·대시보드를 브라우저로 확인. 상세는 §9.4.
- **출력**: md → html 렌더 (또는 메일/메신저 발송).

> **확장 시점**: 데이터가 수 GB를 넘거나 복잡한 분석 쿼리·본격 동시쓰기가 필요해지면 PostgreSQL로 이관. SQLAlchemy를 쓰면 이관 부담이 작다.

> 관심종목(watchlist)은 SQLite로 관리하며, 각 종목은 `{ticker, name, market(KR/US)}` 형태로 저장한다. 종목 추가/삭제가 서비스의 일상 동작이므로 §5.4의 스키마·CLI를 갖춘다. (구체 종목 리스트는 사용자 확정 후 시드로 입력)

---

## 9. 배포 아키텍처 (도커)

> **핵심**: 분석 로직·에이전트 코어는 배포 방식과 무관하게 두고, 진입점/DB접근/스케줄링만 얇은 어댑터로 분리한다. 지금은 도커 단일 배포지만 이 구조를 지키면 나중에 다른 환경으로 옮기기도 쉽다.

### 9.1 코드 구조 (환경 독립 코어 + 어댑터)

```
app/
├── core/               # 배포 무관 — 서비스 본체
│   ├── agents/         # 애널리스트·Bull/Bear·리서치매니저·계량·자문
│   ├── formulas.py     # 기대값·켈리·CAPE·현금흐름 (순수 함수)
│   ├── datasources/    # KR/US 시장별 데이터 어댑터
│   └── pipeline.py     # analyze_stock(), morning_brief()
├── db/
│   ├── client.py       # SQLite 연결 (WAL 모드)
│   └── models.py       # SQLAlchemy 스키마 (watchlist 등)
├── entrypoints/
│   ├── cli.py          # add/analyze/brief 명령
│   ├── scheduler.py    # APScheduler 상시 루프 (브리핑 + 감시)
│   └── web.py          # FastAPI 앱 — 브리핑 조회·대시보드 (§9.4)
├── static/             # 렌더된 브리핑 html·자산 (nginx가 직접 서빙)
├── data/               # SQLite 파일 (볼륨 마운트로 영속화)
├── nginx/
│   └── default.conf    # 리버스 프록시 + 정적 서빙 설정
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

### 9.2 Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# 웹(FastAPI) + 스케줄러를 함께 기동.
# 간단하게는 FastAPI의 lifespan에서 APScheduler를 함께 시작하고 uvicorn으로 실행:
CMD ["uvicorn", "entrypoints.web:app", "--host", "0.0.0.0", "--port", "8000"]
# (스케줄러만 단독 운용하려면: python -m entrypoints.scheduler)
```

> 웹과 스케줄러를 한 프로세스로 묶으면 관리가 단순하다(FastAPI `lifespan`에서 APScheduler 기동). 부하가 커지면 app 컨테이너를 web용·scheduler용으로 분리한다.

### 9.3 운용

- **DB 영속화**: 볼륨 마운트로 SQLite 파일을 컨테이너 밖에 저장 → `-v ./data:/app/data`
- **상시 동작**: 스케줄러(APScheduler)가 컨테이너 안에서 계속 돌며 아침 브리핑(KST 07:00)과 감시 루프(§5.5, N분 간격)를 실행.
- **수동 실행**: 필요 시 CLI로 `docker compose exec app python -m entrypoints.cli analyze NVDA` 처럼 개별 분석.
- **백업**: `data/` 디렉터리 파일 복사만으로 완료.
- **환경변수**: Claude API 키, 각 데이터소스 API 키는 `.env`로 주입 (`--env-file`).

### 9.4 웹 계층 (nginx + FastAPI)

브라우저로 아침 브리핑을 확인하고, 관심종목 추가·개별 분석을 웹에서 트리거하기 위한 계층. **nginx가 앞단, FastAPI가 뒷단**에 서는 표준 구성이다.

**역할 분담**:
- **nginx** — ① 정적 파일(`static/`의 브리핑 html·CSS·JS)을 직접 서빙, ② 동적 요청(`/api/*`)은 FastAPI로 리버스 프록시, ③ 도메인 구매 후 TLS(HTTPS) 종료 지점, ④ 기본 접근 제어(1인용이므로 Basic Auth 또는 IP 화이트리스트 권장).
- **FastAPI** — 브리핑 조회 API, watchlist 추가/삭제, 개별 종목 분석 트리거. `core/pipeline.py`를 그대로 호출하므로 로직 중복 없음.

**nginx 설정 예시** (`nginx/default.conf`):

```nginx
server {
    listen 80;
    server_name _;                      # 도메인 구매 후 실제 도메인으로 교체

    # 정적 브리핑 파일은 nginx가 직접 서빙 (앱 서버 부담 감소)
    location /static/ {
        alias /srv/static/;
        expires 1h;
    }

    # 동적 요청은 FastAPI(app:8000)로 프록시
    location / {
        proxy_pass         http://app:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 600s;        # LLM 분석이 길어질 수 있어 넉넉히
    }
}
```

> LLM 분석은 응답이 수십 초~수 분 걸릴 수 있으므로 `proxy_read_timeout`을 넉넉히 준다(위 예시 600초). 짧으면 분석 도중 502가 난다.

**docker-compose 구성** (앱 + nginx 2개 컨테이너):

```yaml
# docker-compose.yml
services:
  app:                                  # FastAPI + 스케줄러
    build: .
    env_file: .env
    volumes:
      - ./data:/app/data
      - ./static:/app/static
    expose: ["8000"]                    # 외부 노출 X, nginx만 접근

  nginx:
    image: nginx:stable
    ports: ["80:80"]                    # (도메인+TLS 시 443 추가)
    volumes:
      - ./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
      - ./static:/srv/static:ro
    depends_on: [app]
```

> **도메인 구매 후 HTTPS**: `certbot`(Let's Encrypt)으로 인증서를 발급받아 nginx에 443 리스너와 인증서 경로를 추가하면 된다. 앱 코드(`core/`)는 전혀 손대지 않는다 — TLS는 nginx가 전담.

> **1인용 보안**: 공개 도메인에 올릴 경우, API 키·분석 결과가 노출되지 않도록 nginx 단에서 Basic Auth(`auth_basic`)나 신뢰 IP 화이트리스트를 거는 것을 권장.

---

## 10. 구현 로드맵

**Phase 1 — 단일 파이프라인 MVP**
- 종목 1개에 대해 애널리스트 4명 → 토론 → 계량 → 결론까지 순차 실행
- 데이터는 하드코딩/수동 입력으로 시작, 출력은 콘솔 md
- **환경 독립 코어(§9.1) 구조로 시작** — 나중에 진입점 추가가 쉬워짐

**Phase 2 — 데이터 연결**
- 재무·뉴스·거시 API 실연결, 공식(§4) 코드화

**Phase 3 — 관심종목 관리 + 아침 브리핑 자동화**
- watchlist DB/CLI(§5.4), 시장(KR/US)별 소스 분기
- 관심종목 배치 실행, 스케줄러(KST 아침)

**Phase 4 — 실시간 뉴스/동향 감시**
- 감시 루프(§5.5), 경량 분류, 촉매/저해 알림
- 알림 시 해당 종목 심층분석 자동 트리거

**Phase 5 — 도커 배포 & 웹 계층 & 최적화**
- Dockerfile·docker-compose·볼륨·스케줄러 확립(§9)
- nginx + FastAPI 웹 계층(§9.4) — 브라우저로 브리핑 확인, 도메인 구매 시 TLS
- 계층별 모델 분리, reflection(자기검증) 단계 추가(FinVision), 캐싱
- 폴링 → 웹소켓 실시간 스트림 승급

---

## 11. 참고 논문

- **TradingAgents: Multi-Agents LLM Financial Trading Framework** — arXiv:2412.20138 · 코드 tradingagents-ai.github.io *(본 서비스 뼈대)*
- **FinVision: A Multi-Agent Framework for Stock Market Prediction** — ACM ICAIF'24 *(reflection 모듈)*
- **FinCon: LLM Multi-Agent System with Conceptual Verbal Reinforcement** — NeurIPS'24 *(리스크 통제)*
- **Large Language Models in equity markets** — Front. AI 2025 (84개 연구 리뷰) *(전체 지형)*
- **A Survey of LLMs for Financial Applications** — arXiv:2406.11903 *(멀티에이전트 계보)*

---

## 12. 면책

본 서비스는 정보 제공·분석 보조 목적이며 투자 자문이 아니다. 모든 승률·손익비·기대값은 **가정에 근거한 추정치**이며 최종 투자 판단과 책임은 사용자에게 있다.
