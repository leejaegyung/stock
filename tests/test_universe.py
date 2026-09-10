"""스캐너 유니버스 — 순수 데이터 검증."""

from app.core.universe import chunks, exclude_held, universe


def test_universe_shape():
    u = universe()
    assert len(u) > 300
    us = [t for t, m in u if m == "US"]
    kr = [t for t, m in u if m == "KR"]
    assert len(us) > 200 and len(kr) > 30
    assert "AAPL" in us and "NVDA" in us
    assert "005930" in kr  # 삼성전자
    assert len(u) == len(set(u))  # 중복 없음
    for t, m in u:
        assert m in ("US", "KR")
        if m == "US":
            assert t.isascii() and 1 <= len(t) <= 6


def test_exclude_held():
    cands = [("AAPL", "US"), ("MSFT", "US"), ("005930", "KR")]
    held = {("AAPL", "US"), ("005930", "KR")}
    assert exclude_held(cands, held) == [("MSFT", "US")]


def test_chunks():
    assert chunks([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert chunks([], 3) == []
