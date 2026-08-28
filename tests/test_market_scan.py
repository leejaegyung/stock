"""Layer 1 순수 함수 테스트 — API 키 불필요."""

import numpy as np

from app.core.market_scan import (
    market_regime,
    momentum_profile,
    rank_sectors,
    relative_strength,
    score_label,
    trend_score,
)


def _series(start, drift, days, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    out = [start]
    for _ in range(days):
        out.append(out[-1] * (1 + drift + rng.normal(0, noise)))
    return out


def test_momentum_profile_uptrend():
    mp = momentum_profile(_series(100, 0.002, 260))
    assert mp["ret_3m"] > 0
    assert mp["above_ma50"] is True
    assert mp["above_ma200"] is True
    assert 0 <= mp["rsi"] <= 100


def test_momentum_profile_downtrend():
    mp = momentum_profile(_series(100, -0.002, 260))
    assert mp["ret_3m"] < 0
    assert mp["above_ma200"] is False


def test_momentum_profile_handles_nan_and_short():
    mp = momentum_profile([100, float("nan"), 101, 102])
    assert mp["price"] == 102
    assert mp["above_ma200"] is None  # 데이터 부족


def test_relative_strength_sign():
    strong = _series(100, 0.003, 130, seed=1)
    weak = _series(100, 0.0005, 130, seed=1)
    assert relative_strength(strong, weak) > 0
    assert relative_strength(weak, strong) < 0
    assert relative_strength([100], [100]) == 0.0  # 데이터 부족 → 0


def test_trend_score_bounds_and_ordering():
    up = momentum_profile(_series(100, 0.0025, 260, seed=2))
    down = momentum_profile(_series(100, -0.0025, 260, seed=2))
    su, sd = trend_score(up, 5), trend_score(down, -5)
    assert 0 <= sd <= su <= 100
    assert su > 60 and sd < 45


def test_trend_score_overbought_penalty():
    mp = momentum_profile(_series(100, 0.02, 40, seed=3))  # 급등 → RSI 과열
    if mp["rsi"] > 78:
        base = dict(mp, rsi=60)
        assert trend_score(mp, 0) < trend_score(base, 0)


def test_score_label_thresholds():
    assert score_label(75)[0] == "유망"
    assert score_label(60)[0] == "관심"
    assert score_label(45)[0] == "중립"
    assert score_label(20)[0] == "주의"


def test_rank_sectors_orders_desc():
    r = rank_sectors({"A": 40, "B": 80, "C": 60})
    assert [k for k, _ in r] == ["B", "C", "A"]


def test_market_regime_bull():
    spx = {"above_ma200": True}
    reg = market_regime(cape=28, vix=13, tnx=3.5, spx_mp=spx, breadth_pos_ratio=0.8)
    assert reg["risk_score"] == 0
    assert "강세" in reg["label"]
    assert reg["tone"] == "good"


def test_market_regime_defensive():
    spx = {"above_ma200": False}
    reg = market_regime(cape=40, vix=32, tnx=5.0, spx_mp=spx, breadth_pos_ratio=0.2)
    assert reg["risk_score"] >= 4
    assert reg["tone"] == "bad"
    assert reg["reasons"]


def test_market_regime_neutral_midrange():
    # VIX 22(+1) + CAPE 36(+1) → risk 2 → 경계
    reg = market_regime(cape=36, vix=22, tnx=4.0, spx_mp={"above_ma200": True}, breadth_pos_ratio=0.5)
    assert reg["label"] in ("중립", "경계")
    assert 1 <= reg["risk_score"] <= 3


def test_market_regime_all_benign_is_bull():
    reg = market_regime(cape=28, vix=13, tnx=3.5, spx_mp={"above_ma200": True}, breadth_pos_ratio=0.6)
    assert reg["risk_score"] == 0 and reg["tone"] == "good"
