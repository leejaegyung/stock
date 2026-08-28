"""
Layer 1 — 시장 국면 판정 & 섹터·종목 모멘텀 스코어링 (순수 함수, app import 없음).

우리 분석 파이프라인의 기술 점수 로직(LLM 없는 알고리즘)을 참고해
지수·섹터 ETF·대형주의 추세와 상대강도를 계량화한다.
입력은 이미 정렬된 종가 리스트. 무위험수익률 등 가정은 호출자가 넘긴다.
"""

from __future__ import annotations

import numpy as np


def _clean(closes) -> list[float]:
    return [float(x) for x in closes if x == x and x is not None]


def _ret(closes: list[float], n: int) -> float | None:
    if len(closes) <= n or closes[-n - 1] == 0:
        return None
    return round((closes[-1] / closes[-n - 1] - 1) * 100, 1)


def _sma(closes: list[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return float(np.mean(closes[-n:]))


def _rsi(closes: list[float], n: int = 14) -> float:
    if len(closes) < n + 1:
        return 50.0
    d = np.diff(np.asarray(closes[-(n + 1) :], dtype=float))
    gains = np.where(d > 0, d, 0.0)
    losses = np.where(d < 0, -d, 0.0)
    ag, al = float(np.mean(gains)), float(np.mean(losses))
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    rs = ag / al
    return round(100 - 100 / (1 + rs), 1)


def momentum_profile(closes) -> dict:
    """종가 시계열 → 모멘텀·추세 프로필."""
    c = _clean(closes)
    price = c[-1] if c else None
    ma50, ma200 = _sma(c, 50), _sma(c, 200)
    return {
        "price": round(price, 2) if price else None,
        "ret_1m": _ret(c, 21),
        "ret_3m": _ret(c, 63),
        "ret_6m": _ret(c, 126),
        "rsi": _rsi(c),
        "above_ma50": (price > ma50) if (ma50 and price) else None,
        "above_ma200": (price > ma200) if (ma200 and price) else None,
    }


def relative_strength(closes, bench_closes, n: int = 63) -> float:
    """벤치마크 대비 초과 수익률 (n거래일). 양수면 시장보다 강함."""
    a, b = _ret(_clean(closes), n), _ret(_clean(bench_closes), n)
    if a is None or b is None:
        return 0.0
    return round(a - b, 1)


def trend_score(mp: dict, rel_strength: float = 0.0) -> float:
    """0~100 종합 점수 — 모멘텀 + 추세 + 상대강도, 과열은 감점."""
    s = 50.0
    for key, w in (("ret_1m", 0.5), ("ret_3m", 1.0), ("ret_6m", 0.5)):
        v = mp.get(key)
        if v is not None:
            s += max(-15.0, min(15.0, v * w * 0.5))
    if mp.get("above_ma50"):
        s += 6
    if mp.get("above_ma200") is True:
        s += 9
    elif mp.get("above_ma200") is False:
        s -= 9
    s += max(-12.0, min(12.0, rel_strength * 0.6))
    rsi = mp.get("rsi", 50) or 50
    if rsi > 78:
        s -= 8
    elif rsi < 30:
        s += 4
    return round(max(0.0, min(100.0, s)), 1)


def score_label(score: float) -> tuple[str, str]:
    """종합 점수 → (등급, 색조)."""
    if score >= 68:
        return "유망", "good"
    if score >= 56:
        return "관심", "mid"
    if score >= 42:
        return "중립", "warn"
    return "주의", "bad"


def rank_sectors(sector_scores: dict) -> list[tuple[str, float]]:
    """{key: score} → 점수 내림차순 리스트."""
    return sorted(sector_scores.items(), key=lambda kv: kv[1], reverse=True)


def market_regime(
    cape: float | None,
    vix: float | None,
    tnx: float | None,
    spx_mp: dict | None,
    breadth_pos_ratio: float | None,
) -> dict:
    """
    시장 국면 판정: 강세 / 중립 / 경계 / 방어.

    - breadth_pos_ratio: 섹터 중 200일선 위 비율 (0~1)
    - spx_mp: S&P 500 momentum_profile
    """
    reasons: list[str] = []
    risk = 0

    if vix is not None:
        if vix >= 28:
            risk += 2
            reasons.append(f"VIX {vix:.0f} — 변동성 크게 확대")
        elif vix >= 20:
            risk += 1
            reasons.append(f"VIX {vix:.0f} — 경계 수준")
        elif vix < 14:
            reasons.append(f"VIX {vix:.0f} — 시장 안정")

    if cape is not None and cape >= 35:
        risk += 1
        reasons.append(f"CAPE {cape:.0f} — 역사적 고평가 구간")

    if spx_mp:
        if spx_mp.get("above_ma200") is False:
            risk += 2
            reasons.append("S&P 500 이 200일선 아래 — 중기 하락 추세")
        elif spx_mp.get("above_ma200") is True:
            reasons.append("S&P 500 이 200일선 위 — 중기 상승 추세")

    if breadth_pos_ratio is not None:
        if breadth_pos_ratio >= 0.7:
            reasons.append(f"섹터 {breadth_pos_ratio * 100:.0f}% 가 상승 추세 — 폭넓은 강세")
        elif breadth_pos_ratio <= 0.35:
            risk += 1
            reasons.append(f"섹터 {breadth_pos_ratio * 100:.0f}% 만 상승 추세 — 좁은 장세")

    if tnx is not None and tnx >= 4.8:
        risk += 1
        reasons.append(f"미 10년물 금리 {tnx:.1f}% — 밸류에이션 부담")

    if risk >= 4:
        label, tone = "방어적 대응", "bad"
        guide = "현금·필수소비재·헬스케어·유틸리티 비중을 늘리고, 신규 진입은 분할 매수로."
    elif risk >= 2:
        label, tone = "경계", "warn"
        guide = "성장주 비중을 줄이고 실적·현금흐름이 탄탄한 대형주 위주로 접근."
    elif risk >= 1:
        label, tone = "중립", "mid"
        guide = "지수를 따라가되, 상대적으로 강한 섹터에만 소폭 초과 비중."
    else:
        label, tone = "위험 선호 (강세)", "good"
        guide = "성장·기술·경기민감 섹터에 적극적으로 접근할 수 있는 국면."

    return {"label": label, "tone": tone, "risk_score": risk, "guide": guide, "reasons": reasons}
