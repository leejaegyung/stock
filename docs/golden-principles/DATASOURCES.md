# Golden Principle: Data Sources

## DO

- **market 필드(KR/US)로 datasource를 분기.** pipeline.py의 `_get_datasource(market)`이 이를 담당한다.
- **DataSourceBase 인터페이스를 완전히 구현.** stub 메서드도 반환 타입과 시그니처는 동일해야 한다.
- **stub 메서드는 logger.warning으로 미구현을 알린다.** 조용한 빈 반환 금지.
- **날짜 필터는 datasource 레이어에서.** 오래된 뉴스 기사를 agents에 넘기지 않는다.
- **Phase 2 TODO를 주석으로 명시.** KR datasource의 모든 stub에 `# TODO (Phase 2):` 포함.

## DON'T

- **datasource에서 직접 에이전트 호출 금지.** datasource는 데이터만 반환한다. 판단·해석은 agent 몫.
- **한 datasource에서 KR+US 혼합 금지.** us.py는 yfinance만, kr.py는 pykrx/DART만.
- **하드코딩된 종목 데이터.** 상수 peers 맵(`_SECTOR_PEERS`)은 허용하되, 실 데이터 API로 교체가 목표.

## 시장 분기 패턴

```python
# pipeline.py의 올바른 분기 패턴
def _get_datasource(market: str) -> DataSourceBase:
    if market.upper() == "KR":
        return KRDataSource()
    return USDataSource()  # default: US
```

## Phase 2 연결 우선순위

1. KR financials: DART API (`https://opendart.fss.or.kr`)
2. KR news: Naver News Search API
3. KR peers: KRX 섹터 분류
4. US CAPE: Shiller 데이터셋 직접 연결 (현재 yfinance 프록시)
5. 한국 거시: 한국은행 ECOS API
