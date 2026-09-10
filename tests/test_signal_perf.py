"""시그널 성과 집계 — 순수 로직 테스트."""

from app.core.signal_perf import is_hit, summarize


def test_is_hit_directions():
    assert is_hit("매수", 5.0) is True
    assert is_hit("매수", 1.0) is False
    assert is_hit("추가매수", 3.0) is True
    assert is_hit("매도", -4.0) is True
    assert is_hit("매도", 1.0) is False
    assert is_hit("보유", 0.5) is True
    assert is_hit("보유", 9.0) is False
    assert is_hit("알수없음", 5.0) is None


def test_is_hit_band():
    assert is_hit("매수", 2.5, band=2.0) is True
    assert is_hit("매수", 2.5, band=3.0) is False


def test_summarize_basic():
    sig = [
        {"verdict": "매수", "confidence": "상", "fwd_return_pct": 8.0},
        {"verdict": "매수", "confidence": "중", "fwd_return_pct": -3.0},
        {"verdict": "매도", "confidence": "상", "fwd_return_pct": -6.0},
        {"verdict": "보유", "confidence": "중", "fwd_return_pct": 0.4},
        {"verdict": "알수없음", "confidence": "하", "fwd_return_pct": 2.0},  # 제외
        {"verdict": "매수", "confidence": None, "fwd_return_pct": None},     # 제외
    ]
    out = summarize(sig)
    assert out["overall"]["count"] == 4
    assert out["overall"]["hits"] == 3            # 매수(8), 매도(-6), 보유(0.4)
    assert out["by_verdict"]["매수"]["count"] == 2
    assert out["by_verdict"]["매수"]["hit_rate"] == 0.5
    assert out["by_confidence"]["상"]["count"] == 2
    assert out["by_confidence"]["상"]["hit_rate"] == 1.0
    assert out["directional"]["count"] == 3       # 보유 제외


def test_summarize_empty():
    out = summarize([])
    assert out["overall"]["count"] == 0
    assert out["overall"]["hit_rate"] is None
    assert out["by_verdict"]["매수"]["count"] == 0
