# Golden Principle: Testing

## DO

- **formulas.py는 100% 단위 테스트.** 순수 함수라 mocking 불필요. API key 없이 항상 실행.
- **test_architecture.py는 CI에서 항상 실행.** API key 없어도 돌아간다. 레이어 위반의 유일한 기계적 방어선.
- **에이전트 테스트는 `@pytest.mark.live`로 표시.** `pytest -m "not live"`로 제외 가능.
- **conftest.py에 공통 fixture 집중.** in_memory_db, sample_financial_data 등.

## DON'T

- **LLM 응답 mocking 금지.** `mock.patch("anthropic.Anthropic")` 패턴은 거짓 안심을 준다. 실제 API가 바뀌면 테스트가 통과해도 서비스가 깨진다.
- **test_architecture.py의 KNOWN_VIOLATIONS 임의 추가 금지.** 위반을 숨기는 것이 아니라 위반을 없애야 한다.
- **외부 API 의존 테스트를 기본 실행에 포함 금지.** CI는 빨라야 한다.

## 실행 명령

```bash
# API key 없이 항상 실행 가능
pytest tests/test_formulas.py -v
pytest tests/test_architecture.py -v

# API key 있을 때만
pytest tests/ -m live -v

# 전체 (live 제외)
pytest tests/ -m "not live" -v
```
