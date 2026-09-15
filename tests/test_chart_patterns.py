"""차트 패턴 인식 — 순수 로직 테스트 (Lo·Mamaysky·Wang 2000 / Brock·Lakonishok·LeBaron 1992 근사)."""

from app.core.chart_patterns import (
    detect_pattern,
    local_extrema,
    support_resistance_break,
)


def _wave(points: list[float], seg_len: int = 8) -> list[float]:
    """제어점 사이를 선형보간해 매끈한 가격 시계열을 만든다."""
    out: list[float] = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        for k in range(seg_len):
            out.append(a + (b - a) * k / seg_len)
    out.append(points[-1])
    return out


def test_local_extrema_alternates():
    series = _wave([100, 110, 95, 115, 90])
    ext = local_extrema(series, min_gap=2)
    types = [e[2] for e in ext]
    for i in range(len(types) - 1):
        assert types[i] != types[i + 1]


def test_head_and_shoulders_top_detected():
    # 어깨(100) - 목선(90) - 머리(115) - 목선(90) - 어깨(100)
    series = _wave([70, 100, 90, 115, 90, 100, 70])
    hit = detect_pattern(series, window=len(series))
    assert hit is not None
    assert hit["key"] == "hs_top"
    assert hit["bias"] == "bear"


def test_inverse_head_and_shoulders_detected():
    series = _wave([130, 100, 110, 85, 110, 100, 130])
    hit = detect_pattern(series, window=len(series))
    assert hit is not None
    assert hit["key"] == "ihs"
    assert hit["bias"] == "bull"


def test_broadening_top_detected():
    # 고점 점점 상승(100<108<118), 저점 점점 하락(95>88>80) — 변동성 확대
    series = _wave([70, 100, 95, 108, 88, 118, 80])
    hit = detect_pattern(series, window=len(series))
    assert hit is not None
    assert hit["key"] == "broadening_top"
    assert hit["bias"] == "bear"


def test_triangle_converging_detected():
    # 고점 점점 하락(118>108>100), 저점 점점 상승(80<88<95) — 변동성 축소
    series = _wave([70, 118, 80, 108, 88, 100, 95])
    hit = detect_pattern(series, window=len(series))
    assert hit is not None
    assert hit["key"] == "triangle_top"
    assert hit["bias"] == "neutral"


def test_rectangle_detected():
    series = _wave([70, 100, 90, 100, 90, 100, 90])
    hit = detect_pattern(series, window=len(series))
    assert hit is not None
    assert hit["key"] == "rectangle"
    assert hit["bias"] == "neutral"


def test_double_top_detected():
    # 두 번째 고점 뒤로 살짝 눌리는 구간까지 있어야 그 고점이 국소극값으로 확정된다
    series = _wave([65, 100, 82, 100, 90])
    hit = detect_pattern(series, window=len(series))
    assert hit is not None
    assert hit["key"] == "double_top"
    assert hit["bias"] == "bear"


def test_no_pattern_on_monotonic_series():
    series = [float(x) for x in range(50, 100)]
    assert detect_pattern(series) is None


def test_detect_pattern_requires_min_length():
    assert detect_pattern([1.0, 2.0, 3.0]) is None
    assert detect_pattern([]) is None


def test_support_resistance_break_bull():
    prior = [100.0] * 20
    hit = support_resistance_break(prior + [105.0], window=20)
    assert hit is not None
    assert hit["key"] == "resistance_break"
    assert hit["bias"] == "bull"


def test_support_resistance_break_bear():
    prior = [100.0] * 20
    hit = support_resistance_break(prior + [95.0], window=20)
    assert hit is not None
    assert hit["key"] == "support_break"
    assert hit["bias"] == "bear"


def test_support_resistance_no_break_inside_range():
    prior = [95.0 + (i % 3) for i in range(20)]
    hit = support_resistance_break(prior + [96.0], window=20)
    assert hit is None


def test_support_resistance_break_insufficient_data():
    assert support_resistance_break([1.0, 2.0, 3.0], window=20) is None
