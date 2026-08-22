import json
import logging

from anthropic import Anthropic

from app.core.agents.base import BaseAgent
from app.config import settings

logger = logging.getLogger(__name__)


class NewsSentimentAnalyst(BaseAgent):
    """
    Layer 4 Agent — 뉴스·센티먼트 수집·분류 전문가.
    촉매/중립/저해 태깅 및 근거를 산출한다.
    """

    model = settings.haiku_model

    def __init__(self, client: Anthropic) -> None:
        super().__init__(client)

    @property
    def system_prompt(self) -> str:
        return (
            "당신은 주식시장 뉴스와 센티먼트를 분석하는 전문 애널리스트입니다.\n\n"
            "역할: 당일 기준 시장 이슈와 종목별 최근 뉴스를 수집·요약하고 "
            "촉매(catalyst)/저해(headwind) 요인으로 분류합니다.\n\n"
            "반드시 포함할 항목:\n"
            "- 당일 증권시장 전반 이슈 (지수·섹터 동향)\n"
            "- 종목별 최근 뉴스 헤드라인 + 3줄 요약\n"
            "- 각 뉴스의 촉매/중립/저해 태깅 및 근거\n"
            "- 정부 정책·규제 이슈 (해당 종목/섹터)\n\n"
            "주의: 오래된 기사 배제(날짜 필터 필수), 출처 명시.\n"
            "출력 형식: JSON"
        )

    def run(self, ticker: str, date: str, news_items: list[dict]) -> dict:
        prompt = (
            f"종목: {ticker}\n"
            f"분석 날짜: {date}\n\n"
            f"뉴스 데이터:\n{json.dumps(news_items, ensure_ascii=False, default=str)}\n\n"
            "위 뉴스를 분석해 센티먼트 분석 결과를 JSON으로 출력하세요.\n"
            "각 뉴스 항목에 impact 필드(촉매/중립/저해)와 reason 필드를 포함하세요."
        )
        raw = self._call(prompt)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            return json.loads(raw[start:end])
        except (json.JSONDecodeError, ValueError):
            logger.warning("NewsSentimentAnalyst JSON parse failed for %s", ticker)
            return {"raw": raw, "ticker": ticker, "date": date}
