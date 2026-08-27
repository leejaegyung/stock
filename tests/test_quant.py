"""Layer 1 순수 함수 테스트 — API 키 불필요, 항상 실행 가능."""

import math

import pytest

from app.core.quant import (
    annualized_volatility,
    backtest_rebalance,
    beta,
    cagr,
    correlation,
    cumulative_return,
    daily_returns,
    diversification_ratio,
    effective_holdings,
    equal_weights,
    herfindahl_index,
    historical_cvar,
    historical_var,
    inverse_vol_weights,
    max_drawdown,
    min_variance_weights,
    portfolio_metrics,
    portfolio_returns,
    risk_contributions,
    risk_parity_weights,
    sharpe_ratio,
    sortino_ratio,
)


def test_daily_returns_basic():
    assert daily_returns([100, 110, 99]) == pytest.approx([0.1, -0.1])
    assert daily_returns([]) == []
    assert daily_returns([100]) == []


def test_cumulative_return_geometric():
    assert cumulative_return([0.1, -0.1]) == pytest.approx(1.1 * 0.9 - 1.0)  # -0.01
    assert cumulative_return([0.0, 0.0]) == 0.0


def test_cagr_annualizes():
    # 63거래일 동안 +5% → CAGR ≈ (1.05)^(252/63) - 1
    r = [0.05 / 63] * 63
    assert cagr(r) == pytest.approx((1 + cumulative_return(r)) ** (252 / 63) - 1, rel=1e-6)


def test_volatility_zero_for_constant():
    assert annualized_volatility([0.01] * 50) == 0.0
    assert annualized_volatility([0.01]) == 0.0


def test_volatility_annualization_factor():
    daily = [0.01, -0.01] * 60
    import numpy as np

    assert annualized_volatility(daily) == pytest.approx(np.std(daily, ddof=1) * math.sqrt(252))


def test_sharpe_sign_and_zero_rf():
    up = [0.002, 0.001] * 60  # 평균 > 0, 변동성 존재
    assert sharpe_ratio(up, rf_annual=0.0) > 0
    down = [-0.002, -0.001] * 60
    assert sharpe_ratio(down, rf_annual=0.0) < 0
    assert sharpe_ratio([0.01] * 100) == 0.0  # 변동성 0 → 0 (정의 불가)


def test_sortino_only_penalizes_downside():
    mixed = [0.02, -0.01, 0.03, -0.005, 0.01] * 20
    assert sortino_ratio(mixed) > sharpe_ratio(mixed)  # 상방 변동은 분모에서 제외


def test_max_drawdown_is_negative_ratio():
    # +10%, -20%, +5%  → 낙폭은 최소 -20%
    mdd = max_drawdown([0.10, -0.20, 0.05])
    assert -0.21 < mdd <= -0.19
    assert max_drawdown([0.01, 0.01, 0.01]) == 0.0


def test_var_cvar_ordering():
    rets = [(-1) ** i * 0.01 * (i % 5 + 1) for i in range(200)]
    v = historical_var(rets, 0.95)
    c = historical_cvar(rets, 0.95)
    assert v >= 0 and c >= 0
    assert c >= v - 1e-9  # CVaR(꼬리 평균 손실) ≥ VaR


def test_beta_self_is_one():
    m = [0.01, -0.02, 0.015, -0.005, 0.02] * 20
    assert beta(m, m) == pytest.approx(1.0, rel=1e-6)


def test_beta_double_is_two():
    m = [0.01, -0.02, 0.015, -0.005, 0.02] * 20
    a = [x * 2 for x in m]
    assert beta(a, m) == pytest.approx(2.0, rel=1e-6)


def test_correlation_bounds():
    a = [0.01, -0.02, 0.03, -0.01, 0.02] * 20
    assert correlation(a, a) == pytest.approx(1.0, rel=1e-6)
    assert correlation(a, [-x for x in a]) == pytest.approx(-1.0, rel=1e-6)


def test_herfindahl_and_effective_holdings():
    assert herfindahl_index([0.5, 0.5]) == pytest.approx(0.5)
    assert effective_holdings([0.25, 0.25, 0.25, 0.25]) == pytest.approx(4.0)
    assert effective_holdings([1.0]) == pytest.approx(1.0)


def test_weight_schemes_sum_to_one():
    # 3자산: 저변동, 중변동, 고변동
    import numpy as np

    rng = np.random.default_rng(42)
    M = [
        list(rng.normal(0, 0.005, 250)),
        list(rng.normal(0, 0.010, 250)),
        list(rng.normal(0, 0.020, 250)),
    ]
    for w in (
        equal_weights(3),
        inverse_vol_weights(M),
        risk_parity_weights(M),
        min_variance_weights(M),
    ):
        assert len(w) == 3
        assert sum(w) == pytest.approx(1.0, rel=1e-6)
        assert all(x >= -1e-9 for x in w)
    # 역변동성: 고변동 자산 비중이 저변동보다 작아야 한다
    iv = inverse_vol_weights(M)
    assert iv[0] > iv[2]


def test_risk_parity_equalizes_contributions():
    import numpy as np

    rng = np.random.default_rng(7)
    M = [
        list(rng.normal(0, 0.006, 300)),
        list(rng.normal(0, 0.012, 300)),
        list(rng.normal(0, 0.018, 300)),
    ]
    w = risk_parity_weights(M)
    rc = risk_contributions(M, w)
    assert max(rc) - min(rc) < 0.05  # 위험기여가 거의 균등


def test_diversification_ratio_ge_one():
    import numpy as np

    rng = np.random.default_rng(1)
    M = [list(rng.normal(0, 0.01, 200)), list(rng.normal(0, 0.01, 200))]
    dr = diversification_ratio(M, [0.5, 0.5])
    assert dr >= 1.0 - 1e-6  # 상관 < 1 이면 분산비율 > 1


def test_portfolio_returns_weighting():
    M = [[0.10, 0.10], [0.00, 0.00]]
    assert portfolio_returns(M, [0.5, 0.5]) == pytest.approx([0.05, 0.05])


def test_portfolio_metrics_keys():
    m = portfolio_metrics([0.001, -0.002, 0.003] * 40)
    for k in (
        "cumulative_return",
        "cagr",
        "volatility",
        "sharpe",
        "sortino",
        "max_drawdown",
        "var_95",
        "cvar_95",
        "observations",
    ):
        assert k in m


def test_backtest_rebalance_runs():
    import numpy as np

    rng = np.random.default_rng(3)
    M = [list(rng.normal(0.0003, 0.01, 300)) for _ in range(3)]
    res = backtest_rebalance(M, scheme="risk_parity", lookback=60, rebalance_every=20)
    assert len(res["equity"]) == len(res["returns"]) + 1
    assert res["metrics"]["observations"] == len(res["returns"])
    assert sum(res["final_weights"]) == pytest.approx(1.0, abs=1e-3)


def test_backtest_empty_input():
    res = backtest_rebalance([], scheme="equal")
    assert res["equity"] == [] and res["metrics"] == {}
