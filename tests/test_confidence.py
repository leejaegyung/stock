"""Layer 1 순수 함수 테스트 — API 키 불필요."""

from app.core.confidence import analysis_confidence, improvement_hints

_FULL_COV = {
    "technical_ok": True, "has_ma200": True,
    "has_pe": True, "has_roe": True, "has_cashflow": True,
}
_NO_COV = {k: False for k in _FULL_COV}


def _c(tech, fund, macro, news, cov=_FULL_COV, bulls=3, bears=1, nc=5):
    return analysis_confidence(
        {"technical": tech, "fundamental": fund, "macro": macro, "news": news},
        cov, bulls, bears, nc,
    )


def test_all_aligned_bullish_high_confidence():
    r = _c(26, 34, 16, 8, bulls=5, bears=1, nc=6)
    assert r["grade"] == "상"
    assert r["score"] >= 68
    assert any("일치" in x for x in r["reasons"])


def test_conflicting_signals_lower_confidence():
    aligned = _c(26, 34, 16, 8)
    conflict = _c(27, 8, 15, 2, bulls=3, bears=3)   # 기술 강세 / 펀더 약세, 신호 팽팽
    assert conflict["score"] < aligned["score"]
    assert any("상충" in x or "팽팽" in x for x in conflict["reasons"])


def test_missing_data_penalized():
    full = _c(24, 30, 12, 6)
    sparse = _c(24, 30, 12, 6, cov=_NO_COV, nc=0)
    assert sparse["score"] < full["score"]
    assert any("누락" in x for x in sparse["reasons"])
    assert sparse["grade"] in ("중", "하")


def test_threshold_proximity_penalty_and_reason():
    # 종합 72 = 매수 경계선
    r = _c(22, 30, 12, 8)
    assert sum([22, 30, 12, 8]) == 72
    assert any("경계선" in x for x in r["reasons"])


def test_no_news_flagged():
    r = _c(24, 30, 12, 0, nc=0)
    assert any("뉴스 없음" in x for x in r["reasons"])


def test_score_bounds_and_factor_sum():
    r = _c(30, 40, 20, 10, bulls=8, bears=0, nc=10)
    assert 0 <= r["score"] <= 100
    assert sum(f["score"] for f in r["factors"]) == r["score"] or r["score"] == 100
    assert [f["max"] for f in r["factors"]] == [22, 28, 15, 25, 10]
    assert sum(f["max"] for f in r["factors"]) == 100


def test_grade_thresholds():
    assert _c(30, 40, 20, 10, bulls=9, bears=0, nc=10)["grade"] == "상"
    weak = _c(15, 18, 9, 4, cov=_NO_COV, bulls=1, bears=1, nc=0)
    assert weak["grade"] == "하"


def test_improvement_hints_kr_dart():
    conf = _c(20, 8, 10, 1, cov={**_NO_COV, "technical_ok": True}, nc=1)
    hints = improvement_hints(conf, {**_NO_COV, "technical_ok": True}, 1, "KR")
    assert any("DART" in h for h in hints)
    assert any("뉴스" in h for h in hints)
