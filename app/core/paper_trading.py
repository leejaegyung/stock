"""
Layer 1 — 모의투자(가상 매매) 의사결정 순수 함수 (app import 없음).

실제 돈은 전혀 오가지 않는다. 우리 알고리즘(analyze_stock_algo)의 결론과
trade_plan(진입가·목표가·손절가)을 그대로 따라가는 가상 체결을 계산해,
"이 워크플로우를 실제로 따랐다면 어땠을까"를 데이터로 남기는 것이 목적이다
(신호 적중률·확신도 모델 검증·개선용 학습 데이터).

포지션 크기는 §4.2 하프켈리 비중을 그대로 쓰되, 계좌 보호를 위해 상한을
추가로 건다(한 종목에 자산의 일정 비율 이상 몰지 않도록).
"""

from __future__ import annotations

MAX_POSITION_PCT = 0.25   # 한 종목당 최대 비중 (25%) — half_kelly 가 더 커도 이 안에서 캡
MIN_TRADE_KRW = 10_000     # 이 미만이면 매매를 의미 없다고 보고 건너뜀


def position_size(equity_krw: float, kelly_pct: float, price_krw: float) -> dict:
    """켈리 비중 기준 매수 수량(정수 주) 산출. 살 수 없으면 qty=0.

    :param equity_krw: 계좌 평가금액(현금 기준, 원)
    :param kelly_pct: half_kelly 권장 비중 (0~100)
    :param price_krw: 1주당 원화 환산 가격
    """
    if equity_krw <= 0 or price_krw <= 0:
        return {"qty": 0, "budget_krw": 0.0, "weight_pct": 0.0}

    weight = max(0.0, min(kelly_pct / 100.0, MAX_POSITION_PCT))
    budget = equity_krw * weight
    if budget < MIN_TRADE_KRW:
        return {"qty": 0, "budget_krw": 0.0, "weight_pct": round(weight * 100, 1)}

    qty = int(budget // price_krw)
    return {
        "qty": qty,
        "budget_krw": round(qty * price_krw),
        "weight_pct": round(weight * 100, 1),
    }


def should_exit(
    entry_price: float,
    target1: float | None,
    target2: float | None,
    stop: float | None,
    current_price: float,
    verdict: str | None = None,
    days_held: int | None = None,
    max_hold_days: int = 90,
) -> dict | None:
    """청산 트리거 판정. 위험관리 우선순위: 손절 > 2차목표 > 1차목표 > 결론반전 > 보유기간초과.

    반환 None 이면 계속 보유. 아니면 {"reason": str, "price": float}.
    """
    if stop is not None and current_price <= stop:
        return {"reason": "stop", "price": current_price}
    if target2 is not None and current_price >= target2:
        return {"reason": "target2", "price": current_price}
    if target1 is not None and current_price >= target1:
        return {"reason": "target1", "price": current_price}
    if verdict == "매도":
        return {"reason": "verdict_sell", "price": current_price}
    if days_held is not None and days_held >= max_hold_days:
        return {"reason": "timeout", "price": current_price}
    return None


def compute_pnl(entry_price_krw: float, exit_price_krw: float, qty: float) -> dict:
    """청산 손익(원화 기준) 계산."""
    pnl_krw = (exit_price_krw - entry_price_krw) * qty
    pnl_pct = (exit_price_krw / entry_price_krw - 1) * 100 if entry_price_krw else 0.0
    return {"pnl_krw": round(pnl_krw), "pnl_pct": round(pnl_pct, 2)}


EXIT_REASON_LABEL = {
    "stop": "손절",
    "target1": "1차 목표 도달",
    "target2": "2차 목표 도달",
    "verdict_sell": "결론 매도 전환",
    "timeout": "보유기간 초과",
    "manual": "수동 청산",
}


def summarize_trades(trades: list[dict]) -> dict:
    """청산 완료된 거래 목록(pnl_pct 포함) → 승률·평균손익 등 통계."""
    closed = [t for t in trades if t.get("pnl_pct") is not None]
    n = len(closed)
    if n == 0:
        return {"count": 0, "win_rate": None, "avg_pnl_pct": None, "total_pnl_krw": 0}
    wins = sum(1 for t in closed if t["pnl_pct"] > 0)
    return {
        "count": n,
        "wins": wins,
        "win_rate": round(wins / n, 3),
        "avg_pnl_pct": round(sum(t["pnl_pct"] for t in closed) / n, 2),
        "total_pnl_krw": round(sum(t.get("pnl_krw") or 0 for t in closed)),
    }


MIN_TRACK_RECORD_N = 5  # 이 미만 표본은 "학습" 근거로 쓰지 않는다 (노이즈 방지)


def breakdown_by(trades: list[dict], key: str) -> list[dict]:
    """청산된 거래를 key 값별로 묶어 승률·평균손익 산출 — 알고리즘 검증/학습용.

    예: breakdown_by(trades, "entry_confidence_grade")
        → [{"key":"상","count":18,"win_rate":0.61,...}, {"key":"중",...}, ...]
    표본이 적을수록 승률이 크게 흔들리므로, 이 비교로 알고리즘 가중치를 자동
    조정하지는 않는다 — 사람이 참고할 수 있게 투명하게 보여주는 것이 목적이다.
    """
    groups: dict = {}
    for t in trades:
        if t.get("pnl_pct") is None:
            continue
        k = t.get(key) or "미상"
        groups.setdefault(k, []).append(t)
    out = [dict(summarize_trades(ts), key=k) for k, ts in groups.items()]
    out.sort(key=lambda x: x["count"], reverse=True)
    return out
