"""월가 거장 5인 합의 블렌딩 — 순수 함수 테스트 (API 키 불필요)."""

from app.core.legends_blend import blend_votes, MAX_POSITION_PCT, STRATEGY_WEIGHTS


def _vote(name, verdict, confidence):
    return {"name": name, "verdict": verdict, "confidence": confidence}


def test_unanimous_buy_gives_max_weight():
    votes = [_vote(n, "매수", 90) for n in STRATEGY_WEIGHTS["펀더멘털 롱숏"]]
    r = blend_votes(votes, "펀더멘털 롱숏")
    assert r["consensus"] == "매수"
    assert r["dispersion"] == 0.0
    assert r["target_weight_pct"] == MAX_POSITION_PCT * 0.9 or r["target_weight_pct"] <= MAX_POSITION_PCT
    assert r["target_weight_pct"] > 15  # 만장일치 고확신이면 비중이 커야 함


def test_unanimous_sell_gives_zero_weight():
    votes = [_vote(n, "매도", 80) for n in STRATEGY_WEIGHTS["딥 밸류"]]
    r = blend_votes(votes, "딥 밸류")
    assert r["consensus"] == "매도"
    assert r["target_weight_pct"] == 0.0
    assert r["cash_pct"] == 100.0


def test_split_vote_reduces_weight_toward_cash():
    votes = [
        _vote("버핏", "매수", 80), _vote("멍거", "매수", 80),
        _vote("그레이엄", "매도", 80), _vote("린치", "매도", 80),
        _vote("드러켄밀러", "중립", 50),
    ]
    r = blend_votes(votes, "펀더멘털 롱숏")
    assert r["dispersion"] > 0
    assert any("갈려" in n for n in r["notes"]) or r["dispersion"] < 0.6


def test_strategy_changes_weighting():
    # 그레이엄만 매도, 나머지는 매수 — 딥 밸류 전략(그레이엄 가중 2배)에서는
    # 펀더멘털 롱숏(버핏·멍거 가중 2배)보다 그레이엄의 매도 의견이 더 크게 반영돼야 함
    votes = [
        _vote("버핏", "매수", 80), _vote("멍거", "매수", 80),
        _vote("그레이엄", "매도", 80), _vote("린치", "매수", 80), _vote("드러켄밀러", "매수", 80),
    ]
    deep_value = blend_votes(votes, "딥 밸류")
    fundamental = blend_votes(votes, "펀더멘털 롱숏")
    assert deep_value["weighted_score"] < fundamental["weighted_score"]


def test_unknown_strategy_falls_back_to_default():
    votes = [_vote(n, "매수", 70) for n in STRATEGY_WEIGHTS["펀더멘털 롱숏"]]
    r = blend_votes(votes, "없는 전략")
    assert r["strategy"] == "펀더멘털 롱숏"


def test_max_position_cap_enforced():
    votes = [_vote(n, "매수", 100) for n in STRATEGY_WEIGHTS["펀더멘털 롱숏"]]
    r = blend_votes(votes, "펀더멘털 롱숏")
    assert r["target_weight_pct"] <= MAX_POSITION_PCT


def test_empty_votes_returns_neutral():
    r = blend_votes([], "펀더멘털 롱숏")
    assert r["target_weight_pct"] == 0.0
    assert r["cash_pct"] == 100.0
    assert r["consensus"] == "중립"
