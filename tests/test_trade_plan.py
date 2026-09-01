"""매매 타이밍·가격대 — 순수 로직 테스트."""

from app.core.trade_plan import fmt_px, trade_plan, trade_plan_md

_TA = {
    "ok": True,
    "price": 100.0,
    "sma20": 98.0,
    "sma50": 95.0,
    "bb_upper": 106.0,
    "bb_lower": 92.0,
    "atr14": 2.5,
    "hi_20": 104.0,
    "lo_20": 93.0,
    "hi_52w": 120.0,
}
_QUANT = {"ev": 6.0}


def test_returns_none_without_technical():
    assert trade_plan({"ok": False}, _QUANT, "매수") is None


def test_price_level_ordering():
    tp = trade_plan(_TA, _QUANT, "매수")
    assert tp is not None
    assert tp["add_zone"] < tp["entry_low"] <= tp["entry_high"] <= tp["price"] or tp["entry_high"] <= tp["price"] * 1.02
    assert tp["stop"] < tp["entry_low"]
    assert tp["target1"] < tp["target2"]
    assert tp["target1"] >= tp["price"]


def test_stop_below_entry_and_floored():
    tp = trade_plan(_TA, _QUANT, "매수")
    assert tp["stop"] < tp["entry_low"]
    assert tp["stop"] >= _TA["price"] * 0.85 - 1e-9


def test_target1_at_least_3pct():
    flat = dict(_TA, bb_upper=100.5, hi_20=100.2, hi_52w=100.3, sma20=100.0, sma50=100.0)
    tp = trade_plan(flat, {"ev": 0.0}, "매수")
    assert tp["target1"] >= flat["price"] * 1.03 - 1e-9


def test_risk_reward_positive():
    tp = trade_plan(_TA, _QUANT, "매수")
    assert tp["rr"] is None or tp["rr"] > 0


def test_md_verdict_variants():
    tp = trade_plan(_TA, _QUANT, "매수")
    buy = trade_plan_md(tp, "US")
    assert any("매수 희망 구간" in ln for ln in buy)
    hold = trade_plan_md(dict(tp, verdict="보유"), "US")
    assert any("보유 유지" in ln for ln in hold)
    sell = trade_plan_md(dict(tp, verdict="매도"), "US")
    assert any("반등 매도" in ln for ln in sell)


def test_md_empty_when_none():
    assert trade_plan_md(None, "US") == []


def test_fmt_px_by_market():
    assert fmt_px(1234.5, "US") == "$1,234.50"
    assert fmt_px(1234.5, "KR") == "₩1,234"
