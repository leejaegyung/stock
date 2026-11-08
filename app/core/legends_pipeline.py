"""
Layer 5 — 월가 거장 5인(버핏·멍거·그레이엄·린치·드러켄밀러) 분석 오케스트레이터.

algo_pipeline.py(LLM-free 메인 분석 경로)가 이미 쓰는 것과 같은 재무·기술·거시
데이터 수집 함수를 그대로 재사용해 중복 호출을 피하고, 5명의 규칙 기반 분석기
(app.core.agents.legends.LegendAnalyst — LLM 호출 없음, 비용 0)에게 판단을
묻는다. 판단을 숫자로 합치는 계산은 app.core.legends_blend 가 담당한다
(Sacred Rule: 해석은 분리된 규칙, 집계는 코드). DB 저장은 하지 않는다 —
호출자(entrypoints)가 결과를 받아 저장한다.
"""

from __future__ import annotations

import logging
import statistics

from app.core.agents.legends import LEGEND_PERSONAS, LegendAnalyst
from app.core.algo_pipeline import _fetch_price_df, _technical_indicators
from app.core.datasources.kr import KRDataSource
from app.core.datasources.us import USDataSource
from app.core.legends_blend import DEFAULT_STRATEGY, blend_votes

logger = logging.getLogger(__name__)


def _peer_stats(ds, ticker: str) -> tuple[float | None, float | None, int]:
    """동일 업종 비교기업의 PER·ROE 중앙값 — 절대 기준 대신 업종 상대 밸류에이션에 쓴다.

    US/KR 데이터소스가 돌려주는 roe 단위가 서로 달라(US는 소수, KR은 이미 %)
    1 이하면 소수로 보고 100을 곱해 맞춘다 — 실제 ROE가 -1~1% 사이인 경우는
    거의 없어 이 어림짐작이 안전하다.
    """
    try:
        peers = (ds.get_peers(ticker) or {}).get("peers", [])
    except Exception as e:
        logger.warning("legends: get_peers failed for %s: %s", ticker, e)
        peers = []
    pers = [p["per"] for p in peers if isinstance(p.get("per"), (int, float)) and p["per"] > 0]
    roes_raw = [p["roe"] for p in peers if isinstance(p.get("roe"), (int, float))]
    roes = [r * 100 if abs(r) <= 1 else r for r in roes_raw]
    med_per = round(statistics.median(pers), 1) if pers else None
    med_roe = round(statistics.median(roes), 1) if roes else None
    return med_per, med_roe, len(peers)


