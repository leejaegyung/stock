import json

from anthropic import Anthropic

from app.core.agents.base import BaseAgent
from app.config import settings


class BullResearcher(BaseAgent):
    """
    Layer 4 Agent — 강세론자(Bull).
    애널리스트 4명의 노트를 근거로 매수 논리를 최대한 강하게 구성한다.
    근거 없는 낙관은 금지.
    """

    model = settings.sonnet_model

    def __init__(self, client: Anthropic) -> None:
        super().__init__(client)

    @property
    def system_prompt(self) -> str:
        return (
            "당신은 강세론자(Bull Researcher)입니다.\n\n"
            "역할: 애널리스트들의 분석 데이터를 근거로 해당 종목의 매수 논리를 "
            "최대한 강하게 구성합니다.\n\n"
            "규칙:\n"
            "- 반드시 제공된 데이터에 근거해야 합니다. 근거 없는 낙관 금지.\n"
            "- Bear의 반박이 있으면 데이터 근거로 재반박하세요.\n"
            "- 핵심 매수 논거 3~5개를 명확히 제시하세요.\n"
            "- 자신의 논거 중 가장 취약한 점 1개도 솔직하게 인정하세요.\n\n"
            "출력: 설득력 있는 매수 논리 (자유 형식 텍스트)"
        )

    def run(self, context: dict, bear_rebuttal: str | None = None) -> str:
        parts = [
            f"분석 컨텍스트:\n{json.dumps(context, ensure_ascii=False, default=str)}"
        ]
        if bear_rebuttal:
            parts.append(f"\nBear 반박:\n{bear_rebuttal}\n\n위 반박에 재반박하며 매수 논리를 강화하세요.")
        else:
            parts.append("\n위 데이터를 기반으로 강력한 매수 논리를 제시하세요.")
        return self._call("\n".join(parts))
