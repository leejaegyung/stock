"""모의투자 의사결정 로직 — 순수 함수 테스트 (실제 자금 없음)."""

from app.core.paper_trading import (
    compute_pnl,
    position_size,
    should_exit,
    summarize_trades,
)


def test_position_size_basic():
    r = position_size(equity_krw=10_000_000, kelly_pct=10.0, price_krw=100_000)
    assert r["qty"] == 10          # 10% * 1000만 = 100만원 예산 / 10만원 = 10주
    assert r["weight_pct"] == 10.0


def test_position_size_capped_at_max():
    r = position_size(equity_krw=10_000_000, kelly_pct=90.0, price_krw=100_000)
    assert r["weight_pct"] == 25.0   # MAX_POSITION_PCT 캡
    assert r["qty"] == 25


def test_position_size_too_small_skips():
    r = position_size(equity_krw=10_000_000, kelly_pct=0.01, price_krw=100_000)
    assert r["qty"] == 0


def test_position_size_zero_equity_or_price():
    assert position_size(0, 10, 1000)["qty"] == 0
    assert position_size(1_000_000, 10, 0)["qty"] == 0


def test_should_exit_stop_priority_over_target():
    # 손절가와 목표가를 동시에 만족하는 극단적 상황이어도 손절이 우선
    hit = should_exit(entry_price=100, target1=110, target2=120, stop=95, current_price=95)
    assert hit["reason"] == "stop"


def test_should_exit_target2_before_target1():
    hit = should_exit(entry_price=100, target1=110, target2=120, stop=90, current_price=125)
    assert hit["reason"] == "target2"


def test_should_exit_target1():
    hit = should_exit(entry_price=100, target1=110, target2=120, stop=90, current_price=112)
    assert hit["reason"] == "target1"


def test_should_exit_verdict_sell():
    hit = should_exit(entry_price=100, target1=110, target2=120, stop=90, current_price=105, verdict="매도")
    assert hit["reason"] == "verdict_sell"


def test_should_exit_timeout():
    hit = should_exit(entry_price=100, target1=110, target2=120, stop=90, current_price=101,
                       days_held=100, max_hold_days=90)
    assert hit["reason"] == "timeout"


def test_should_exit_none_when_holding():
    assert should_exit(entry_price=100, target1=110, target2=120, stop=90, current_price=102) is None


def test_compute_pnl():
    r = compute_pnl(entry_price_krw=100_000, exit_price_krw=110_000, qty=10)
    assert r["pnl_krw"] == 100_000
    assert r["pnl_pct"] == 10.0

    loss = compute_pnl(entry_price_krw=100_000, exit_price_krw=90_000, qty=5)
    assert loss["pnl_krw"] == -50_000
    assert loss["pnl_pct"] == -10.0


def test_summarize_trades():
    trades = [
        {"pnl_pct": 5.0, "pnl_krw": 50_000},
        {"pnl_pct": -3.0, "pnl_krw": -30_000},
        {"pnl_pct": 8.0, "pnl_krw": 80_000},
        {"pnl_pct": None},  # 아직 청산 안 된 거래는 통계에서 제외
    ]
    s = summarize_trades(trades)
    assert s["count"] == 3
    assert s["wins"] == 2
    assert abs(s["win_rate"] - 2 / 3) < 1e-3
    assert s["total_pnl_krw"] == 100_000


def test_summarize_trades_empty():
    s = summarize_trades([])
    assert s["count"] == 0
    assert s["win_rate"] is None
