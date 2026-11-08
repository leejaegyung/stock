"""
월가의 전설적 투자자 5인의 투자 철학을 대변하는 규칙 기반(rule-based) 분석기.

실존 인물이 아니라 각자 공개적으로 잘 알려진 투자 원칙(저서·인터뷰·주주서한 등
공개된 사실)을 바탕으로 만든 페르소나다. virattt/ai-hedge-fund(오픈소스, MIT
라이선스) 저장소의 "여러 투자자 관점으로 같은 데이터를 보고 각자 판단한다"는
구조에서 착안했다.

★ 비용 원칙: 우리 시스템은 돈이 들면 안 된다 — 처음엔 LLM(Sonnet → Haiku)으로
구현했지만, Anthropic API는 선불 크레딧이 바닥나면 (모델과 무관하게) 모든 호출이
막힌다는 걸 실제로 확인했다. 그래서 LLM 호출을 완전히 없애고, 각 거장의 투자
원칙을 코드로 옮긴 "규칙 기반 점수표"로 다시 짰다. 데이터는 이미
app.core.algo_pipeline이 계산해둔 재무·기술 지표를 그대로 쓰고, 판단 로직만
사람이 설계한 조건문으로 대체했다 — API 과금이 전혀 없고, 실행도 즉시(동기) 끝난다.
Sacred Rule(판단은 해석, 계산은 숫자)은 그대로 유지된다 — 여기서는 "해석"조차
결정론적 규칙이라 재현 가능하고 비용이 0이다.
"""

from __future__ import annotations

LEGEND_PERSONAS: dict[str, dict[str, str]] = {
    "버핏": {
        "title": "워런 버핏 — 경제적 해자와 장기 우량주",
        "philosophy": (
            "이해하기 쉬운 사업, 지속 가능한 경제적 해자, 높고 꾸준한 ROE, "
            "보수적인 부채, '훌륭한 기업을 적당한 가격에'."
        ),
    },
    "멍거": {
        "title": "찰리 멍거 — 사업의 질과 복리의 힘",
        "philosophy": (
            "바보짓을 피하라, 사업의 질이 가격보다 우선, 평범한 기업을 싸게 사기보다 "
            "훌륭한 기업을 합리적 가격에."
        ),
    },
    "그레이엄": {
        "title": "벤저민 그레이엄 — 안전마진과 저평가",
        "philosophy": (
            "내재가치 대비 안전마진, 낮은 PER, 보수적 재무구조, 꾸준한 배당. "
            "안전마진이 부족하면 사업이 좋아 보여도 주저하지 않고 중립·매도."
        ),
    },
    "린치": {
        "title": "피터 린치 — PEG와 성장 스토리",
        "philosophy": (
            "아는 것에 투자하라, PEG 비율로 본 성장 대비 밸류에이션, 가속되는 성장, "
            "인기 테마주는 오히려 경계."
        ),
    },
    "드러켄밀러": {
        "title": "스탠리 드러켄밀러 — 매크로와 비대칭 베팅",
        "philosophy": (
            "거시 환경·추세·모멘텀을 밸류에이션보다 우선, 확신이 설 때 집중해서, "
            "틀렸다는 신호가 오면 즉시 손절."
        ),
    },
}


def _n(v) -> float | None:
    """data 딕셔너리 값이 숫자가 아니면(None 등) None으로 통일."""
    return v if isinstance(v, (int, float)) else None


def _verdict(tilt: float) -> str:
    if tilt >= 2:
        return "매수"
    if tilt <= -2:
        return "매도"
    return "중립"


def _confidence(tilt: float, signal_count: int) -> int:
    """신호 개수가 적으면(데이터 부족) 확신도를 낮춘다."""
    base = 40 + min(abs(tilt), 6) * 9
    if signal_count < 2:
        base -= 15
    return max(20, min(95, round(base)))


def _buffett(data: dict) -> dict:
    roe, margin = _n(data.get("roe_pct")), _n(data.get("profit_margin_pct"))
    dte, per = _n(data.get("debt_to_equity_pct")), _n(data.get("per"))
    tilt, reasons, signals = 0.0, [], 0

    if roe is not None:
        signals += 1
        if roe >= 20:
            tilt += 2; reasons.append(f"ROE {roe}%로 높고 꾸준한 자본효율 — 경제적 해자의 신호")
        elif roe >= 12:
            tilt += 1; reasons.append(f"ROE {roe}%는 양호한 수준")
        else:
            tilt -= 1; reasons.append(f"ROE {roe}%는 다소 낮아 자본효율이 아쉬움")
    if margin is not None:
        signals += 1
        if margin >= 20:
            tilt += 1; reasons.append(f"순이익률 {margin}%로 높은 가격 결정력을 시사")
        elif margin < 8:
            tilt -= 1; reasons.append(f"순이익률 {margin}%로 마진이 얇은 편")
    if dte is not None:
        signals += 1
        if dte < 50:
            tilt += 1; reasons.append(f"부채비율 {dte}%로 보수적인 재무구조")
        elif dte > 150:
            tilt -= 2; reasons.append(f"부채비율 {dte}%로 부채 부담이 커 보수적 기준에 못 미침")
        elif dte > 100:
            tilt -= 1; reasons.append(f"부채비율 {dte}%로 다소 높은 편")
    if per is not None:
        signals += 1
        if per > 35:
            tilt -= 1; reasons.append(f"PER {per}배는 '적당한 가격' 기준에서 다소 비쌈")
        elif per < 15:
            tilt += 1; reasons.append(f"PER {per}배로 밸류에이션 부담이 적음")

    key_risk = "부채가 늘어나거나 해자가 훼손되면 장기 보유 전제가 흔들림" if (dte or 0) > 100 \
        else "경제적 해자가 생각보다 약하거나 경쟁에 잠식될 가능성"
    return {"verdict": _verdict(tilt), "confidence": _confidence(tilt, signals), "reasons": reasons or ["데이터 부족"], "key_risk": key_risk}


