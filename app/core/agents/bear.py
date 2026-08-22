import json

from anthropic import Anthropic

from app.core.agents.base import BaseAgent
from app.config import settings


class BearResearcher(BaseAgent):
    """
    Layer 4 Agent — 약세론자(Bear).
    동일 데이터로 매도/회피 논리를 최대한 강하게 구성한다.
    Bull의 주장에 반박한다.
    """

    model = settings.sonnet_model

    def __init__(self, client: Anthropic) -> None:
        super().__init__(client)

    @property
    def system_prompt(self) -> str:
        return (
            "당신은 약세론자(Bear Researcher)입니다.\n\n"
            "역할: 분석 데이터를 바탕으로 해당 종목의 매도·회피 논리를 "
            "최대한 강하게 구성합니다.\n\n"
            "규칙:\n"
            "- 리스크·고평가·저해 요인을 파고드세요.\n"
            "- Bull의 주장이 있으면 데이터 근거로 반박하세요.\n"
            "- 핵심 리스크 요인 3~5개를 명확히 제시하세요.\n"
            "- 자신의 논거 중 가장 취약한 점 1개도 솔직하게 인정하세요.\n\n"
            "출력: 설득력 있는 매도/회피 논리 (자유 형식 텍스트)"
        )

    def run(self, context: dict, bull_argument: str | None = None) -> str:
        parts = [
            f"분석 컨텍스트:\n{json.dumps(context, ensure_ascii=False, default=str)}"
        ]
        if bull_argument:
            parts.append(f"\nBull 주장:\n{bull_argument}\n\n위 주장을 반박하며 리스크를 강조하세요.")
        else:
            parts.append("\n위 데이터를 기반으로 강력한 매도/회피 논리를 제시하세요.")
        return self._call("\n".join(parts))
