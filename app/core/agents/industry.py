import json
import logging

from anthropic import Anthropic

from app.core.agents.base import BaseAgent
from app.config import settings

logger = logging.getLogger(__name__)


class IndustryAnalyst(BaseAgent):
    """
    Layer 4 Agent — 산업/경쟁사 분석 전문가.
    대상 기업의 업계 내 경쟁적 위치를 평가한다.
    """

    model = settings.haiku_model

    def __init__(self, client: Anthropic) -> None:
        super().__init__(client)

    @property
    def system_prompt(self) -> str:
        return (
            "당신은 산업 구조와 경쟁사를 분석하는 전문 애널리스트입니다.\n\n"
            "역할: 대상 기업의 업계 내 경쟁적 위치를 평가하고 경쟁사와 재무를 비교합니다.\n\n"
            "반드시 포함할 항목:\n"
            "- 산업 구조 및 성장성 개요\n"
            "- 경쟁사 2~3곳과 핵심 지표 비교표 (PER·ROE·매출성장·마진)\n"
            "- 대상 기업의 상대적 강점/약점 (해자, 점유율)\n\n"
            "출력 형식: JSON"
        )

    def run(self, ticker: str, peers_data: dict) -> dict:
        prompt = (
            f"종목: {ticker}\n\n"
            f"경쟁사·산업 데이터:\n{json.dumps(peers_data, ensure_ascii=False, default=str)}\n\n"
            "위 데이터를 분석해 산업/경쟁사 분석 결과를 JSON으로 출력하세요."
        )
        raw = self._call(prompt)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            return json.loads(raw[start:end])
        except (json.JSONDecodeError, ValueError):
            logger.warning("IndustryAnalyst JSON parse failed for %s", ticker)
            return {"raw": raw, "ticker": ticker}
