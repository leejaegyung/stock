"""
Layer 1 — 매매 타이밍·가격대 산출 (순수 함수, app import 없음).

이동평균·볼린저밴드·ATR·스윙 고저(전부 기술적 지표 dict 로 전달) 만으로
매수 희망 구간 / 분할 추가매수 / 목표가 / 손절가를 계산한다.
LLM 은 관여하지 않는다 (STOCK_ANALYST_SERVICE.md §"LLM 해석, 코드 계산").
결과는 참고용이며 투자자문이 아니다.
"""

from __future__ import annotations


def trade_plan(ta: dict, quant: dict, verdict: str) -> dict | None:
    """`_technical_indicators` 결과(ta)와 계량 지표(quant)로 매매 가격대 산출.

    필요한 ta 키: price, sma20, sma50, bb_upper, bb_lower, atr14,
                   hi_20, lo_20, hi_52w. 없으면 가격 기준 근사값 사용.
    """
    if not ta.get("ok"):
        return None

    price = float(ta["price"])
    sma20 = float(ta.get("sma20") or price)
    sma50 = float(ta.get("sma50") or sma20)
    bb_up = float(ta.get("bb_upper") or price * 1.05)
    bb_lo = float(ta.get("bb_lower") or price * 0.95)
    atr = float(ta.get("atr14") or price * 0.02)
    hi20 = float(ta.get("hi_20") or bb_up)
    lo20 = float(ta.get("lo_20") or bb_lo)
    hi52 = float(ta.get("hi_52w") or hi20)
    ev = quant.get("ev")

    supports = sorted(
        (s for s in (sma20, sma50, bb_lo, lo20) if s and s < price), reverse=True
    )
    resists = sorted(r for r in (sma20, sma50, bb_up, hi20, hi52) if r and r > price)
    near_support = supports[0] if supports else price - atr
    deep_support = supports[1] if len(supports) > 1 else near_support - atr
    near_resist = resists[0] if resists else price + 2 * atr
    far_resist = resists[1] if len(resists) > 1 else near_resist + 2 * atr

    entry_high = min(price, sma20) if sma20 <= price * 1.02 else price
    entry_low = min(near_support, entry_high - 0.5 * atr)
    if entry_low >= entry_high:
        entry_low = entry_high - atr
    add_zone = min(deep_support, entry_low - atr)

    ev_target = price * (1 + ev / 100) if ev is not None else None
    target1 = near_resist
    if ev_target and ev_target > price:
        target1 = min(target1, ev_target)
    target1 = max(target1, price * 1.03)
    target2 = max(far_resist, target1 * 1.05)

    # 손절: 깊은 지지 살짝 아래. 단 최대 손실은 -15% 로 제한, 반드시 진입가 아래.
    stop = min(entry_low, add_zone) - 0.5 * atr
    stop = max(stop, price * 0.85)
    stop = min(stop, entry_low - 0.3 * atr)

    entry_mid = (entry_low + entry_high) / 2
    risk, reward = entry_mid - stop, target1 - entry_mid
    rr = round(reward / risk, 1) if risk > 0 else None

    return {
        "price": round(price, 2),
        "entry_low": round(entry_low, 2),
        "entry_high": round(entry_high, 2),
        "add_zone": round(add_zone, 2),
        "target1": round(target1, 2),
        "target2": round(target2, 2),
        "stop": round(stop, 2),
        "rr": rr,
        "verdict": verdict,
    }


def fmt_px(v: float, market: str) -> str:
    """가격을 시장 통화 기호와 함께 문자열로."""
    if market == "US":
        return f"${v:,.2f}"
    return f"₩{v:,.0f}"


def trade_plan_md(tp: dict | None, market: str) -> list[str]:
    """매매 계획을 리포트용 마크다운 줄 리스트로 (verdict 별 문구)."""
    if not tp:
        return []

    def f(v: float) -> str:
        return fmt_px(v, market)

    vd = tp.get("verdict", "보유")
    lines = ["**매매 타이밍·가격** (차트 기준 자동 계산 — 참고용, 투자자문 아님)"]
    if vd in ("매수", "추가매수"):
        lines.append(f"- 매수 희망 구간: {f(tp['entry_low'])} ~ {f(tp['entry_high'])} (현재 {f(tp['price'])})")
        lines.append(f"- 분할 추가매수: {f(tp['add_zone'])} 부근까지 밀릴 때")
        lines.append(f"- 목표가: 1차 {f(tp['target1'])} · 2차 {f(tp['target2'])}")
        lines.append(f"- 손절: {f(tp['stop'])} 이탈 시")
        if tp.get("rr"):
            lines.append(f"- 손익비(1차 목표 기준): 약 {tp['rr']} : 1")
    elif vd == "보유":
        lines.append(f"- 보유 유지. 추가매수는 {f(tp['entry_low'])} ~ {f(tp['entry_high'])} 구간")
        lines.append(f"- 일부 차익실현: {f(tp['target1'])} 부근 저항")
        lines.append(f"- 손절: {f(tp['stop'])} 이탈 시 비중 축소")
    else:  # 매도
        lines.append(f"- 반등 매도 구간: {f(tp['target1'])} 부근 저항")
        lines.append(f"- 지지 {f(tp['entry_low'])} 회복 실패 시 정리")
        lines.append(f"- 손절: {f(tp['stop'])} 이탈")
    lines.append("")
    return lines
