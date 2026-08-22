import json
import logging

from anthropic import Anthropic

from app.core.agents.base import BaseAgent
from app.config import settings

logger = logging.getLogger(__name__)


class FundamentalAnalyst(BaseAgent):
    """
    Layer 4 Agent — 재무 데이터 수집 전문가.
    사실과 지표만 정리하며 투자 판단은 하지 않는다.
    """

    model = settings.haiku_model

    def __init__(self, client: Anthropic) -> None:
        super().__init__(client)

    @property
    def system_prompt(self) -> str:
        return (
            "당신은 기업 재무 건전성과 밸류에이션 원천 데이터를 분석하는 전문 애널리스트입니다.\n"
            "역할: 사실과 지표만 정리하고 투자 판단은 절대 하지 않습니다.\n\n"
            "반드시 포함할 항목:\n"
            "- 최근 실적 발표 요약 (매출/영업이익/EPS, 전년·전분기 대비)\n"
            "- PER, ROE (동종 업계·과거 5년 밴드와 비교)\n"
            "- 현금흐름 3분류: 영업활동 / 투자활동 / 재무활동 (숫자 명시)\n"
            "- 부채비율, 배당수익률, 예상 이익성장률\n\n"
            "출력 형식: JSON (숫자 위주, 서술 최소). 없는 데이터는 null로 표시.\n"
            "판단이나 추천은 포함하지 마세요."
        )

    @staticmethod
    def _safe_dumps(obj) -> str:
        """Recursively stringify non-serializable keys before JSON encoding."""
        if isinstance(obj, dict):
            return json.dumps(
                {str(k): FundamentalAnalyst._sanitize(v) for k, v in obj.items()},
                ensure_ascii=False, default=str,
            )
        return json.dumps(obj, ensure_ascii=False, default=str)

    @staticmethod
    def _sanitize(obj):
        if isinstance(obj, dict):
            return {str(k): FundamentalAnalyst._sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [FundamentalAnalyst._sanitize(i) for i in obj]
        return obj

    def run(self, ticker: str, data: dict) -> dict:
        prompt = (
            f"종목: {ticker}\n\n"
            f"원시 재무 데이터:\n{self._safe_dumps(data)}\n\n"
            "위 데이터를 분석해 구조화된 재무 분석 노트를 JSON으로 출력하세요.\n"
            "응답은 반드시 유효한 JSON 객체여야 합니다."
        )
        raw = self._call(prompt)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            return json.loads(raw[start:end])
        except (json.JSONDecodeError, ValueError):
            logger.warning("FundamentalAnalyst JSON parse failed for %s, returning raw", ticker)
            return {"raw": raw, "ticker": ticker}
