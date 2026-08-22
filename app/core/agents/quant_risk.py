import json
import logging

from anthropic import Anthropic

from app.core.agents.base import BaseAgent
from app.config import settings
from app.core.formulas import (
    cashflow_pattern,
    cape_judgment,
    expected_value,
    half_kelly,
    per_roe_judgment,
)

logger = logging.getLogger(__name__)


class QuantRisk(BaseAgent):
    """
    Layer 4 Agent — 계량/리스크 팀.
    정성 판단을 숫자로 검증한다.
    LLM은 승률·손익비 추정과 해석만 담당.
    실제 EV·켈리 계산은 formulas.py가 전담.
    """

    model = settings.sonnet_model

    def __init__(self, client: Anthropic) -> None:
        super().__init__(client)

    @property
    def system_prompt(self) -> str:
        return (
            "당신은 계량·리스크 분석가입니다.\n\n"
            "역할: 앞선 정성 판단을 숫자로 검증합니다. 여기서 결론이 뒤집힐 수 있습니다.\n\n"
            "당신이 해야 할 일:\n"
            "1. 리서치 결론과 재무 데이터를 바탕으로 승률(win_prob, 0~1)과 "
            "손익비(odds_ratio = 예상이익/예상손실)를 추정하세요.\n"
            "2. 추정 근거(assumptions)를 명확히 서술하세요. 가정임을 명시하세요.\n"
            "3. 밸류에이션 종합 판정을 내리세요 (저평가/적정/고평가/버블).\n"
            "4. 손절가(stop_loss)와 목표가(target_price) 시나리오를 제시하세요.\n\n"
            "출력 형식: JSON {win_prob, odds_ratio, valuation, target_price, stop_loss, assumptions}\n"
            "주의: EV와 켈리 계산은 당신이 하지 않습니다. 시스템이 자동으로 계산합니다."
        )

    def run(self, research_output: dict, fundamental_data: dict) -> dict:
        prompt = (
            f"리서치 결론:\n{json.dumps(research_output, ensure_ascii=False, default=str)}\n\n"
            f"재무 데이터:\n{json.dumps(fundamental_data, ensure_ascii=False, default=str)}\n\n"
            "위 내용을 바탕으로 계량 분석 결과를 JSON으로 출력하세요.\n"
            "반드시 win_prob(0~1 float)과 odds_ratio(양수 float) 필드를 포함하세요."
        )
        raw = self._call(prompt)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            llm_output = json.loads(raw[start:end])
        except (json.JSONDecodeError, ValueError):
            logger.warning("QuantRisk JSON parse failed")
            llm_output = {"win_prob": 0.5, "odds_ratio": 1.0, "assumptions": raw}

        win_prob: float = float(llm_output.get("win_prob", 0.5))
        odds_ratio: float = float(llm_output.get("odds_ratio", 1.0))

        # Clamp to valid ranges
        win_prob = max(0.0, min(1.0, win_prob))
        odds_ratio = max(0.01, odds_ratio)

        # formulas.py handles all calculations — LLM provides only estimates
        gain = odds_ratio * 100  # normalised to percentage points
        loss = 100.0
        ev = expected_value(win_prob, gain, loss)
        hk = half_kelly(win_prob, odds_ratio)

        # Optional: enrich valuation with formula-based judgments when data available
        per = fundamental_data.get("per") or fundamental_data.get("trailingPE")
        roe = fundamental_data.get("roe") or fundamental_data.get("returnOnEquity")
        cape = fundamental_data.get("cape")

        per_roe_label = None
        if per and roe:
            try:
                sector_per = fundamental_data.get("sector_avg_per", per)
                sector_roe = fundamental_data.get("sector_avg_roe", roe)
                per_roe_label = per_roe_judgment(
                    float(per), float(roe), float(sector_per), float(sector_roe)
                )
            except (TypeError, ValueError):
                pass

        cape_label = None
        if cape:
            try:
                cape_label = cape_judgment(float(cape))
            except (TypeError, ValueError):
                pass

        cf = fundamental_data.get("cashflow", {})
        cf_label = None
        if cf:
            try:
                cf_label = cashflow_pattern(
                    float(cf.get("operating", 0)),
                    float(cf.get("investing", 0)),
                    float(cf.get("financing", 0)),
                )
            except (TypeError, ValueError):
                pass

        return {
            "ev": round(ev, 4),
            "kelly": round(half_kelly(win_prob, odds_ratio) * 2, 4),
            "half_kelly": round(hk, 4),
            "win_prob": win_prob,
            "odds_ratio": odds_ratio,
            "valuation": llm_output.get("valuation", ""),
            "per_roe_judgment": per_roe_label,
            "cape_judgment": cape_label,
            "cashflow_pattern": cf_label,
            "target_price": llm_output.get("target_price", ""),
            "stop_loss": llm_output.get("stop_loss", ""),
            "assumptions": llm_output.get("assumptions", ""),
        }
