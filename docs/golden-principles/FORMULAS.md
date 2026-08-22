# Golden Principle: Formulas

## DO

- **순수 함수만.** 사이드이펙트, 로깅, API 호출 없음.
- **모든 가정은 호출자가 명시.** `half_kelly(win_prob, odds_ratio)` — win_prob는 호출자(QuantRisk)가 LLM 추정값으로 채운다. formulas.py는 가정을 모른다.
- **표준 라이브러리만 사용.** numpy도 금지 (Layer 1은 의존성 없음).
- **경계값을 테스트.** `test_formulas.py`에 경계값 케이스를 항상 포함한다.

## DON'T

- **LLM 호출 절대 금지.** `formulas.py`에 `Anthropic` import가 있으면 레이어 위반.
- **전역 상태 사용 금지.** `_cache = {}` 같은 모듈 레벨 상태 없음.
- **계산 결과를 에이전트 내부에 직접 구현 금지.** `ev = win_prob * gain - ...`가 agents/에 있으면 GC 스크립트가 감지한다.

## 공식 목록

| 함수 | 설명 | 주의사항 |
|------|------|---------|
| `expected_value(win_prob, gain, loss)` | EV > 0이면 통계적으로 유리 | gain/loss 단위 통일 |
| `kelly_fraction(win_prob, odds_ratio)` | 전체 켈리. F ≤ 0 = 진입 금지 | 실무 사용 금지 — half_kelly 사용 |
| `half_kelly(win_prob, odds_ratio)` | 하프켈리. 실무 권장 비중 | 0~50% 범위 확인 |
| `cashflow_pattern(op, inv, fin)` | 현금흐름 3분류 패턴 판정 | 부호 조합 기반 |
| `cape_judgment(cape)` | 시장 버블 수준 | 개별 종목 아닌 시장 전체 |
| `per_roe_judgment(per, roe, ...)` | 상대 밸류에이션 | 반드시 섹터 평균과 비교 |
| `peg_ratio(per, growth_pct)` | PEG = PER/성장률 | 성장률 0 이하 → inf |
