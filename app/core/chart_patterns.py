"""
Layer 1 — 차트 패턴 인식 (순수 함수, app import 없음).

기술적 분석을 실증적으로 검증한 논문 중 가장 많이 인용된 두 편을 근거로 한다
(둘 다 The Journal of Finance 게재, 기술적 분석 실증연구 중 피인용 최상위권):

  1. Lo, A. W., Mamaysky, H., & Wang, J. (2000).
     "Foundations of Technical Analysis: Computational Algorithms,
     Statistical Inference, and Empirical Implementation."
     The Journal of Finance, 55(4), 1705–1770.
     → 가격을 평활화(smoothing)해 국소 극값(local extrema)을 찾고, 그
       배열 순서·상대 크기로 헤드앤숄더·확산형·삼각형·사각형·더블탑/바텀
       등 전형적 차트 패턴을 자동으로 정의·인식한다 (논문 Table I).

  2. Brock, W., Lakonishok, J., & LeBaron, B. (1992).
     "Simple Technical Trading Rules and the Stochastic Properties
     of Stock Returns." The Journal of Finance, 47(5), 1731–1764.
     → 이동평균 규칙과 함께 N일 신고가/신저가 돌파("trading range
       break", 지지·저항 돌파) 규칙의 예측력을 검증. 여기서는 후자를
       구현했다 (이동평균 규칙은 이미 _score_technical 의 SMA 로직이
       같은 취지를 다룬다).

본 모듈은 두 논문이 정립한 "패턴을 코드로 정의한다"는 방법론을 실무적으로
근사 구현한 것이며, 원 논문의 커널 대역폭 교차검증이나 부트스트랩 유의성
검정까지 재현하지는 않는다. LLM 은 관여하지 않는다(코드가 전부 계산).
"""

from __future__ import annotations

_TOL = 0.02           # "거의 동일" 판정 허용 오차 (2%)
_HEAD_MARGIN = 0.01   # 머리(head)가 어깨(shoulder)보다 이만큼은 더 튀어나와야 유효


def _smooth(vals: list[float], window: int) -> list[float]:
    """중심이동평균 평활화 — Lo·Mamaysky·Wang 의 커널 회귀를 실무적으로 근사."""
    n = len(vals)
    if n == 0:
        return []
    half = max(1, window) // 2
    out = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        seg = vals[lo:hi]
        out.append(sum(seg) / len(seg))
    return out


def local_extrema(smoothed: list[float], min_gap: int = 3) -> list[tuple[int, float, str]]:
    """평활화된 시계열의 국소극값을 (인덱스, 값, 'max'|'min') 리스트로, 시간순 교대 보장."""
    n = len(smoothed)
    if n < 3:
        return []
    raw: list[tuple[int, float, str]] = []
    for i in range(1, n - 1):
        if smoothed[i] > smoothed[i - 1] and smoothed[i] >= smoothed[i + 1]:
            raw.append((i, smoothed[i], "max"))
        elif smoothed[i] < smoothed[i - 1] and smoothed[i] <= smoothed[i + 1]:
            raw.append((i, smoothed[i], "min"))

    out: list[tuple[int, float, str]] = []
    for pt in raw:
        if out and pt[2] == out[-1][2]:
            # 같은 유형이 연달아 나오면 더 극단적인 쪽만 남긴다
            if (pt[2] == "max" and pt[1] > out[-1][1]) or (pt[2] == "min" and pt[1] < out[-1][1]):
                out[-1] = pt
            continue
        if out and pt[0] - out[-1][0] < min_gap:
            continue
        out.append(pt)
    return out


def _approx_eq(a: float, b: float, tol: float = _TOL) -> bool:
    m = (a + b) / 2
    return m != 0 and abs(a - b) / abs(m) <= tol


