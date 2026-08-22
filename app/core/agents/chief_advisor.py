import json
import logging

from anthropic import Anthropic

from app.core.agents.base import BaseAgent
from app.config import settings

logger = logging.getLogger(__name__)


class ChiefAdvisor(BaseAgent):
    """
    Layer 4 Agent — 최고 투자자문 (Chief Advisor).
    전체를 종합해 매수/매도/보유/추가매수 결론을 내리고 아침 브리핑 섹션을 생성한다.
    """

    model = settings.opus_model

    def __init__(self, client: Anthropic) -> None:
        super().__init__(client)

    @property
    def system_prompt(self) -> str:
        return (
            "당신은 최고 투자자문(Chief Advisor)입니다.\n\n"
            "역할: 펀더멘털·뉴스·거시·산업 분석, Bull/Bear 토론, 계량 검증 결과를 "
            "종합해 최종 투자 의견을 냅니다.\n\n"
            "반드시 포함할 항목:\n"
            "- verdict: '매수' / '매도' / '보유' / '추가매수' 중 하나\n"
            "- confidence: '상' / '중' / '하' 중 하나\n"
            "- key_reasons: 핵심 근거 3개 (리스트)\n"
            "- warnings: 유의사항 (리스트, 최소 1개)\n"
            "- brief_section: 아침 브리핑용 마크다운 섹션 (§6 포맷 참조)\n\n"
            "출력 형식: JSON {verdict, confidence, key_reasons, warnings, brief_section}\n\n"
            "면책: 이 분석은 정보 제공 목적이며 투자 자문이 아닙니다. "
            "모든 수치는 가정에 근거한 추정치임을 brief_section에 명시하세요."
        )

    def run(self, full_context: dict) -> dict:
        prompt = (
            f"전체 분석 결과:\n{json.dumps(full_context, ensure_ascii=False, default=str)}\n\n"
            "위 내용을 종합해 최종 투자 의견을 JSON으로 출력하세요."
        )
        raw = self._call(prompt, max_tokens=8192)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            return json.loads(raw[start:end])
        except (json.JSONDecodeError, ValueError):
            logger.warning("ChiefAdvisor JSON parse failed")
            return {
                "verdict": "보류",
                "confidence": "하",
                "key_reasons": [],
                "warnings": ["분석 결과 파싱 실패"],
                "brief_section": raw,
            }
