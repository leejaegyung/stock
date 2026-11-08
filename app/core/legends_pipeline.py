"""
Layer 5 — 월가 거장 5인(버핏·멍거·그레이엄·린치·드러켄밀러) 분석 오케스트레이터.

algo_pipeline.py(LLM-free 메인 분석 경로)가 이미 쓰는 것과 같은 재무·기술·거시
데이터 수집 함수를 그대로 재사용해 중복 호출을 피하고, 5명의 LLM 에이전트
(app.core.agents.legends.LegendAnalyst)에게 동시에 판단을 묻는다. 판단을 숫자로
합치는 계산은 app.core.legends_blend 가 담당한다 (Sacred Rule: LLM은 해석,
코드는 계산). DB 저장은 하지 않는다 — 호출자(entrypoints)가 결과를 받아 저장한다.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from anthropic import Anthropic

from app.core.agents.legends import LEGEND_PERSONAS, LegendAnalyst
from app.core.algo_pipeline import _fetch_price_df, _technical_indicators
from app.core.datasources.kr import KRDataSource
from app.core.datasources.us import USDataSource
from app.core.legends_blend import DEFAULT_STRATEGY, blend_votes

logger = logging.getLogger(__name__)


def _build_legend_data(ticker: str, market: str, held: bool, avg_price: float | None) -> dict:
    """5명이 공통으로 보는 데이터 — algo_pipeline.py와 동일한 소스에서 가져온다."""
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

    ta = _technical_indicators(df) if df is not None else {"ok": False}
    info = (fund.get("info") or {}) if isinstance(fund, dict) else {}

    def _mv(key: str):
        return (macro.get(key) or {}).get("price") if isinstance(macro, dict) else None

    return {
        "price": ta.get("price"),
        "currency": "KRW" if market.upper() == "KR" else "USD",
        "sector": info.get("sector"),
        "per": info.get("trailingPE") or info.get("forwardPE"),
        "roe_pct": round(info["returnOnEquity"] * 100, 1) if info.get("returnOnEquity") is not None else None,
        "debt_to_equity_pct": info.get("debtToEquity"),
        "profit_margin_pct": round(info["profitMargins"] * 100, 1) if info.get("profitMargins") is not None else None,
        "earnings_growth_pct": round(info["earningsGrowth"] * 100, 1) if info.get("earningsGrowth") is not None else None,
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
        "held": held, "avg_price": avg_price,
    }


def analyze_legends(
    ticker: str,
    market: str,
    client: Anthropic,
    strategy: str = DEFAULT_STRATEGY,
    held: bool = False,
    avg_price: float | None = None,
) -> dict:
    """5명의 거장 에이전트를 병렬로 돌리고 합친다.

    :returns: {"ticker","market","strategy","data","votes":[...5개...],"blend":{...}}
    """
    data = _build_legend_data(ticker, market, held, avg_price)

    votes: list[dict] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(LegendAnalyst(client, persona).run, ticker, data): persona
            for persona in LEGEND_PERSONAS
        }
        for fut in as_completed(futures):
            persona = futures[fut]
            try:
                votes.append(fut.result())
            except Exception as e:
                logger.warning("legends: %s agent failed for %s: %s", persona, ticker, e)
                votes.append({
                    "name": persona, "title": LEGEND_PERSONAS[persona]["title"],
                    "verdict": "중립", "confidence": 0, "reasons": ["분석 실패"], "key_risk": "",
                })

    # 표시 순서를 고정(병렬 실행이라 완료 순서가 들쭉날쭉하므로)
    order = {name: i for i, name in enumerate(LEGEND_PERSONAS)}
    votes.sort(key=lambda v: order.get(v.get("name", ""), 99))

    blend = blend_votes(votes, strategy)
    return {"ticker": ticker, "market": market, "strategy": blend["strategy"], "data": data, "votes": votes, "blend": blend}