def _classify_five(pts: list[tuple[int, float, str]]) -> dict | None:
    """마지막 5개 국소극값을 Lo·Mamaysky·Wang(2000) Table I 분류 체계로 판정."""
    if len(pts) < 5:
        return None
    e1, e2, e3, e4, e5 = pts[-5:]
    v1, v2, v3, v4, v5 = e1[1], e2[1], e3[1], e4[1], e5[1]
    t1 = e1[2]

    # 사각형(박스권) — 시작 유형과 무관, 상단·하단이 각각 평행이면 성립
    if _approx_eq(v1, v3) and _approx_eq(v3, v5) and _approx_eq(v2, v4):
        band_ok = min(v1, v3, v5) > max(v2, v4) if t1 == "max" else max(v1, v3, v5) < min(v2, v4)
        if band_ok:
            return {"key": "rectangle", "name": "사각형 박스권 (Rectangle)", "bias": "neutral"}

    if t1 == "max":  # 고점→저점→고점→저점→고점
        if v3 > v1 * (1 + _HEAD_MARGIN) and v3 > v5 * (1 + _HEAD_MARGIN) and _approx_eq(v1, v5) and _approx_eq(v2, v4):
            return {"key": "hs_top", "name": "헤드앤숄더 상단 (Head-and-Shoulders Top)", "bias": "bear"}
        if v1 < v3 < v5 and v2 > v4:
            return {"key": "broadening_top", "name": "확산형 상단 (Broadening Top) — 변동성 확대", "bias": "bear"}
        if v1 > v3 > v5 and v2 < v4:
            return {"key": "triangle_top", "name": "삼각형 수렴 (고점 하락) — 변동성 축소", "bias": "neutral"}
    else:  # 저점→고점→저점→고점→저점
        if v3 < v1 * (1 - _HEAD_MARGIN) and v3 < v5 * (1 - _HEAD_MARGIN) and _approx_eq(v1, v5) and _approx_eq(v2, v4):
            return {"key": "ihs", "name": "역헤드앤숄더 (Inverse Head-and-Shoulders)", "bias": "bull"}
        if v1 > v3 > v5 and v2 < v4:
            return {"key": "broadening_bottom", "name": "확산형 하단 (Broadening Bottom) — 변동성 확대", "bias": "bull"}
        if v1 < v3 < v5 and v2 > v4:
            return {"key": "triangle_bottom", "name": "삼각형 수렴 (저점 상승) — 변동성 축소", "bias": "neutral"}
    return None


def _classify_double(pts: list[tuple[int, float, str]]) -> dict | None:
    """마지막 3개 국소극값으로 더블탑/더블바텀 판정 (같은 유형 극값 2개 + 사이 반전폭)."""
    if len(pts) < 3:
        return None
    e1, e2, e3 = pts[-3:]
    if e1[2] == e3[2] == "max" and e2[2] == "min" and _approx_eq(e1[1], e3[1]):
        if e1[1] and (e1[1] - e2[1]) / e1[1] >= _HEAD_MARGIN * 2:
            return {"key": "double_top", "name": "더블 탑 (Double Top)", "bias": "bear"}
    if e1[2] == e3[2] == "min" and e2[2] == "max" and _approx_eq(e1[1], e3[1]):
        if e1[1] and (e2[1] - e1[1]) / e1[1] >= _HEAD_MARGIN * 2:
            return {"key": "double_bottom", "name": "더블 바텀 (Double Bottom)", "bias": "bull"}
    return None


def detect_pattern(
    closes: list[float], window: int = 60, smooth_window: int = 5, min_gap: int = 3
) -> dict | None:
    """최근 `window`개 종가에서 전형적 차트 패턴을 판정 (없으면 None).

    5-극값 패턴(헤드앤숄더·확산형·삼각형·사각형)을 먼저 시도하고, 없으면
    3-극값 패턴(더블탑/바텀)을 시도한다. 데이터가 20개 미만이면 판정하지 않는다.
    """
    if not closes or len(closes) < 20:
        return None
    seg = closes[-window:] if len(closes) > window else list(closes)
    sm = _smooth(seg, smooth_window)
    pts = local_extrema(sm, min_gap=min_gap)

    hit = _classify_five(pts) or _classify_double(pts)
    if hit:
        hit["extrema_count"] = len(pts)
    return hit


def support_resistance_break(closes: list[float], window: int = 20) -> dict | None:
    """Brock·Lakonishok·LeBaron(1992) 의 trading-range-break 규칙.

    직전 `window`거래일의 고가·저가(종가 기준)를 오늘 종가가 넘어서면
    저항선 상향 돌파(강세) / 지지선 하향 이탈(약세)로 판정."""
    if not closes or len(closes) < window + 1:
        return None
    prior = closes[-window - 1:-1]
    last = closes[-1]
    hi, lo = max(prior), min(prior)
    if last > hi:
        return {"key": "resistance_break", "bias": "bull",
                "name": f"{window}일 신고가 돌파 — 저항선 상향 돌파"}
    if last < lo:
        return {"key": "support_break", "bias": "bear",
                "name": f"{window}일 신저가 이탈 — 지지선 하향 이탈"}
    return None
