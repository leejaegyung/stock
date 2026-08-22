# Golden Principle: Agent Design

## DO

- **각 에이전트는 단일 역할만 가진다.** 시스템 프롬프트로 역할을 고정한다.
- **Layer 1 에이전트 4명(Fundamental/News/Macro/Industry)은 ThreadPoolExecutor로 병렬 실행**한다. 순차 실행은 4배 느리다.
- **LLM은 해석만, 계산은 formulas.py.** QuantRisk가 EV·켈리를 LLM에 맡기면 안 된다.
- **에이전트는 pipeline.py 경유로만 통신**한다. 에이전트끼리 직접 호출 금지.
- **JSON 파싱 실패는 graceful fallback**으로 처리한다. `{"raw": response}` 반환 후 로그.

## DON'T

- **에이전트에 계산 로직 포함 금지.** `ev = win_prob * gain - ...` 같은 코드가 agents/ 에 있으면 `gc_check_formula_in_agents.py`가 잡는다.
- **에이전트 간 직접 통신 금지.** `BullResearcher`가 `QuantRisk`를 직접 호출하면 안 된다.
- **하나의 에이전트에 여러 역할 부여 금지.** "분석하고 판단도 하고 포맷도" → 분리하라.
- **LLM 응답을 신뢰하지 말고 파싱 검증.** `start = raw.find("{")` 패턴을 항상 사용.

## 새 에이전트 추가 시

```
1. app/core/agents/에 파일 생성 (BaseAgent 상속)
2. model = settings.haiku_model | sonnet_model | opus_model 설정
3. system_prompt 프로퍼티 구현
4. run() 메서드 구현 — 반환값은 dict 또는 str
5. pipeline.py에 통합
6. docs/architecture/LAYERS.md 다이어그램 업데이트
```
