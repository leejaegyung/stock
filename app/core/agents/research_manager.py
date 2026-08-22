import json
import logging

from anthropic import Anthropic

from app.core.agents.base import BaseAgent
from app.config import settings

logger = logging.getLogger(__name__)


class ResearchManager(BaseAgent):
    """
    Layer 4 Agent — 리서치 매니저.
    Bull/Bear 토론을 N라운드 진행시킨 뒤 균형 잡힌 관점으로 종합한다.
    """

    model = settings.sonnet_model

    def __init__(self, client: Anthropic) -> None:
        super().__init__(client)

    @property
    def system_prompt(self) -> str:
        return (
            "당신은 리서치 매니저입니다.\n\n"
            "역할: Bull/Bear 토론 전체를 검토한 뒤 균형 잡힌 관점으로 종합합니다.\n\n"
            "반드시 포함할 항목:\n"
            "- 양측 핵심 논점 요약\n"
            "- 어느 쪽이 더 설득력 있는지 판정 (Bull 우세 / Bear 우세 / 팽팽)\n"
            "- 판정 이유 (데이터 근거)\n"
            "- 투자 시 가장 주목해야 할 변수 1~2개\n\n"
            "출력 형식: JSON {summary, verdict, reasoning, key_variables}"
        )

    def run(self, debate_transcript: list[dict]) -> dict:
        transcript_text = "\n\n".join(
            f"[{item['role']}]\n{item['content']}"
            for item in debate_transcript
        )
        prompt = (
            f"Bull/Bear 토론 전문:\n\n{transcript_text}\n\n"
            "위 토론을 종합해 JSON 형식으로 분석 결과를 출력하세요."
        )
        raw = self._call(prompt)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            return json.loads(raw[start:end])
        except (json.JSONDecodeError, ValueError):
            logger.warning("ResearchManager JSON parse failed")
            return {"summary": raw, "verdict": "판정 불가", "reasoning": "", "key_variables": []}
