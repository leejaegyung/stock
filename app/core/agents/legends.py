"""
월가의 전설적 투자자 5인의 투자 철학을 대변하는 규칙 기반(rule-based) 분석기.

실존 인물이 아니라 각자 공개적으로 잘 알려진 투자 원칙(저서·인터뷰·주주서한 등
공개된 사실)을 바탕으로 만든 페르소나다. virattt/ai-hedge-fund(오픈소스, MIT
라이선스) 저장소의 "여러 투자자 관점으로 같은 데이터를 보고 각자 판단한다"는
구조에서 착안했다.

★ 비용 원칙: 우리 시스템은 돈이 들면 안 된다 — 처음엔 LLM(Sonnet → Haiku)으로
구현했지만, Anthropic API는 선불 크레딧이 바닥나면 (모델과 무관하게) 모든 호출이
막힌다는 걸 실제로 확인했다. 그래서 LLM 호출을 완전히 없애고, 각 거장의 투자
원칙을 코드로 옮긴 "규칙 기반 점수표"로 짰다. 데이터는 이미 app.core.algo_pipeline/
legends_pipeline이 계산·수집해둔 재무·기술·업종비교·애널리스트 컨센서스 지표를
그대로 쓰고, 판단 로직만 사람이 설계한 조건문으로 대체했다 — API 과금이 전혀
없고, 실행도 즉시(동기) 끝난다.

★ 보완 이력: 처음 버전은 절대 기준값(고정 PER·ROE 임계값)만 보고 판단해서
"업종 맥락을 모른다"·"PBR·애널리스트 컨센서스 같은 이미 갖고 있던 데이터를
안 쓴다"는 한계가 있었다. 이번 보강에서:
  ① 그레이엄에 PBR(청산가치 대비 가격)과 업종 평균 PER 대비 상대 밸류에이션을 추가
  ② 린치에 "기업 유형 분류"(고성장/대형우량/저성장/회생주)를 복원해 유형별로
     PEG 적정 기준을 다르게 적용 — 원래 철학 문서에 있었지만 규칙으로는 안 만들어져 있었음
  ③ 드러켄밀러·그레이엄에 CAPE(시장 전반 밸류에이션)·10년물 금리를 연결 — 이미
     수집하고 있었는데 아무도 안 쓰고 있던 데이터
  ④ 확신도(confidence) 계산을 "점수 크기"만 보던 것에서 "신호들이 서로 얼마나
     일치하는가"(agreement)까지 반영하도록 바꿔, 신호가 많아도 서로 충돌하면
     확신도가 낮게 나오게 함 — 더 현실적인 보수적 확신도
Sacred Rule(판단은 해석, 계산은 숫자)은 그대로 유지된다.
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
            "내재가치 대비 안전마진, 낮은 PER·PBR, 보수적 재무구조, 꾸준한 배당. "
            "안전마진이 부족하면 사업이 좋아 보여도 주저하지 않고 중립·매도."
        ),
    },
    "린치": {
        "title": "피터 린치 — PEG와 성장 스토리",
        "philosophy": (
            "아는 것에 투자하라, PEG 비율로 본 성장 대비 밸류에이션, 가속되는 성장, "
            "기업을 성장주/대형우량주/저성장주/회생주로 나눠 유형별로 다른 기준을 적용."
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


def _confidence(tilt: float, pos_hits: int, neg_hits: int) -> int:
    """점수 크기뿐 아니라 '신호들이 서로 얼마나 일치하는가'까지 반영한다.

    신호가 5개 잡혀도 2개는 매수, 3개는 매도 방향이면(agreement가 낮음)
    확신도가 낮게 나온다 — 신호 개수만 보던 이전 버전보다 보수적이고 현실적이다.
    """
    total = pos_hits + neg_hits
    if total == 0:
        return 20
    agreement = max(pos_hits, neg_hits) / total  # 0.5=정반대로 갈림 ~ 1.0=만장일치
    conviction = min(abs(tilt), 8) / 8
    score = 25 + agreement * 45 + conviction * 25
    return max(20, min(95, round(score)))


class _Scorer:
    """tilt(방향성 점수)와 pos/neg 신호 개수를 함께 누적하는 작은 헬퍼."""

    def __init__(self) -> None:
        self.tilt = 0.0
        self.pos = 0
        self.neg = 0
        self.reasons: list[str] = []

    def add(self, points: float, reason: str | None = None) -> None:
        self.tilt += points
        if points > 0:
            self.pos += 1
        elif points < 0:
            self.neg += 1
        if reason:
            self.reasons.append(reason)

    def result(self, key_risk: str) -> dict:
        return {
            "verdict": _verdict(self.tilt),
            "confidence": _confidence(self.tilt, self.pos, self.neg),
            "reasons": self.reasons or ["데이터 부족"],
            "key_risk": key_risk,
        }


def _buffett(data: dict) -> dict:
    roe, margin = _n(data.get("roe_pct")), _n(data.get("profit_margin_pct"))
    opm = _n(data.get("operating_margin_pct"))
    dte, per = _n(data.get("debt_to_equity_pct")), _n(data.get("per"))
    y10 = _n(data.get("macro_10y_yield"))
    s = _Scorer()

    if roe is not None:
        if roe >= 20:
            s.add(2, f"ROE {roe}%로 높고 꾸준한 자본효율 — 경제적 해자의 신호")
        elif roe >= 12:
            s.add(1, f"ROE {roe}%는 양호한 수준")
        else:
            s.add(-1, f"ROE {roe}%는 다소 낮아 자본효율이 아쉬움")
    if margin is not None:
        if margin >= 20:
            s.add(1, f"순이익률 {margin}%로 높은 가격 결정력을 시사")
        elif margin < 8:
            s.add(-1, f"순이익률 {margin}%로 마진이 얇은 편")
    if opm is not None:
        if opm >= 25:
            s.add(1, f"영업이익률 {opm}%로 핵심 사업의 수익성이 뛰어남")
        elif opm < 10:
            s.add(-1, f"영업이익률 {opm}%로 핵심 사업 수익성이 약함")
    if dte is not None:
        if dte < 50:
            s.add(1, f"부채비율 {dte}%로 보수적인 재무구조")
        elif dte > 150:
            s.add(-2, f"부채비율 {dte}%로 부채 부담이 커 보수적 기준에 못 미침")
        elif dte > 100:
            s.add(-1, f"부채비율 {dte}%로 다소 높은 편")
    if per is not None:
        if per > 35:
            s.add(-1, f"PER {per}배는 '적당한 가격' 기준에서 다소 비쌈")
        elif per < 15:
            s.add(1, f"PER {per}배로 밸류에이션 부담이 적음")
        if y10 is not None and y10 >= 4.5 and per > 25:
            s.add(-1, f"10년물 금리 {y10}%로 높은 환경에서 PER {per}배는 상대적으로 더 비쌈")

    key_risk = "부채가 늘어나거나 해자가 훼손되면 장기 보유 전제가 흔들림" if (dte or 0) > 100 \
        else "경제적 해자가 생각보다 약하거나 경쟁에 잠식될 가능성"
    return s.result(key_risk)


def _munger(data: dict) -> dict:
    roe, margin = _n(data.get("roe_pct")), _n(data.get("profit_margin_pct"))
    opm = _n(data.get("operating_margin_pct"))
    dte, peg, pbr = _n(data.get("debt_to_equity_pct")), _n(data.get("peg_ratio")), _n(data.get("pbr"))
    inst = _n(data.get("institutional_held_pct"))
    s = _Scorer()

    if roe is not None:
        if roe >= 20:
            s.add(2, f"ROE {roe}%는 훌륭한 사업의 증거")
        elif roe >= 12:
            s.add(1, f"ROE {roe}%는 양호한 수준")
        else:
            s.add(-1, f"ROE {roe}%로 사업의 질이 눈에 띄지 않음")
    if margin is not None and margin >= 25:
        s.add(2, f"순이익률 {margin}%로 가격결정력이 뚜렷함")
    elif margin is not None and margin >= 15:
        s.add(1, f"순이익률 {margin}%는 양호한 수준")
    if opm is not None and opm >= 25:
        s.add(1, f"영업이익률 {opm}%로 사업의 질이 우수")
    if dte is not None:
        if dte > 150:
            s.add(-2, f"부채비율 {dte}% — '바보짓을 피하라' 원칙에서 경계 대상")
        elif dte > 100:
            s.add(-1, f"부채비율 {dte}%로 다소 높은 편")
        elif dte < 50:
            s.add(1, f"부채비율 {dte}%로 보수적인 재무구조")
    if peg is not None:
        if peg < 1.5:
            s.add(1, f"PEG {peg}로 성장 대비 가격이 합리적")
        elif peg > 3:
            s.add(-1, f"PEG {peg}로 성장 대비 비싼 가격 — 훌륭한 기업도 너무 비싸면 곤란")
    if pbr is not None and peg is not None and pbr > 8 and peg > 3:
        s.add(-1, f"PBR {pbr}배 + PEG {peg} — 두 지표 모두 비싸 이중으로 과도한 지불 신호")
    if inst is not None and inst >= 70:
        s.add(1, f"기관 보유비율 {inst}%로 전문투자자들의 신뢰가 확인됨")

    key_risk = "복잡성이 숨어있거나(과도한 부채·불투명한 회계) 경영진 신뢰가 흔들릴 경우"
    return s.result(key_risk)


def _graham(data: dict) -> dict:
    per, pbr = _n(data.get("per")), _n(data.get("pbr"))
    dte = _n(data.get("debt_to_equity_pct"))
    div_yield = _n(data.get("dividend_yield_pct"))
    price, hi, lo = _n(data.get("price")), _n(data.get("week52_high")), _n(data.get("week52_low"))
    peer_per = _n(data.get("peer_avg_per"))
    cape = _n(data.get("macro_cape"))
    s = _Scorer()

    if per is not None:
        if per < 15:
            s.add(2, f"PER {per}배는 전통적 가치투자 기준에 부합")
        elif per < 20:
            s.add(1, f"PER {per}배는 비교적 합리적인 수준")
        elif per >= 30:
            s.add(-2, f"PER {per}배는 안전마진 관점에서 너무 비쌈")
    if pbr is not None:
        if pbr < 1.5:
            s.add(2, f"PBR {pbr}배로 청산가치 대비 저평가 — 안전마진 확보")
        elif pbr > 3:
            s.add(-1, f"PBR {pbr}배로 자산가치 대비 비싼 가격")
    if per is not None and peer_per is not None and peer_per > 0:
        if per < peer_per * 0.8:
            s.add(1, f"업종 평균 PER({peer_per}배) 대비 저평가(현재 {per}배)")
        elif per > peer_per * 1.3:
            s.add(-1, f"업종 평균 PER({peer_per}배) 대비 고평가(현재 {per}배)")
    if dte is not None:
        if dte > 100:
            s.add(-1, f"부채비율 {dte}%로 보수적 재무구조 기준에 못 미침")
        elif dte < 30:
            s.add(1, "보수적인 재무구조")
    if div_yield is not None and div_yield >= 2:
        s.add(1, f"배당수익률 {div_yield}%로 꾸준한 주주환원")
    if price is not None and hi is not None and lo is not None and hi > lo:
        pos = (price - lo) / (hi - lo)
        if pos > 0.85:
            s.add(-1, f"52주 최고가({hi}) 근처(현재 {price})로 안전마진이 부족")
        elif pos < 0.3:
            s.add(1, f"52주 저점권(저가 {lo})으로 안전마진 확보 가능성")
    if cape is not None:
        if cape >= 32:
            s.add(-1, f"CAPE {cape}로 시장 전반이 역사적으로 비싼 구간 — 보수적 접근 필요")
        elif cape < 20:
            s.add(1, f"CAPE {cape}로 시장 전반이 역사적으로 싼 구간")

    key_risk = "안전마진 없이 매수하면 하락장에서 손실 방어력이 약함"
    return s.result(key_risk)


def _lynch_classify(eg: float | None, rg: float | None, market_cap: float | None) -> tuple[str, float | None]:
    """린치의 기업 유형 분류 — 유형별로 적정 PEG 기준이 다르다.

    :returns: (유형명, 이 유형에서 적정하다고 보는 PEG 기준. 회생주는 PEG로 안 보므로 None)
    """
    if eg is not None and eg < 0 and rg is not None and rg > 0:
        return "회생주", None
    if eg is not None and eg >= 25:
        return "고성장주", 1.0
    if market_cap and market_cap >= 50_000_000_000 and eg is not None and 8 <= eg < 25:
        return "대형우량주", 2.0
    if eg is not None and eg < 8:
        return "저성장주", 1.2
    return "중견성장주", 1.5


def _lynch(data: dict) -> dict:
    peg = _n(data.get("peg_ratio"))
    eg, rg = _n(data.get("earnings_growth_pct")), _n(data.get("revenue_growth_pct"))
    qoq = _n(data.get("earnings_qoq_growth_pct"))
    market_cap = _n(data.get("market_cap"))
    s = _Scorer()

    ctype, threshold = _lynch_classify(eg, rg, market_cap)
    s.reasons.append(f"기업 유형: {ctype}" + (f" (적정 PEG 기준 {threshold})" if threshold else " — PEG 대신 회복 신호를 봄"))

    if threshold is not None and peg is not None:
        if peg < threshold * 0.7:
            s.add(2, f"PEG {peg}로 '{ctype}' 기준({threshold}) 대비 매우 저평가")
        elif peg < threshold:
            s.add(1, f"PEG {peg}로 '{ctype}' 기준 대비 합리적인 가격")
        elif peg > threshold * 2:
            s.add(-2, f"PEG {peg}로 '{ctype}' 기준({threshold}) 대비 과도하게 비쌈")
        elif peg > threshold * 1.3:
            s.add(-1, f"PEG {peg}로 '{ctype}' 기준 대비 다소 비쌈")
    elif ctype == "회생주" and rg is not None:
        if rg > 10:
            s.add(1, f"매출 성장률 {rg}%로 회생 국면 진입 신호")
        else:
            s.add(-1, "회생 신호(매출 성장 재개)가 아직 뚜렷하지 않음")

    if eg is not None:
        if eg < 5:
            s.add(-1, f"이익성장률 {eg}%로 성장 스토리가 약함")
    if eg is not None and rg is not None and eg > rg:
        s.add(1, f"이익성장({eg}%)이 매출성장({rg}%)보다 빨라 마진 개선 추세")
    if qoq is not None and eg is not None and qoq > eg:
        s.add(1, f"최근 분기 성장({qoq}%)이 연간 성장({eg}%)보다 빨라 가속 중")

    key_risk = "성장이 숫자로 뒷받침되지 않으면 PEG가 금세 비싸 보일 수 있음"
    return s.result(key_risk)


def _druckenmiller(data: dict) -> dict:
    price = _n(data.get("price"))
    sma20, sma50, sma200 = _n(data.get("sma20")), _n(data.get("sma50")), _n(data.get("sma200"))
    rsi, macd = _n(data.get("rsi")), _n(data.get("macd_hist"))
    ret3m, vix = _n(data.get("return_3m_pct")), _n(data.get("macro_vix"))
    cape, y10 = _n(data.get("macro_cape")), _n(data.get("macro_10y_yield"))
    s = _Scorer()

    if price is not None and sma20 is not None and sma50 is not None and sma200 is not None:
        if price > sma20 > sma50 > sma200:
            s.add(2, "단기>중기>장기 이동평균 정배열로 뚜렷한 상승 추세")
        elif price < sma20 < sma50 < sma200:
            s.add(-2, "이동평균 역배열로 뚜렷한 하락 추세")
        elif price > sma200:
            s.add(1, f"이동평균이 뒤섞여 있지만(정배열·역배열 아님) 장기선({sma200}) 위라 추세는 아직 살아있음")
        else:
            s.add(-1, f"이동평균이 뒤섞여 있고 장기선({sma200}) 아래라 추세가 꺾인 쪽에 가까움")
    if macd is not None:
        if macd > 0:
            s.add(1, f"MACD 히스토그램 양수({macd})로 모멘텀 우호적")
        else:
            s.add(-1, f"MACD 히스토그램 음수({macd})로 모멘텀 약화")
    if rsi is not None:
        if rsi > 70:
            s.add(-1, f"RSI {rsi}로 과열권 — 단기 되돌림 리스크")
        elif rsi < 30:
            s.add(1, f"RSI {rsi}로 과매도권 — 반등 가능성")
    if ret3m is not None:
        if ret3m > 10:
            s.add(1, f"최근 3개월 +{ret3m}%의 강한 모멘텀")
        elif ret3m < -10:
            s.add(-1, f"최근 3개월 {ret3m}%로 추세가 꺾임")
    if vix is not None:
        if vix > 25:
            s.add(-1, f"VIX {vix}로 시장 변동성 확대 — 리스크 축소 선호")
        elif vix < 15:
            s.add(1, f"VIX {vix}로 시장이 안정적 — 위험자산에 우호적")
    if cape is not None:
        if cape >= 32:
            s.add(-1, f"CAPE {cape}로 시장 전반이 과열 — 거시 역풍")
        elif cape < 18:
            s.add(1, f"CAPE {cape}로 시장 전반이 저평가 구간")
    if y10 is not None:
        if y10 >= 4.5:
            s.add(-1, f"10년물 금리 {y10}%로 긴축적 환경 — 위험자산에 역풍")
        elif y10 < 2.5:
            s.add(1, f"10년물 금리 {y10}%로 완화적 환경")

    key_risk = "추세가 꺾이면(주요 이동평균 이탈) 즉시 손절해야 하는 포지션 — 펀더멘털 방어 근거는 약함"
    return s.result(key_risk)


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
