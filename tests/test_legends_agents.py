"""월가 거장 5인 규칙 기반 분석기 테스트 — LLM 호출 없음, API 키 불필요."""

from app.core.agents.legends import LEGEND_PERSONAS, LegendAnalyst


def test_all_personas_have_valid_persona_names():
    for persona in LEGEND_PERSONAS:
        a = LegendAnalyst(persona)
        assert a.persona == persona


def test_unknown_persona_raises():
    try:
        LegendAnalyst("존재하지않는사람")
        assert False, "should have raised"
    except ValueError:
        pass


def test_high_quality_low_debt_tilts_buffett_toward_buy():
    data = {"roe_pct": 30, "profit_margin_pct": 25, "debt_to_equity_pct": 30, "per": 18}
    r = LegendAnalyst("버핏").run("TEST", data)
    assert r["verdict"] == "매수"
    assert r["name"] == "버핏"
    assert 0 <= r["confidence"] <= 100
    assert r["reasons"]


def test_expensive_and_leveraged_tilts_graham_toward_sell():
    data = {"per": 45, "debt_to_equity_pct": 180, "dividend_yield_pct": 0, "price": 100, "week52_high": 102, "week52_low": 60}
    r = LegendAnalyst("그레이엄").run("TEST", data)
    assert r["verdict"] == "매도"


def test_lynch_penalizes_high_peg():
    data = {"peg_ratio": 5.0, "earnings_growth_pct": 3, "revenue_growth_pct": 10}
    r = LegendAnalyst("린치").run("TEST", data)
    assert r["verdict"] in ("매도", "중립")


def test_druckenmiller_reads_uptrend_as_buy():
    data = {"price": 110, "sma20": 105, "sma50": 100, "sma200": 90, "macd_hist": 0.5, "rsi": 55, "return_3m_pct": 15}
    r = LegendAnalyst("드러켄밀러").run("TEST", data)
    assert r["verdict"] == "매수"


def test_missing_data_returns_safe_neutral_result():
    for persona in LEGEND_PERSONAS:
        r = LegendAnalyst(persona).run("TEST", {})
        assert r["verdict"] in ("매수", "중립", "매도")
        assert 0 <= r["confidence"] <= 100
        assert "key_risk" in r and r["reasons"]