def _build_legend_data(ticker: str, market: str, held: bool, avg_price: float | None) -> dict:
    """5명이 공통으로 보는 데이터 — algo_pipeline.py와 동일한 소스에서 가져온다.

    업종 평균 PER·ROE(동일 업종 비교기업), PBR, 영업이익률, 월가 애널리스트
    컨센서스, 기관 보유비율까지 더해 절대 기준값만 보던 규칙을 상대·외부
    검증 신호로 보강했다 — 전부 기존에 쓰던 무료 데이터소스(yfinance·네이버)
    안에 이미 있던 값이라 추가 비용은 없다.
    """
    ds = USDataSource() if market.upper() != "KR" else KRDataSource()
    us_ds = USDataSource() if market.upper() != "KR" else None

    fund = {}
    macro = {}
    df = None
    try:
        fund = ds.get_financials(ticker)
    except Exception as e:
        logger.warning("legends: get_financials failed for %s: %s", ticker, e)
    try:
        macro = (us_ds or USDataSource()).get_macro_data()
    except Exception as e:
        logger.warning("legends: get_macro_data failed for %s: %s", ticker, e)
    try:
        df = _fetch_price_df(ticker, market, period="1y")
    except Exception as e:
        logger.warning("legends: price fetch failed for %s: %s", ticker, e)

    peer_avg_per, peer_avg_roe, peer_count = _peer_stats(ds, ticker)

    ta = _technical_indicators(df) if df is not None else {"ok": False}
    info = (fund.get("info") or {}) if isinstance(fund, dict) else {}

    def _mv(key: str):
        return (macro.get(key) or {}).get("price") if isinstance(macro, dict) else None

    price = ta.get("price")
    target_mean = info.get("targetMeanPrice")
    analyst_upside_pct = (
        round((target_mean - price) / price * 100, 1)
        if (price and target_mean) else None
    )

    return {
        "price": price,
        "currency": "KRW" if market.upper() == "KR" else "USD",
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "per": info.get("trailingPE") or info.get("forwardPE"),
        "pbr": info.get("priceToBook") or info.get("pbr"),
        "roe_pct": round(info["returnOnEquity"] * 100, 1) if info.get("returnOnEquity") is not None else None,
        "debt_to_equity_pct": info.get("debtToEquity"),
        "profit_margin_pct": round(info["profitMargins"] * 100, 1) if info.get("profitMargins") is not None else None,
        "operating_margin_pct": round(info["operatingMargins"] * 100, 1) if info.get("operatingMargins") is not None else None,
        "earnings_growth_pct": round(info["earningsGrowth"] * 100, 1) if info.get("earningsGrowth") is not None else None,
        "earnings_qoq_growth_pct": round(info["earningsQuarterlyGrowth"] * 100, 1) if info.get("earningsQuarterlyGrowth") is not None else None,
        "revenue_growth_pct": round(info["revenueGrowth"] * 100, 1) if info.get("revenueGrowth") is not None else None,
        "dividend_yield_pct": round(info["dividendYield"] * 100, 2) if info.get("dividendYield") else None,
        "peg_ratio": info.get("pegRatio") or info.get("trailingPegRatio"),
        "market_cap": info.get("marketCap"),
        "rsi": ta.get("rsi"),
        "macd_hist": ta.get("macd_hist"),
        "sma20": ta.get("sma20"), "sma50": ta.get("sma50"), "sma200": ta.get("sma200"),
        "return_1m_pct": ta.get("ret_1m"), "return_3m_pct": ta.get("ret_3m"), "return_6m_pct": ta.get("ret_6m"),
        "week52_high": ta.get("hi_52w"), "week52_low": ta.get("lo_52w"),
        "macro_vix": _mv("VIX"), "macro_cape": _mv("CAPE"), "macro_10y_yield": _mv("10Y 금리"),
        # 월가 컨센서스 / 외부 검증 신호 — 거장들의 "자기 철학" 판단과 별개로 교차 확인용
        "analyst_mean_rating": info.get("recommendationMean"),  # 1=강력매수 ~ 5=매도
        "analyst_count": info.get("numberOfAnalystOpinions"),
        "analyst_target_upside_pct": analyst_upside_pct,
        "institutional_held_pct": round(info["heldPercentInstitutions"] * 100, 1) if info.get("heldPercentInstitutions") is not None else None,
        # 동일 업종 비교기업(최대 5개) 중앙값 — 절대 기준이 아닌 상대 밸류에이션용
        "peer_avg_per": peer_avg_per, "peer_avg_roe": peer_avg_roe, "peer_count": peer_count,
        "held": held, "avg_price": avg_price,
    }


def analyze_legends(
    ticker: str,
    market: str,
    strategy: str = DEFAULT_STRATEGY,
    held: bool = False,
    avg_price: float | None = None,
) -> dict:
    """5명의 거장 분석기를 돌리고 합친다. 규칙 기반이라 즉시(동기) 끝나고 비용이 없다.

    :returns: {"ticker","market","strategy","data","votes":[...5개...],"blend":{...}}
    """
    data = _build_legend_data(ticker, market, held, avg_price)

    votes: list[dict] = []
    for persona in LEGEND_PERSONAS:
        try:
            votes.append(LegendAnalyst(persona).run(ticker, data))
        except Exception as e:
            logger.warning("legends: %s agent failed for %s: %s", persona, ticker, e)
            votes.append({
                "name": persona, "title": LEGEND_PERSONAS[persona]["title"],
                "verdict": "중립", "confidence": 0, "reasons": ["분석 실패"], "key_risk": "",
            })

    blend = blend_votes(votes, strategy)
    return {"ticker": ticker, "market": market, "strategy": blend["strategy"], "data": data, "votes": votes, "blend": blend}