def _munger(data: dict) -> dict:
    roe, margin = _n(data.get("roe_pct")), _n(data.get("profit_margin_pct"))
    dte, peg = _n(data.get("debt_to_equity_pct")), _n(data.get("peg_ratio"))
    tilt, reasons, signals = 0.0, [], 0

    if roe is not None:
        signals += 1
        if roe >= 20:
            tilt += 2; reasons.append(f"ROE {roe}%는 훌륭한 사업의 증거")
        elif roe >= 12:
            tilt += 1
        else:
            tilt -= 1; reasons.append(f"ROE {roe}%로 사업의 질이 눈에 띄지 않음")
    if margin is not None:
        signals += 1
        if margin >= 25:
            tilt += 2; reasons.append(f"순이익률 {margin}%로 가격결정력이 뚜렷함")
        elif margin >= 15:
            tilt += 1
    if dte is not None:
        signals += 1
        if dte > 150:
            tilt -= 2; reasons.append(f"부채비율 {dte}% — '바보짓을 피하라' 원칙에서 경계 대상")
        elif dte > 100:
            tilt -= 1
        elif dte < 50:
            tilt += 1
    if peg is not None:
        signals += 1
        if peg < 1.5:
            tilt += 1; reasons.append(f"PEG {peg}로 성장 대비 가격이 합리적")
        elif peg > 3:
            tilt -= 1; reasons.append(f"PEG {peg}로 성장 대비 비싼 가격 — 훌륭한 기업도 너무 비싸면 곤란")

    key_risk = "복잡성이 숨어있거나(과도한 부채·불투명한 회계) 경영진 신뢰가 흔들릴 경우"
    return {"verdict": _verdict(tilt), "confidence": _confidence(tilt, signals), "reasons": reasons or ["데이터 부족"], "key_risk": key_risk}


def _graham(data: dict) -> dict:
    per, dte = _n(data.get("per")), _n(data.get("debt_to_equity_pct"))
    div_yield = _n(data.get("dividend_yield_pct"))
    price, hi, lo = _n(data.get("price")), _n(data.get("week52_high")), _n(data.get("week52_low"))
    tilt, reasons, signals = 0.0, [], 0

    if per is not None:
        signals += 1
        if per < 15:
            tilt += 2; reasons.append(f"PER {per}배는 전통적 가치투자 기준에 부합")
        elif per < 20:
            tilt += 1
        elif per >= 30:
            tilt -= 2; reasons.append(f"PER {per}배는 안전마진 관점에서 너무 비쌈")
    if dte is not None:
        signals += 1
        if dte > 100:
            tilt -= 1; reasons.append(f"부채비율 {dte}%로 보수적 재무구조 기준에 못 미침")
        elif dte < 30:
            tilt += 1; reasons.append("보수적인 재무구조")
    if div_yield is not None and div_yield >= 2:
        signals += 1
        tilt += 1; reasons.append(f"배당수익률 {div_yield}%로 꾸준한 주주환원")
    if price is not None and hi is not None and lo is not None and hi > lo:
        signals += 1
        pos = (price - lo) / (hi - lo)
        if pos > 0.85:
            tilt -= 1; reasons.append(f"52주 최고가({hi}) 근처(현재 {price})로 안전마진이 부족")
        elif pos < 0.3:
            tilt += 1; reasons.append(f"52주 저점권(저가 {lo})으로 안전마진 확보 가능성")

    key_risk = "안전마진 없이 매수하면 하락장에서 손실 방어력이 약함"
    return {"verdict": _verdict(tilt), "confidence": _confidence(tilt, signals), "reasons": reasons or ["데이터 부족"], "key_risk": key_risk}


