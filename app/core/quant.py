"""
Layer 1 — 포트폴리오 계량 분석 (순수 함수, app import 없음).

Goldman Sachs `gs-quant` (github.com/goldmansachs/gs-quant) 의
`gs_quant.timeseries.econometrics` / `statistics` / `backtests` 에서 쓰는
표준 지표·워크플로우를 외부 API 없이 self-contained 로 재구현한 것.

- 입력은 이미 정렬된 일간 수익률(list[float]) 또는 자산×시간 행렬(list[list[float]]).
- 연율화 계수는 거래일 기준 252.
- 무위험수익률 등 모든 가정은 호출자가 명시적으로 넘긴다 (formulas.py 규약과 동일).
"""

from __future__ import annotations

import math

import numpy as np

TRADING_DAYS = 252


# ── 수익률 시계열 ────────────────────────────────────────────────────────────


def daily_returns(prices: list[float]) -> list[float]:
    """가격 시계열 → 일간 단순수익률."""
    out: list[float] = []
    for i in range(1, len(prices)):
        p0 = prices[i - 1]
        if p0:
            out.append(prices[i] / p0 - 1.0)
    return out


def cumulative_return(returns: list[float]) -> float:
    """기간 누적 수익률 (geometric)."""
    c = 1.0
    for r in returns:
        c *= 1.0 + r
    return c - 1.0


def cagr(returns: list[float]) -> float:
    """일간 수익률 → 연평균 복리수익률(CAGR)."""
    n = len(returns)
    if n == 0:
        return 0.0
    total = cumulative_return(returns)
    base = 1.0 + total
    if base <= 0:
        return -1.0
    return base ** (TRADING_DAYS / n) - 1.0


def equity_curve(returns: list[float], initial: float = 1.0) -> list[float]:
    """일간 수익률 → 누적 가치 곡선."""
    eq = initial
    out = [initial]
    for r in returns:
        eq *= 1.0 + r
        out.append(eq)
    return out


# ── 위험 지표 (gs_quant.timeseries.econometrics) ─────────────────────────────


def annualized_volatility(returns: list[float]) -> float:
    """연율화 변동성 = std(daily) * sqrt(252)."""
    if len(returns) < 2:
        return 0.0
    v = float(np.std(returns, ddof=1) * math.sqrt(TRADING_DAYS))
    return v if v > 1e-12 else 0.0


def downside_deviation(returns: list[float], mar_daily: float = 0.0) -> float:
    """하방 편차 (목표수익률 mar 이하 구간만) — 연율화."""
    if not returns:
        return 0.0
    sq = [min(0.0, r - mar_daily) ** 2 for r in returns]
    return float(math.sqrt(sum(sq) / len(sq)) * math.sqrt(TRADING_DAYS))


def sharpe_ratio(returns: list[float], rf_annual: float = 0.0) -> float:
    """연율화 샤프 지수. gs-quant 규약: 초과수익 평균 / 표준편차 * sqrt(252)."""
    if len(returns) < 2:
        return 0.0
    rf_daily = rf_annual / TRADING_DAYS
    sd = float(np.std(returns, ddof=1))
    if sd < 1e-12:
        return 0.0
    excess_mean = float(np.mean(returns)) - rf_daily
    return excess_mean / sd * math.sqrt(TRADING_DAYS)


def sortino_ratio(returns: list[float], rf_annual: float = 0.0) -> float:
    """연율화 소르티노 지수 = 연율 초과수익 / 하방편차."""
    if len(returns) < 2:
        return 0.0
    rf_daily = rf_annual / TRADING_DAYS
    dd = downside_deviation(returns, rf_daily)
    if dd < 1e-12:
        return 0.0
    ann_excess = (float(np.mean(returns)) - rf_daily) * TRADING_DAYS
    return ann_excess / dd


def max_drawdown(returns: list[float]) -> float:
    """최대 낙폭 (peak-to-trough). gs-quant 규약대로 음수 비율 반환 (예: -0.2)."""
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in returns:
        eq *= 1.0 + r
        peak = max(peak, eq)
        if peak:
            mdd = min(mdd, eq / peak - 1.0)
    return mdd


def historical_var(returns: list[float], level: float = 0.95) -> float:
    """1일 역사적 VaR — 양수 손실 비율 (예: 0.031 = -3.1% 손실)."""
    if not returns:
        return 0.0
    q = float(np.percentile(returns, (1.0 - level) * 100.0))
    return max(0.0, -q)


def historical_cvar(returns: list[float], level: float = 0.95) -> float:
    """조건부 VaR (Expected Shortfall) — VaR 초과 손실의 평균."""
    if not returns:
        return 0.0
    q = float(np.percentile(returns, (1.0 - level) * 100.0))
    tail = [r for r in returns if r <= q]
    if not tail:
        return max(0.0, -q)
    return max(0.0, -float(np.mean(tail)))


