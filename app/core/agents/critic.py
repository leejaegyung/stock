"""
Critic (Reflection) agent — inspired by FinVision (ACM ICAIF'24).

FinVision showed that a self-verification module significantly improves accuracy
by catching logical inconsistencies between the qualitative narrative and the
quantitative output before the final verdict is issued.
"""

import json
import logging

from anthropic import Anthropic

from app.config import settings
from app.core.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class Critic(BaseAgent):
    """
    Layer 4 Agent — Reflection / self-verification step.
    Reviews ChiefAdvisor output for internal consistency before it is accepted.

    Checks:
    1. Does the verdict align with the quant numbers (EV sign, Kelly > 0)?
    2. Are key_reasons supported by the analyst notes?
    3. Does the confidence level match the Bull/Bear debate verdict?
    4. Are there contradictions between macro context and the stock-level call?

    If inconsistencies are found, returns a revised verdict and the correction notes.
    If everything is consistent, passes through unchanged with an empty corrections list.
    """

    model = settings.sonnet_model

    def __init__(self, client: Anthropic) -> None:
        super().__init__(client)

    @property
    def system_prompt(self) -> str:
        return (
            "당신은 투자 분석 검증 전문가(Critic)입니다. FinVision 논문의 reflection 모듈 역할입니다.\n\n"
            "역할: Chief Advisor의 최종 의견을 검토해 논리적 일관성을 확인합니다.\n\n"
            "검증 항목:\n"
            "1. verdict와 계량 수치 일치 여부\n"
            "   - EV > 0인데 매도/회피? EV < 0인데 매수?\n"
            "   - half_kelly <= 0인데 매수 결론?\n"
            "2. key_reasons가 애널리스트 노트의 실제 데이터에 근거하는가?\n"
            "3. 확신도(confidence)가 Bull/Bear 토론 결과와 일치하는가?\n"
            "4. 거시 환경(버블 수준 CAPE, 금리 등)과 종목 매수 결론이 상충하지 않는가?\n\n"
            "출력 형식: JSON\n"
            "{\n"
            "  'passed': true/false,\n"
            "  'corrections': ['문제 설명 및 수정 제안', ...],  // 통과 시 빈 리스트\n"
            "  'revised_verdict': '매수'|'매도'|'보유'|'추가매수'|null,  // 변경 없으면 null\n"
            "  'revised_confidence': '상'|'중'|'하'|null,\n"
            "  'reflection_note': '한 줄 총평'\n"
            "}\n\n"
            "중요: 사소한 문체 차이는 무시하고 실질적 논리 오류만 지적하세요."
        )

    def run(self, full_context: dict, chief_output: dict) -> dict:
        prompt = (
            f"분석 컨텍스트 (애널리스트 노트·토론·계량 결과):\n"
            f"{json.dumps(full_context, ensure_ascii=False, default=str)}\n\n"
            f"Chief Advisor 최종 의견:\n"
            f"{json.dumps(chief_output, ensure_ascii=False, default=str)}\n\n"
            "위 의견의 논리적 일관성을 검증해 JSON으로 출력하세요."
        )
        raw = self._call(prompt, max_tokens=2048)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            result = json.loads(raw[start:end])
        except (json.JSONDecodeError, ValueError):
            logger.warning("Critic JSON parse failed; treating as passed")
            return {
                "passed": True,
                "corrections": [],
                "revised_verdict": None,
                "revised_confidence": None,
                "reflection_note": "검증 파싱 실패 — 원본 유지",
            }

        # Apply revisions back to chief_output if critic found issues
        if not result.get("passed") and result.get("revised_verdict"):
            chief_output["verdict"] = result["revised_verdict"]
            logger.info(
                "Critic revised verdict: %s → %s",
                chief_output.get("verdict"),
                result["revised_verdict"],
            )
        if not result.get("passed") and result.get("revised_confidence"):
            chief_output["confidence"] = result["revised_confidence"]

        return result