def _lynch(data: dict) -> dict:
    peg = _n(data.get("peg_ratio"))
    eg, rg = _n(data.get("earnings_growth_pct")), _n(data.get("revenue_growth_pct"))
    tilt, reasons, signals = 0.0, [], 0

    if peg is not None:
        signals += 1
        if peg < 1:
            tilt += 2; reasons.append(f"PEG {peg}로 성장 대비 매우 저평가")
        elif peg < 2:
            tilt += 1; reasons.append(f"PEG {peg}로 성장 대비 합리적인 가격")
        elif peg >= 3:
            tilt -= 2; reasons.append(f"PEG {peg}로 성장 대비 과도하게 비쌈 (린치 기준 1 안팎이 이상적)")
    if eg is not None:
        signals += 1
        if eg >= 15:
            tilt += 1; reasons.append(f"이익성장률 {eg}%로 양호한 성장세")
        elif eg < 5:
            tilt -= 1; reasons.append(f"이익성장률 {eg}%로 성장 스토리가 약함")
    if eg is not None and rg is not None:
        signals += 1
        if eg > rg:
            tilt += 1; reasons.append(f"이익성장({eg}%)이 매출성장({rg}%)보다 빨라 마진 개선 추세")

    key_risk = "성장이 숫자로 뒷받침되지 않으면 PEG가 금세 비싸 보일 수 있음"
    return {"verdict": _verdict(tilt), "confidence": _confidence(tilt, signals), "reasons": reasons or ["데이터 부족"], "key_risk": key_risk}


def _druckenmiller(data: dict) -> dict:
    price = _n(data.get("price"))
    sma20, sma50, sma200 = _n(data.get("sma20")), _n(data.get("sma50")), _n(data.get("sma200"))
    rsi, macd = _n(data.get("rsi")), _n(data.get("macd_hist"))
    ret3m, vix = _n(data.get("return_3m_pct")), _n(data.get("macro_vix"))
    tilt, reasons, signals = 0.0, [], 0

    if price is not None and sma20 is not None and sma50 is not None and sma200 is not None:
        signals += 1
        if price > sma20 > sma50 > sma200:
            tilt += 2; reasons.append("단기>중기>장기 이동평균 정배열로 뚜렷한 상승 추세")
        elif price < sma20 < sma50 < sma200:
            tilt -= 2; reasons.append("이동평균 역배열로 뚜렷한 하락 추세")
        elif price > sma200:
            tilt += 1
        else:
            tilt -= 1
    if macd is not None:
        signals += 1
        if macd > 0:
            tilt += 1; reasons.append(f"MACD 히스토그램 양수({macd})로 모멘텀 우호적")
        else:
            tilt -= 1; reasons.append(f"MACD 히스토그램 음수({macd})로 모멘텀 약화")
    if rsi is not None:
        signals += 1
        if rsi > 70:
            tilt -= 1; reasons.append(f"RSI {rsi}로 과열권 — 단기 되돌림 리스크")
        elif rsi < 30:
            tilt += 1; reasons.append(f"RSI {rsi}로 과매도권 — 반등 가능성")
    if ret3m is not None:
        signals += 1
        if ret3m > 10:
            tilt += 1; reasons.append(f"최근 3개월 +{ret3m}%의 강한 모멘텀")
        elif ret3m < -10:
            tilt -= 1; reasons.append(f"최근 3개월 {ret3m}%로 추세가 꺾임")
    if vix is not None:
        signals += 1
        if vix > 25:
            tilt -= 1; reasons.append(f"VIX {vix}로 시장 변동성 확대 — 리스크 축소 선호")
        elif vix < 15:
            tilt += 1

    key_risk = "추세가 꺾이면(주요 이동평균 이탈) 즉시 손절해야 하는 포지션 — 펀더멘털 방어 근거는 약함"
    return {"verdict": _verdict(tilt), "confidence": _confidence(tilt, signals), "reasons": reasons or ["데이터 부족"], "key_risk": key_risk}


_SCORERS = {
    "버핏": _buffett,
    "멍거": _munger,
    "그레이엄": _graham,
    "린치": _lynch,
    "드러켄밀러": _druckenmiller,
}


class LegendAnalyst:
    """월가 거장 페르소나 하나를 대변 — 생성 시 persona 이름을 지정한다.

    LLM을 호출하지 않는다. 각 거장의 투자 원칙을 조건문으로 옮긴 규칙 기반
    점수표로 판단한다 — API 비용이 0이고, 실행이 즉시 끝난다.
    """

    def __init__(self, persona: str) -> None:
        if persona not in LEGEND_PERSONAS:
            raise ValueError(f"Unknown legend persona: {persona}")
        self.persona = persona

    def run(self, ticker: str, data: dict) -> dict:
        result = _SCORERS[self.persona](data)
        result["name"] = self.persona
        result["title"] = LEGEND_PERSONAS[self.persona]["title"]
        result["confidence"] = max(0, min(100, int(result.get("confidence", 0) or 0)))
        if result.get("verdict") not in ("매수", "중립", "매도"):
            result["verdict"] = "중립"
        return result
