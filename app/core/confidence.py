"""
Layer 1 — 분석 확신도 모델 (순수 함수, app import 없음).

기존 확신도는 종합점수 구간만으로 상/중/하를 매겼다(71점=중, 72점=상).
이 모델은 "이 결론을 얼마나 믿을 수 있나"를 5개 요소로 분해해 0~100 점수와
등급, 그리고 사람이 읽을 수 있는 근거를 돌려준다.

요소(가중치)
  1. 데이터 커버리지 (22) — 기술·재무·뉴스 데이터가 얼마나 확보됐나
  2. 신호 일치도     (28) — 기술/펀더멘털/거시/뉴스 4개 분야가 같은 방향인가
  3. 신호 우위       (15) — 강세/약세 신호 중 한쪽이 뚜렷이 많은가
  4. 점수 확신       (25) — 종합점수가 중립(50)에서 떨어져 있고, 경계선이 아닌가
  5. 뉴스 근거       (10) — 심리 판단을 뒷받침할 뉴스가 충분한가
"""

from __future__ import annotations

_THRESHOLDS = (72, 60, 45, 30)  # _verdict() 등급 경계


def _dir_signals(dim_scores: dict) -> list[float]:
    """분야 점수 → 방향 신호(대략 -1~+1, 0 = 중립)."""
    return [
        (dim_scores.get("technical", 0) - 15) / 15,
        (dim_scores.get("fundamental", 0) - 20) / 20,
        (dim_scores.get("macro", 0) - 10) / 10,
        (dim_scores.get("news", 0) - 5) / 5,
    ]


def analysis_confidence(
    dim_scores: dict,
    coverage: dict,
    bull_count: int,
    bear_count: int,
    news_count: int,
) -> dict:
    """
    :param dim_scores: {"technical","fundamental","macro","news"} 각 원점수
    :param coverage: {"technical_ok","has_ma200","has_pe","has_roe","has_cashflow"} bool
    :returns: {"score":int, "grade":"상|중|하", "factors":[...], "reasons":[...]}
    """
    total = sum(dim_scores.get(k, 0) for k in ("technical", "fundamental", "macro", "news"))
    sigs = _dir_signals(dim_scores)

    # 1. 데이터 커버리지 (0~22)
    cov = 0
    cov += 8 if coverage.get("technical_ok") else 0
    cov += 2 if coverage.get("has_ma200") else 0
    cov += 4 if coverage.get("has_pe") else 0
    cov += 3 if coverage.get("has_roe") else 0
    cov += 3 if coverage.get("has_cashflow") else 0
    cov += 2 if news_count >= 1 else 0
    cov = min(22, cov)

    # 2. 신호 일치도 (0~28)
    overall = sum(sigs)
    agree = sum(abs(s) for s in sigs if s * overall > 0)
    disagree = sum(abs(s) for s in sigs if s * overall < 0)
    mag = agree + disagree
    agr = 7 if mag < 1e-6 else round(agree / mag * 28)

    # 3. 신호 우위 (0~15)
    tot_sig = bull_count + bear_count
    if tot_sig == 0:
        dom_ratio, dom = 0.0, 5
    else:
        dom_ratio = abs(bull_count - bear_count) / tot_sig
        dom = round(5 + dom_ratio * 10)

    # 4. 점수 확신 (0~25) — 종합점수가 중립(50)에서 멀수록, 등급 경계선이 아닐수록 높다
    dev = abs(total - 50) / 50
    conv = dev * 25
    near_thr = next((t for t in _THRESHOLDS if abs(total - t) <= 3), None)
    if near_thr is not None:
        conv -= 6
    conv = round(max(0.0, conv))

    # 5. 뉴스 근거 (0~10)
    news_f = 10 if news_count >= 5 else 7 if news_count >= 3 else 4 if news_count >= 1 else 0

    score = max(0, min(100, cov + agr + dom + conv + news_f))
    grade = "상" if score >= 68 else "중" if score >= 45 else "하"

    # ── 근거 문장 ──
    reasons: list[str] = []

    missing = []
    if not coverage.get("technical_ok"):
        missing.append("가격 데이터")
    if not coverage.get("has_pe"):
        missing.append("PER")
    if not coverage.get("has_roe"):
        missing.append("ROE")
    if not coverage.get("has_cashflow"):
        missing.append("현금흐름")
    if missing:
        reasons.append(f"데이터 일부 누락: {', '.join(missing)} — 판단 근거 제한")
    elif cov >= 20:
        reasons.append("기술·재무 데이터 완비")

    n_pos = sum(1 for s in sigs if s > 0.05)
    n_neg = sum(1 for s in sigs if s < -0.05)
    if n_pos == 4 or n_neg == 4:
        reasons.append(f"4개 분야 신호가 모두 {'긍정' if n_pos == 4 else '부정'} 방향으로 일치")
    elif n_pos >= 3 or n_neg >= 3:
        reasons.append(f"4개 중 {max(n_pos, n_neg)}개 분야가 같은 방향")
    elif n_pos >= 1 and n_neg >= 1:
        reasons.append("분야별 신호가 상충 — 방향성이 불명확")

    if tot_sig > 0:
        tail = " — 뚜렷한 우위" if dom_ratio >= 0.5 else " — 팽팽함"
        reasons.append(f"강세 신호 {bull_count} vs 약세 {bear_count}{tail}")

    if news_count == 0:
        reasons.append("관련 뉴스 없음 — 심리 판단 근거 부족")
    elif news_count < 3:
        reasons.append(f"관련 뉴스 {news_count}건 — 심리 판단 근거가 얕음")

    if near_thr is not None:
        reasons.append(f"종합점수 {total}점, 결론 경계선({near_thr}) 근처 — 소폭 변동에 등급이 바뀔 수 있음")

    return {
        "score": int(score),
        "grade": grade,
        "factors": [
            {"name": "데이터 커버리지", "score": int(cov), "max": 22},
            {"name": "신호 일치도", "score": int(agr), "max": 28},
            {"name": "신호 우위", "score": int(dom), "max": 15},
            {"name": "점수 확신", "score": int(conv), "max": 25},
            {"name": "뉴스 근거", "score": int(news_f), "max": 10},
        ],
        "reasons": reasons,
    }


def improvement_hints(conf: dict, coverage: dict, news_count: int, market: str) -> list[str]:
    """확신도를 높이려면 무엇을 하면 되는지 실행 가능한 제안."""
    hints: list[str] = []
    if news_count < 3:
        hints.append("뉴스가 쌓인 뒤 재분석하면 심리 신호의 신뢰도가 올라갑니다.")
    if market.upper() == "KR" and not (coverage.get("has_pe") and coverage.get("has_roe")):
        hints.append("DART API 키(.env)를 설정하면 국내 재무 데이터가 채워져 확신도가 개선됩니다.")
    if not coverage.get("has_cashflow"):
        hints.append("현금흐름 데이터 미수신 — 분기 실적 발표 후 재분석 권장.")
    if not coverage.get("has_ma200"):
        hints.append("상장 1년 미만 — 장기 추세 판단이 어려우니 비중을 보수적으로.")
    any_thr = any("경계선" in r for r in conf.get("reasons", []))
    if any_thr:
        hints.append("결론이 경계선에 걸쳐 있습니다. 며칠 뒤 재분석해 방향이 굳는지 확인하세요.")
    return hints
