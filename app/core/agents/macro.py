import json
import logging

from anthropic import Anthropic

from app.core.agents.base import BaseAgent
from app.config import settings

logger = logging.getLogger(__name__)


class MacroAnalyst(BaseAgent):
    """
    Layer 4 Agent — 거시경제 환경 분석 전문가.
    개별 종목이 아니라 시장 전체의 판을 읽는다.
    결과는 캐싱 가능 (날짜 단위).
    """

    model = settings.haiku_model

    def __init__(self, client: Anthropic) -> None:
        super().__init__(client)

    @property
    def system_prompt(self) -> str:
        return (
            "당신은 거시경제 환경을 분석하는 전문 애널리스트입니다.\n\n"
            "역할: 시장 전체의 판을 읽습니다. 개별 종목이 아니라 환경을 분석합니다.\n\n"
            "반드시 포함할 항목:\n"
            "- 금리·인플레이션·환율 등 핵심 거시 지표 현황\n"
            "- CAPE(Shiller P/E) 분석 → 미국 시장 버블 수준 판정\n"
            "- 한국 vs 미국 개인 구매력 비교 (PPP, 빅맥지수 등 언급)\n"
            "- 한국 vs 미국 물가지수(CPI) 비교\n"
            "- 거시 트렌드 코멘트 (AI, 전기화, 소비심리 등)\n\n"
            "출력 형식: JSON"
        )

    def run(self, date: str, macro_data: dict) -> dict:
        prompt = (
            f"분석 날짜: {date}\n\n"
            f"거시경제 데이터:\n{json.dumps(macro_data, ensure_ascii=False, default=str)}\n\n"
            "위 데이터를 분석해 거시경제 환경 분석 결과를 JSON으로 출력하세요."
        )
        raw = self._call(prompt)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            return json.loads(raw[start:end])
        except (json.JSONDecodeError, ValueError):
            logger.warning("MacroAnalyst JSON parse failed for date %s", date)
            return {"raw": raw, "date": date}
