"""
Layer 5 — Pipeline orchestrator.
Coordinates all agents and datasources. No business logic lives here.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from anthropic import Anthropic

from app.config import settings
from app.core.agents.bear import BearResearcher
from app.core.agents.bull import BullResearcher
from app.core.agents.chief_advisor import ChiefAdvisor
from app.core.agents.critic import Critic
from app.core.agents.fundamental import FundamentalAnalyst
from app.core.agents.industry import IndustryAnalyst
from app.core.agents.macro import MacroAnalyst
from app.core.agents.news_sentiment import NewsSentimentAnalyst
from app.core.agents.quant_risk import QuantRisk
from app.core.agents.research_manager import ResearchManager
from app.core.datasources.base import DataSourceBase
from app.core.datasources.kr import KRDataSource
from app.core.datasources.us import USDataSource

logger = logging.getLogger(__name__)

DEBATE_ROUNDS = 2


def _get_datasource(market: str) -> DataSourceBase:
    if market.upper() == "KR":
        return KRDataSource()
    return USDataSource()


def _build_client() -> Anthropic:
    return Anthropic(api_key=settings.anthropic_api_key)


def analyze_stock(
    ticker: str,
    market: str,
    date_str: str,
    client: Anthropic,
    macro_data: dict | None = None,
) -> dict:
    """
    Full 4-layer analysis pipeline for a single stock.
    macro_data can be pre-computed and passed in (cache for morning brief).
    """
    ds = _get_datasource(market)
    us_ds = USDataSource() if market.upper() != "KR" else None

    # Layer 1: Parallel data collection
    def fetch_fundamentals() -> dict:
        return ds.get_financials(ticker)

    def fetch_news() -> list[dict]:
        return ds.get_news(ticker, days=3)

    def fetch_macro() -> dict:
        if macro_data:
            return macro_data
        src = us_ds or USDataSource()
        return src.get_macro_data()

    def fetch_peers() -> dict:
        return ds.get_peers(ticker)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(fetch_fundamentals): "fundamentals",
            pool.submit(fetch_news): "news",
            pool.submit(fetch_macro): "macro",
            pool.submit(fetch_peers): "peers",
        }
        raw: dict = {}
        for future in as_completed(futures):
            key = futures[future]
            try:
                raw[key] = future.result()
            except Exception as e:
                logger.warning("Data fetch failed for %s/%s: %s", ticker, key, e)
                raw[key] = {}

    # Layer 1 agents: parallel analysis (haiku)
    fund_agent = FundamentalAnalyst(client)
    news_agent = NewsSentimentAnalyst(client)
    macro_agent = MacroAnalyst(client)
    industry_agent = IndustryAnalyst(client)

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_fund = pool.submit(fund_agent.run, ticker, raw.get("fundamentals", {}))
        f_news = pool.submit(news_agent.run, ticker, date_str, raw.get("news", []))
        f_macro = pool.submit(macro_agent.run, date_str, raw.get("macro", {}))
        f_industry = pool.submit(industry_agent.run, ticker, raw.get("peers", {}))

        fundamental_note = f_fund.result()
        news_note = f_news.result()
        macro_note = f_macro.result()
        industry_note = f_industry.result()

    analyst_context = {
        "ticker": ticker,
        "market": market,
        "date": date_str,
        "fundamental": fundamental_note,
        "news_sentiment": news_note,
        "macro": macro_note,
        "industry": industry_note,
    }

    # Layer 2: Bull/Bear debate (sonnet)
    bull = BullResearcher(client)
    bear = BearResearcher(client)
    debate_transcript: list[dict] = []

    bull_arg = bull.run(analyst_context)
    debate_transcript.append({"role": "Bull", "content": bull_arg})
    bear_arg = bear.run(analyst_context, bull_argument=bull_arg)
    debate_transcript.append({"role": "Bear", "content": bear_arg})

    for _ in range(DEBATE_ROUNDS - 1):
        bull_rebuttal = bull.run(analyst_context, bear_rebuttal=bear_arg)
        debate_transcript.append({"role": "Bull", "content": bull_rebuttal})
        bear_arg = bear.run(analyst_context, bull_argument=bull_rebuttal)
        debate_transcript.append({"role": "Bear", "content": bear_arg})

    manager = ResearchManager(client)
    research_summary = manager.run(debate_transcript)

    # Layer 3: Quant/Risk (sonnet + formulas.py)
    quant = QuantRisk(client)
    quant_result = quant.run(research_summary, fundamental_note)

    # Layer 4: Chief Advisor (opus)
    full_context = {
        **analyst_context,
        "debate_transcript": debate_transcript,
        "research_summary": research_summary,
        "quant_risk": quant_result,
    }
    advisor = ChiefAdvisor(client)
    advice = advisor.run(full_context)

    # Phase 5: Reflection / self-verification (FinVision §3)
    # Critic reviews the advice for logical inconsistencies and may revise verdict.
    critic = Critic(client)
    reflection = critic.run(full_context, advice)
    if not reflection.get("passed") and reflection.get("corrections"):
        logger.info(
            "Critic corrections for %s: %s",
            ticker,
            "; ".join(reflection["corrections"]),
        )

    return {
        "ticker": ticker,
        "market": market,
        "date": date_str,
        "fundamental": fundamental_note,
        "news_sentiment": news_note,
        "macro": macro_note,
        "industry": industry_note,
        "debate_transcript": debate_transcript,
        "research_summary": research_summary,
        "quant_risk": quant_result,
        "advice": advice,        # may be mutated by Critic
        "reflection": reflection,
    }


def morning_brief(watchlist: list[dict], date_str: str | None = None) -> str:
    """
    Run analysis for all watchlist stocks and format as morning briefing markdown.
    Macro data is fetched once and reused across all tickers.
    """
    if date_str is None:
        date_str = date.today().isoformat()

    client = _build_client()
    us_ds = USDataSource()
    shared_macro_raw = us_ds.get_macro_data()

    macro_agent = MacroAnalyst(client)
    shared_macro_note = macro_agent.run(date_str, shared_macro_raw)

    results = []
    for stock in watchlist:
        ticker = stock["ticker"]
        market = stock["market"]
        logger.info("Analyzing %s [%s]", ticker, market)
        try:
            result = analyze_stock(ticker, market, date_str, client, macro_data=shared_macro_raw)
            results.append(result)
        except Exception as e:
            logger.error("Analysis failed for %s: %s", ticker, e)
            results.append({"ticker": ticker, "market": market, "error": str(e)})

    return _format_brief(date_str, shared_macro_note, results)


def _format_brief(date_str: str, macro_note: dict, results: list[dict]) -> str:
    lines = [f"# 📈 아침 브리핑 — {date_str}\n"]

    lines.append("## 🌍 오늘의 시장")
    lines.append(f"- 거시 코멘트: {macro_note.get('macro_comment', macro_note.get('raw', ''))}")
    lines.append(f"- CAPE 판정: {macro_note.get('cape_judgment', '-')}")
    lines.append(f"- 한미 구매력·물가 비교: {macro_note.get('purchasing_power_comparison', '-')}")
    lines.append("")

    lines.append("## 📊 보유 종목별 브리핑")
    for r in results:
        ticker = r["ticker"]
        market = r.get("market", "")
        if "error" in r:
            lines.append(f"### {ticker} [{market}] — ⚠️ 분석 실패: {r['error']}")
            continue

        advice = r.get("advice", {})
        verdict = advice.get("verdict", "보류")
        confidence = advice.get("confidence", "-")
        emoji = {"매수": "🟢", "추가매수": "🔵", "보유": "🟡", "매도": "🔴"}.get(verdict, "⚪")
        qr = r.get("quant_risk", {})

        lines.append(f"### {ticker} [{market}]")
        lines.append(f"- **결론**: {emoji}{verdict}  (확신도: {confidence})")

        for reason in advice.get("key_reasons", []):
            lines.append(f"- {reason}")

        lines.append(
            f"- 기대값: {qr.get('ev', '-')}  |  "
            f"켈리 권장비중: {qr.get('half_kelly', '-')} (하프켈리)"
        )
        lines.append(f"- 밸류에이션: {qr.get('valuation', '-')}")
        if qr.get("cashflow_pattern"):
            lines.append(f"- 현금흐름 패턴: {qr['cashflow_pattern']}")

        research = r.get("research_summary", {})
        lines.append(f"- Bull vs Bear: {research.get('verdict', '-')}")

        for warning in advice.get("warnings", []):
            lines.append(f"- ⚠️ {warning}")

        lines.append("")

    lines.append("## ⚠️ 면책")
    lines.append(
        "본 브리핑은 정보 제공·분석 보조 목적이며 투자 자문이 아닙니다. "
        "모든 수치는 가정에 근거한 추정치이며 최종 투자 판단과 책임은 사용자에게 있습니다."
    )

    return "\n".join(lines)
