"""
Layer 1 — 분석 시그널 성과 집계 (순수 함수, app import 없음).

과거 리포트의 결론(verdict)과 그 이후 실제 수익률을 대조해
적중률·평균 수익률을 결론별·확신도별로 집계한다.

'적중'의 정의 (band = 중립 허용폭, 기본 2%):
  매수 / 추가매수  → 이후 수익률 > +band          이면 적중
  매도            → 이후 수익률 < -band          이면 적중
  보유            → -band ≤ 이후 수익률 ≤ +band  이면 적중 (횡보 예측)
방향이 없는 결론(알 수 없음)은 집계에서 제외.
"""

from __future__ import annotations

_DIRECTIONAL = {"매수", "추가매수", "매도", "보유"}


def is_hit(verdict: str, fwd_return_pct: float, band: float = 2.0) -> bool | None:
    """단일 시그널 적중 여부. 방향 없는 결론이면 None."""
    if verdict in ("매수", "추가매수"):
        return fwd_return_pct > band
    if verdict == "매도":
        return fwd_return_pct < -band
    if verdict == "보유":
        return -band <= fwd_return_pct <= band
    return None


def _agg(samples: list[dict]) -> dict:
    """samples: [{hit: bool, ret: float}] → 집계 dict."""
    n = len(samples)
    if n == 0:
        return {"count": 0, "hit_rate": None, "avg_return": None, "median_return": None}
    hits = sum(1 for s in samples if s["hit"])
    rets = sorted(s["ret"] for s in samples)
    mid = rets[n // 2] if n % 2 else (rets[n // 2 - 1] + rets[n // 2]) / 2
    return {
        "count": n,
        "hits": hits,
        "hit_rate": round(hits / n, 3),
        "avg_return": round(sum(rets) / n, 2),
        "median_return": round(mid, 2),
        "best_return": round(rets[-1], 2),
        "worst_return": round(rets[0], 2),
    }


def summarize(signals: list[dict], band: float = 2.0) -> dict:
    """signals: [{verdict, confidence, fwd_return_pct}] 목록을 집계.

    반환: overall / by_verdict / by_confidence / directional(매수·매도만).
    """
    scored: list[dict] = []
    for s in signals:
        v = s.get("verdict")
        r = s.get("fwd_return_pct")
        if v not in _DIRECTIONAL or r is None:
            continue
        h = is_hit(v, float(r), band)
        if h is None:
            continue
        scored.append({
            "verdict": v,
            "confidence": s.get("confidence") or "?",
            "ret": float(r),
            "hit": h,
        })

    by_verdict: dict[str, dict] = {}
    for v in ("매수", "추가매수", "보유", "매도"):
        by_verdict[v] = _agg([x for x in scored if x["verdict"] == v])

    by_conf: dict[str, dict] = {}
    for c in ("상", "중", "하"):
        by_conf[c] = _agg([x for x in scored if x["confidence"] == c])

    directional = _agg([x for x in scored if x["verdict"] in ("매수", "추가매수", "매도")])

    return {
        "band": band,
        "overall": _agg(scored),
        "directional": directional,
        "by_verdict": by_verdict,
        "by_confidence": by_conf,
    }
