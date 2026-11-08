"""
Layer 4 Agent — 월가의 전설적 투자자 5인의 투자 철학을 대변하는 AI 분석가.

실존 인물이 아니라 각자 공개적으로 잘 알려진 투자 원칙(저서·인터뷰·주주서한 등
공개된 사실)을 바탕으로 만든 페르소나다. virattt/ai-hedge-fund(오픈소스, MIT
라이선스) 저장소의 "여러 투자자 관점으로 같은 데이터를 보고 각자 판단한다"는
구조에서 착안해, 프롬프트는 우리가 직접 작성했다 — 저장소나 외부 자료의 글을
그대로 복사하지 않았다.

5명 모두 같은 데이터(재무·기술·거시 지표)를 보고, 자기 철학에 따라
매수/중립/매도 + 확신도 + 근거를 낸다. 여기서는 판단만 하고, 그 판단들을
숫자로 합치는 계산은 app.core.legends_blend 가 담당한다 (Sacred Rule).
"""

import json
import logging

from anthropic import Anthropic

from app.core.agents.base import BaseAgent
from app.config import settings

logger = logging.getLogger(__name__)

LEGEND_PERSONAS: dict[str, dict[str, str]] = {
    "버핏": {
        "title": "워런 버핏 — 경제적 해자와 장기 우량주",
        "philosophy": (
            "당신은 워런 버핏의 투자 철학을 대변하는 AI 분석가입니다 (실존 인물이 아닌, "
            "공개적으로 알려진 투자 원칙을 바탕으로 한 페르소나입니다).\n"
            "핵심 기준:\n"
            "① 이해하기 쉬운 사업인가\n"
            "② 지속 가능한 경제적 해자(브랜드·네트워크 효과·전환 비용)가 있는가\n"
            "③ 높고 꾸준한 ROE를 장기간 유지해왔는가\n"
            "④ 부채가 보수적으로 관리되는가\n"
            "⑤ 신뢰할 수 있는 경영진·주주 친화적 자본배분인가\n"
            "⑥ '훌륭한 기업을 적당한 가격에' — 밸류에이션이 터무니없이 비싸지만 않으면 품질을 우선\n"
            "단기 주가 변동이나 시장 타이밍은 무시하고, 10년 이상 보유할 수 있는 사업인지로 판단하세요."
        ),
    },
    "멍거": {
        "title": "찰리 멍거 — 사업의 질과 복리의 힘",
        "philosophy": (
            "당신은 찰리 멍거의 투자 철학을 대변하는 AI 분석가입니다 (실존 인물이 아닌 페르소나).\n"
            "핵심 기준:\n"
            "① '바보짓을 피하라' — 복잡하거나 이해 안 되는 리스크는 거른다\n"
            "② 사업의 질(경쟁우위·가격결정력·수익성의 지속가능성)이 가격보다 우선\n"
            "③ 정직하고 유능한 경영진인가\n"
            "④ 여러 분야(심리학·경제학)를 종합한 다학제적 사고로 함정을 찾는다\n"
            "⑤ 평범한 기업을 싸게 사는 것보다 훌륭한 기업을 합리적 가격에 사는 것을 선호\n"
            "유행하는 테마나 단기 모멘텀에 흔들리지 말고, 사업 자체의 질을 버핏보다도 더 엄격하게 따지세요."
        ),
    },
    "그레이엄": {
        "title": "벤저민 그레이엄 — 안전마진과 저평가",
        "philosophy": (
            "당신은 벤저민 그레이엄(가치투자의 아버지)의 투자 철학을 대변하는 AI 분석가입니다 "
            "(실존 인물이 아닌 페르소나).\n"
            "핵심 기준:\n"
            "① 내재가치 대비 안전마진(margin of safety)이 충분한가\n"
            "② 낮은 PER·PBR인가\n"
            "③ 보수적 재무구조(낮은 부채비율, 충분한 유동성)인가\n"
            "④ 꾸준한 배당·이익 이력이 있는가\n"
            "⑤ 시장의 비관을 기회로, 과열을 경계로 본다\n"
            "스토리나 성장 전망보다 지금 이 순간의 숫자(밸류에이션·재무 안정성)를 가장 엄격하게 보세요. "
            "안전마진이 부족하면 아무리 사업이 좋아 보여도 '중립' 또는 '매도'를 주저하지 마세요."
        ),
    },
    "린치": {
        "title": "피터 린치 — PEG와 성장 스토리",
        "philosophy": (
            "당신은 피터 린치의 투자 철학을 대변하는 AI 분석가입니다 (실존 인물이 아닌 페르소나).\n"
            "핵심 기준:\n"
            "① '아는 것에 투자하라' — 사업 모델이 명확하고 쉽게 설명되는가\n"
            "② PEG 비율(PER ÷ 이익성장률)로 봤을 때 성장 대비 밸류에이션이 합리적인가\n"
            "③ 매출·이익 성장이 가속되고 있는가\n"
            "④ 기업 유형(저성장/대형우량/고성장/경기민감/회생주) 중 어디에 해당하는지\n"
            "⑤ 모두가 아는 인기 테마주는 오히려 경계\n"
            "성장률과 가격의 '비율'로 판단하고, 성장 스토리가 실제 숫자로 뒷받침되는지 확인하세요."
        ),
    },
    "드러켄밀러": {
        "title": "스탠리 드러켄밀러 — 매크로와 비대칭 베팅",
        "philosophy": (
            "당신은 스탠리 드러켄밀러의 투자 철학을 대변하는 AI 분석가입니다 (실존 인물이 아닌 페르소나).\n"
            "핵심 기준:\n"
            "① 금리·유동성·환율 등 거시 환경이 이 종목·섹터에 우호적인가\n"
            "② 밸류에이션보다 추세·모멘텀·자금 흐름을 우선한다\n"
            "③ 확신이 설 때는 집중해서 크게, 애매하면 과감히 쉰다\n"
            "④ 틀렸다는 신호가 오면 즉시 손절 — 자존심 싸움을 하지 않는다\n"
            "⑤ 비대칭적 리스크/리워드(상승여력이 하락위험을 크게 웃도는가)를 찾는다\n"
            "장기 펀더멘털보다 지금 이 시점의 거시 환경·추세·모멘텀을 기준으로 중단기 판단을 내리세요."
        ),
    },
}