# ── 상관·베타 ───────────────────────────────────────────────────────────────


def beta(asset_returns: list[float], market_returns: list[float]) -> float:
    """시장 대비 베타 = cov(a, m) / var(m)."""
    n = min(len(asset_returns), len(market_returns))
    if n < 2:
        return 0.0
    a = np.asarray(asset_returns[-n:], dtype=float)
    m = np.asarray(market_returns[-n:], dtype=float)
    var_m = float(np.var(m, ddof=1))
    if var_m < 1e-18:
        return 0.0
    return float(np.cov(a, m, ddof=1)[0][1] / var_m)


def correlation(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    x = np.asarray(a[-n:], dtype=float)
    y = np.asarray(b[-n:], dtype=float)
    if np.std(x) == 0 or np.std(y) == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0][1])


def correlation_matrix(returns_by_asset: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    keys = list(returns_by_asset)
    return {
        k: {j: round(correlation(returns_by_asset[k], returns_by_asset[j]), 3) for j in keys}
        for k in keys
    }


def average_pairwise_correlation(returns_by_asset: dict[str, list[float]]) -> float:
    keys = list(returns_by_asset)
    vals: list[float] = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            vals.append(correlation(returns_by_asset[keys[i]], returns_by_asset[keys[j]]))
    return float(np.mean(vals)) if vals else 0.0


# ── 집중도·분산 ─────────────────────────────────────────────────────────────


def herfindahl_index(weights: list[float]) -> float:
    """HHI = Σ w². 1/N (완전분산) ~ 1 (완전집중)."""
    return float(sum(w * w for w in weights))


def effective_holdings(weights: list[float]) -> float:
    """유효 종목 수 = 1 / HHI."""
    h = herfindahl_index(weights)
    return 1.0 / h if h else 0.0


def portfolio_returns(returns_matrix: list[list[float]], weights: list[float]) -> list[float]:
    """자산×시간 행렬 + 고정 비중 → 포트폴리오 일간 수익률 (리밸런싱 가정)."""
    if not returns_matrix:
        return []
    M = np.asarray(returns_matrix, dtype=float)
    w = np.asarray(weights, dtype=float)
    return [float(x) for x in np.dot(w, M)]


def diversification_ratio(returns_matrix: list[list[float]], weights: list[float]) -> float:
    """분산비율 = (가중평균 개별변동성) / (포트폴리오 변동성). 1이면 분산효과 없음."""
    if not returns_matrix:
        return 0.0
    M = np.asarray(returns_matrix, dtype=float)
    w = np.asarray(weights, dtype=float)
    vols = np.std(M, axis=1, ddof=1)
    wavg = float(np.dot(w, vols))
    port_vol = float(np.std(np.dot(w, M), ddof=1))
    return wavg / port_vol if port_vol > 1e-12 else 0.0


# ── 비중 최적화 (gs_quant 스타일 3-scheme) ──────────────────────────────────


def _cov(returns_matrix: list[list[float]]) -> np.ndarray:
    return np.cov(np.asarray(returns_matrix, dtype=float), ddof=1)


def equal_weights(n: int) -> list[float]:
    return [1.0 / n] * n if n else []


def inverse_vol_weights(returns_matrix: list[list[float]]) -> list[float]:
    """역변동성 비중 (naive risk parity)."""
    M = np.asarray(returns_matrix, dtype=float)
    vols = np.std(M, axis=1, ddof=1)
    inv = np.where(vols == 0, 0.0, 1.0 / np.where(vols == 0, 1.0, vols))
    s = inv.sum()
    if s == 0:
        return equal_weights(len(vols))
    return [float(x) for x in inv / s]


def min_variance_weights(returns_matrix: list[list[float]], long_only: bool = True) -> list[float]:
    """최소분산 포트폴리오 w = Σ⁻¹1 / (1ᵀΣ⁻¹1)."""
    cov = _cov(returns_matrix)
    n = cov.shape[0] if cov.ndim == 2 else 1
    if n < 2:
        return equal_weights(n)
    try:
        inv = np.linalg.pinv(cov)
    except np.linalg.LinAlgError:
        return equal_weights(n)
    ones = np.ones(n)
    denom = float(ones.dot(inv).dot(ones))
    if abs(denom) < 1e-18:
        return equal_weights(n)
    w = inv.dot(ones) / denom
    if long_only:
        w = np.clip(w, 0.0, None)
        s = w.sum()
        w = w / s if s else np.ones(n) / n
    return [float(x) for x in w]


def risk_parity_weights(returns_matrix: list[list[float]], iters: int = 300) -> list[float]:
    """동일 위험기여(ERC) 비중 — long-only, damped fixed-point."""
    cov = _cov(returns_matrix)
    n = cov.shape[0] if cov.ndim == 2 else 1
    if n < 2:
        return equal_weights(n)
    w = np.ones(n) / n
    for _ in range(iters):
        mrc = cov.dot(w)  # 한계 위험기여
        rc = w * mrc  # 위험기여
        target = rc.mean()
        rc_safe = np.where(rc <= 0, 1e-12, rc)
        w = w * np.sqrt(target / rc_safe)
        w = np.clip(w, 1e-9, None)
        w = w / w.sum()
    return [float(x) for x in w]


def risk_contributions(returns_matrix: list[list[float]], weights: list[float]) -> list[float]:
    """각 자산의 포트폴리오 위험 기여 비율 (합 = 1)."""
    cov = _cov(returns_matrix)
    w = np.asarray(weights, dtype=float)
    port_var = float(w.dot(cov).dot(w))
    if port_var <= 1e-18:
        return equal_weights(len(weights))
    rc = w * cov.dot(w)
    return [float(x) for x in rc / port_var]


# ── 요약 ────────────────────────────────────────────────────────────────────


def portfolio_metrics(returns: list[float], rf_annual: float = 0.0) -> dict:
    """포트폴리오 일간 수익률 → 표준 성과·위험 지표 묶음."""
    return {
        "cumulative_return": round(cumulative_return(returns), 4),
        "cagr": round(cagr(returns), 4),
        "volatility": round(annualized_volatility(returns), 4),
        "sharpe": round(sharpe_ratio(returns, rf_annual), 2),
        "sortino": round(sortino_ratio(returns, rf_annual), 2),
        "max_drawdown": round(max_drawdown(returns), 4),
        "var_95": round(historical_var(returns, 0.95), 4),
        "cvar_95": round(historical_cvar(returns, 0.95), 4),
        "observations": len(returns),
    }


# ── 백테스트 워크플로우 (gs_quant.backtests 스타일) ──────────────────────────

_SCHEMES = ("current", "equal", "inverse_vol", "risk_parity", "min_variance")


def target_weights(
    scheme: str,
    window_matrix: list[list[float]],
    current_weights: list[float],
) -> list[float]:
    """리밸런스 시점의 목표 비중 산출."""
    n = len(window_matrix)
    if scheme == "current":
        return list(current_weights) if current_weights else equal_weights(n)
    if scheme == "equal":
        return equal_weights(n)
    if scheme == "inverse_vol":
        return inverse_vol_weights(window_matrix)
    if scheme == "risk_parity":
        return risk_parity_weights(window_matrix)
    if scheme == "min_variance":
        return min_variance_weights(window_matrix)
    return equal_weights(n)


def backtest_rebalance(
    returns_matrix: list[list[float]],
    scheme: str = "current",
    current_weights: list[float] | None = None,
    lookback: int = 63,
    rebalance_every: int = 21,
    rf_annual: float = 0.0,
) -> dict:
    """
    주기적 리밸런싱 백테스트.

    - `returns_matrix`: 자산×시간 일간 수익률 (정렬됨).
    - 매 `rebalance_every` 거래일마다 직전 `lookback` 구간으로 목표비중 재계산,
      그 사이에는 buy-and-hold (비중이 가격에 따라 드리프트).
    - 반환: equity 곡선, 일간 수익률, 성과지표, 최종 비중.
    """
    if not returns_matrix or not returns_matrix[0]:
        return {"scheme": scheme, "equity": [], "returns": [], "metrics": {}, "final_weights": []}

    M = np.asarray(returns_matrix, dtype=float)  # assets x time
    n_assets, n_days = M.shape
    cur = list(current_weights) if current_weights else equal_weights(n_assets)

    start = min(lookback, max(0, n_days - 1))
    w = np.asarray(
        target_weights(
            scheme, [row[:start].tolist() for row in M] if start > 1 else M.tolist(), cur
        ),
        dtype=float,
    )
    if w.sum() <= 0:
        w = np.ones(n_assets) / n_assets
    w = w / w.sum()

    port_rets: list[float] = []
    for t in range(start, n_days):
        r_t = M[:, t]
        pr = float(np.dot(w, r_t))
        port_rets.append(pr)
        # 비중 드리프트
        grown = w * (1.0 + r_t)
        s = grown.sum()
        w = grown / s if s > 0 else np.ones(n_assets) / n_assets
        # 리밸런스
        if rebalance_every > 0 and (t - start + 1) % rebalance_every == 0:
            lo = max(0, t - lookback + 1)
            window = [row[lo : t + 1].tolist() for row in M]
            tw = np.asarray(target_weights(scheme, window, cur), dtype=float)
            if tw.sum() > 0:
                w = tw / tw.sum()

    return {
        "scheme": scheme,
        "equity": [round(x, 5) for x in equity_curve(port_rets)],
        "returns": port_rets,
        "metrics": portfolio_metrics(port_rets, rf_annual),
        "final_weights": [round(float(x), 4) for x in w],
    }
