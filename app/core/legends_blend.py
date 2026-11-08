"""
Layer 1 — 월가 거장 5인(버핏·멍거·그레이엄·린치·드러켄밀러) 합의 블렌딩.
순수 함수, app import 없음.

virattt/ai-hedge-fund(오픈소스, MIT) 저장소의 구조 — "여러 투자자 페르소나가
각자 매수/매도/중립 + 확신도를 내면, 전략별 가중치로 표를 합쳐 종목별 목표
비중을 계산하고, 의견이 갈리면 현금 비중을 늘린다" — 에서 착안해 우리 데이터
파이프라인에 맞게 자체 구현한 것이다. 각 투자자의 실제 판단(verdict/confidence/
reasons)은 app.core.agents.legends 의 LLM 에이전트가 만들고, 이 모듈은 그 결과를
숫자로 모으는 계산만 한다 (Sacred Rule: LLM은 해석, 코드는 계산).
"""

from __future__ import annotations

LEGEND_NAMES = ("버핏", "멍거", "그레이엄", "린치", "드러켄밀러")

# 전략별 가중치 — 이름의 의미에 맞게 설계:
#   딥 밸류        = 안전마진·저평가를 보는 그레이엄 비중을 가장 높게
#   펀더멘털 롱숏  = 사업의 질·해자를 보는 버핏·멍거 비중을 가장 높게
#   인플렉션       = 성장 변곡점·매크로 모멘텀을 보는 린치·드러켄밀러 비중을 높게
STRATEGY_WEIGHTS: dict[str, dict[str, float]] = {
    "딥 밸류": {"그레이엄": 2.0, "버핏": 1.0, "멍거": 1.0, "린치": 0.5, "드러켄밀러": 0.5},
    "펀더멘털 롱숏": {"버핏": 2.0, "멍거": 2.0, "그레이엄": 1.0, "린치": 1.0, "드러켄밀러": 0.5},
    "인플렉션": {"린치": 2.0, "드러켄밀러": 2.0, "그레이엄": 0.5, "버핏": 0.5, "멍거": 0.5},
}
DEFAULT_STRATEGY = "펀더멘털 롱숏"

MAX_POSITION_PCT = 25.0  # §리스크: 블렌드 결과 자체의 종목당 최대 비중 상한


def _vote_score(verdict: str, confidence: float) -> float:
    """매수=+1, 매도=-1, 중립=0 방향 × 확신도(0~100) = -100~+100."""
    direction = 1 if verdict == "매수" else -1 if verdict == "매도" else 0
    return direction * max(0.0, min(100.0, confidence or 0))


def blend_votes(votes: list[dict], strategy: str = DEFAULT_STRATEGY) -> dict:
    """
    :param votes: [{"name": "버핏", "verdict": "매수|중립|매도", "confidence": 0~100}, ...]
    :param strategy: STRATEGY_WEIGHTS 키 중 하나. 모르는 값이면 기본 전략으로 대체.
    :returns: {
        "target_weight_pct": 0~25,      목표 비중(%) — 매도 합의면 0
        "cash_pct": 75~100,
        "consensus": "매수|중립|매도|혼조",
        "weighted_score": -100~100,
        "dispersion": 0~1,              0=완전 일치, 1=완전 대립(매수표·매도표 가중치가 팽팽)
        "strategy": str,
        "notes": [str, ...],
    }
    """
    weights = STRATEGY_WEIGHTS.get(strategy) or STRATEGY_WEIGHTS[DEFAULT_STRATEGY]
    used_strategy = strategy if strategy in STRATEGY_WEIGHTS else DEFAULT_STRATEGY

    scored = [
        (_vote_score(v.get("verdict", "중립"), v.get("confidence", 0)), weights.get(v.get("name", ""), 1.0))
        for v in votes
    ]
    if not scored:
        return {
            "target_weight_pct": 0.0, "cash_pct": 100.0, "consensus": "중립",
            "weighted_score": 0.0, "dispersion": 0.0, "strategy": used_strategy,
            "notes": ["투자자 의견이 없습니다."],
        }

    total_w = sum(w for _, w in scored) or 1.0
    weighted_score = sum(s * w for s, w in scored) / total_w

    buy_w = sum(w for s, w in scored if s > 0)
    sell_w = sum(w for s, w in scored if s < 0)
    dir_w = buy_w + sell_w
    dispersion = round(1.0 - abs(buy_w - sell_w) / dir_w, 2) if dir_w > 0 else 0.0

    if weighted_score > 15:
        consensus = "매수"
    elif weighted_score < -15:
        consensus = "매도"
    else:
        consensus = "혼조" if dispersion > 0.4 else "중립"

    if weighted_score <= 0:
        target_weight = 0.0
    else:
        raw = min(MAX_POSITION_PCT, weighted_score / 100 * MAX_POSITION_PCT)
        target_weight = raw * (1 - dispersion)  # 의견이 갈릴수록 비중을 현금 쪽으로

    target_weight = round(min(max(target_weight, 0.0), MAX_POSITION_PCT), 1)
    cash_pct = round(100 - target_weight, 1)

    notes: list[str] = []
    if dispersion >= 0.6:
        notes.append(f"투자자 의견이 크게 갈려 목표 비중을 {target_weight}%로 낮췄습니다 (의견 대립 시 현금 확대).")
    if target_weight >= MAX_POSITION_PCT - 0.5:
        notes.append(f"단일 종목 최대 비중({MAX_POSITION_PCT:.0f}%) 한도에 도달했습니다.")
    if weighted_score < 0:
        notes.append("가중 점수가 음수라 신규·추가 비중은 0%로 제한했습니다 (매도 의견이 우세).")

    return {
        "target_weight_pct": target_weight,
        "cash_pct": cash_pct,
        "consensus": consensus,
        "weighted_score": round(weighted_score, 1),
        "dispersion": dispersion,
        "strategy": used_strategy,
        "notes": notes,
    }