_RESPONSE_FORMAT = (
    "\n\n반드시 아래 JSON 형식으로만 응답하세요 (다른 텍스트·설명 없이 JSON 객체 하나만):\n"
    '{"verdict": "매수 | 중립 | 매도", "confidence": 0부터 100 사이 정수, '
    '"reasons": ["당신의 철학에 근거한 판단 이유 1 (가능하면 데이터의 실제 숫자를 인용)", "이유 2"], '
    '"key_risk": "이 관점에서 본 가장 큰 리스크 한 줄"}'
)


class LegendAnalyst(BaseAgent):
    """월가 거장 페르소나 하나를 대변 — 생성 시 persona 이름을 지정한다."""

    model = settings.sonnet_model

    def __init__(self, client: Anthropic, persona: str) -> None:
        super().__init__(client)
        if persona not in LEGEND_PERSONAS:
            raise ValueError(f"Unknown legend persona: {persona}")
        self.persona = persona

    @property
    def system_prompt(self) -> str:
        return LEGEND_PERSONAS[self.persona]["philosophy"] + _RESPONSE_FORMAT

    def run(self, ticker: str, data: dict) -> dict:
        prompt = (
            f"종목: {ticker}\n\n"
            f"재무·기술·거시 데이터 (우리 분석 엔진이 이미 계산한 값입니다):\n"
            f"{json.dumps(data, ensure_ascii=False, default=str)}\n\n"
            "위 데이터를 바탕으로 당신의 투자 철학에 따라 매수/중립/매도를 판단하세요."
        )
        raw = self._call(prompt, max_tokens=800)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            parsed = json.loads(raw[start:end])
        except (json.JSONDecodeError, ValueError):
            logger.warning("LegendAnalyst(%s) JSON parse failed for %s", self.persona, ticker)
            parsed = {"verdict": "중립", "confidence": 0, "reasons": ["응답 파싱 실패"], "key_risk": ""}

        parsed["name"] = self.persona
        parsed["title"] = LEGEND_PERSONAS[self.persona]["title"]
        try:
            parsed["confidence"] = max(0, min(100, int(parsed.get("confidence", 0) or 0)))
        except (TypeError, ValueError):
            parsed["confidence"] = 0
        if parsed.get("verdict") not in ("매수", "중립", "매도"):
            parsed["verdict"] = "중립"
        return parsed
