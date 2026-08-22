"""
Layer 1 — Pure functions only. No app imports allowed.
All quantitative formulas for the stock analyst service.
Callers supply all assumptions (win_prob, odds_ratio, etc.) and must document them.
"""


def expected_value(win_prob: float, gain: float, loss: float) -> float:
    """EV = P * gain - (1-P) * loss. Positive EV = statistically favourable."""
    return win_prob * gain - (1 - win_prob) * loss


def kelly_fraction(win_prob: float, odds_ratio: float) -> float:
    """Full Kelly fraction. F = P - (1-P)/R. F <= 0 means do not enter."""
    if odds_ratio <= 0:
        return 0.0
    return win_prob - (1 - win_prob) / odds_ratio


def half_kelly(win_prob: float, odds_ratio: float) -> float:
    """Half Kelly (recommended for live trading) = kelly_fraction / 2."""
    return kelly_fraction(win_prob, odds_ratio) / 2


def cashflow_pattern(operating: float, investing: float, financing: float) -> str:
    """
    Classify company lifecycle stage from sign combination of three cash flow categories.
    Signs are booleans: True = positive cash flow, False = negative.
    """
    op = operating >= 0
    inv = investing >= 0
    fin = financing >= 0

    if op and not inv and not fin:
        return "우량 성숙기"
    if op and not inv and fin:
        return "성장기"
    if op and inv and not fin:
        return "구조조정 신호"
    if not op and inv and fin:
        return "위험"
    # Remaining combinations are less canonical; return descriptive label
    if op and inv and fin:
        return "자산매각·외부조달 (검토 필요)"
    if not op and not inv and fin:
        return "본업·투자 적자, 외부차입 (위험)"
    if not op and inv and not fin:
        return "본업 적자, 자산매각+부채상환 (위험)"
    return "본업·투자·재무 전부 적자 (심각)"


def per_roe_judgment(
    per: float,
    roe: float,
    sector_avg_per: float,
    sector_avg_roe: float,
) -> str:
    """
    Relative valuation judgment based on PER and ROE vs sector averages.
    Returns a concise qualitative label.
    """
    low_per = per < sector_avg_per
    high_roe = roe > sector_avg_roe

    if low_per and high_roe:
        return "저평가 우량주 후보 (저PER + 고ROE)"
    if low_per and not high_roe:
        return "저평가이나 수익성 평범 (저PER + 저ROE)"
    if not low_per and high_roe:
        return "고평가이나 우량 (고PER + 고ROE) — PEG 확인 필요"
    return "고평가 + 수익성 평범 (고PER + 저ROE) — 주의"


def cape_judgment(cape: float) -> str:
    """
    Market-level bubble assessment using Shiller CAPE.
    Historical average ~17. Thresholds: <15 저평가, 15-25 적정, 25-35 고평가, >35 버블.
    """
    if cape < 15:
        return "저평가"
    if cape < 25:
        return "적정"
    if cape < 35:
        return "고평가"
    return "버블"


def peg_ratio(per: float, earnings_growth_pct: float) -> float:
    """PEG = PER / earnings_growth_pct. Lower is better; <1 often considered undervalued."""
    if earnings_growth_pct <= 0:
        return float("inf")
    return per / earnings_growth_pct
