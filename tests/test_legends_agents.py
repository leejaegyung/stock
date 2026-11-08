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


def test_graham_uses_pbr_as_safety_margin_signal():
    cheap = LegendAnalyst("그레이엄").run("A", {"per": 18, "pbr": 1.0})
    expensive = LegendAnalyst("그레이엄").run("B", {"per": 18, "pbr": 6.0})
    assert cheap["confidence"] >= 0 and expensive["confidence"] >= 0
    # 같은 PER이라도 PBR이 낮은 쪽이 더 매수 쪽으로 기울어야 함
    order = {"매도": -1, "중립": 0, "매수": 1}
    assert order[cheap["verdict"]] >= order[expensive["verdict"]]


def test_graham_uses_peer_relative_valuation():
    r = LegendAnalyst("그레이엄").run("A", {"per": 12, "peer_avg_per": 25})
    assert any("업종 평균" in reason for reason in r["reasons"])


def test_lynch_classifies_company_type_and_adjusts_peg_bar():
    stalwart = LegendAnalyst("린치").run("A", {
        "peg_ratio": 1.8, "earnings_growth_pct": 12, "revenue_growth_pct": 8, "market_cap": 200_000_000_000,
    })
    assert any("대형우량주" in reason for reason in stalwart["reasons"])

    turnaround = LegendAnalyst("린치").run("B", {
        "earnings_growth_pct": -10, "revenue_growth_pct": 15,
    })
    assert any("회생주" in reason for reason in turnaround["reasons"])


def test_druckenmiller_uses_cape_and_yield():
    hawkish = LegendAnalyst("드러켄밀러").run("A", {"macro_cape": 35, "macro_10y_yield": 5.0})
    dovish = LegendAnalyst("드러켄밀러").run("A", {"macro_cape": 15, "macro_10y_yield": 2.0})
    order = {"매도": -1, "중립": 0, "매수": 1}
    assert order[dovish["verdict"]] >= order[hawkish["verdict"]]


def test_confidence_reflects_signal_agreement_not_just_count():
    # 신호 5개가 전부 매수 방향 vs 신호 5개가 팽팽하게 갈린 경우 — 전자가 확신도 더 높아야 함
    unanimous = LegendAnalyst("버핏").run("A", {"roe_pct": 30, "profit_margin_pct": 25, "operating_margin_pct": 30, "debt_to_equity_pct": 20, "per": 10})
    split = LegendAnalyst("버핏").run("B", {"roe_pct": 30, "profit_margin_pct": 3, "operating_margin_pct": 5, "debt_to_equity_pct": 200, "per": 40})
    assert unanimous["confidence"] > split["confidence"]


def test_missing_data_returns_safe_neutral_result():
    for persona in LEGEND_PERSONAS:
        r = LegendAnalyst(persona).run("TEST", {})
        assert r["verdict"] in ("매수", "중립", "매도")
        assert 0 <= r["confidence"] <= 100
        assert "key_risk" in r and r["reasons"]
