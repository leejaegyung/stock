"""
Layer 6 — APScheduler entrypoint.
Runs the morning brief job and watcher loop.
Imported by web.py's lifespan; can also run standalone.
"""

import logging
from datetime import date, datetime, timedelta, timezone

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.db.client import get_session_factory
from app.db.models import AnalysisReport, NewsItem, Watchlist, create_all_tables

logger = logging.getLogger(__name__)

KST = pytz.timezone("Asia/Seoul")
_scheduler: BackgroundScheduler | None = None


def _run_morning_brief() -> None:
    from app.core.algo_pipeline import analyze_stock_algo, morning_brief_algo
    from app.core.datasources.us import USDataSource

    date_str = date.today().isoformat()
    logger.info("Scheduled morning brief starting: %s", date_str)

    create_all_tables(settings.db_path)
    factory = get_session_factory(settings.db_path)

    with factory() as session:
        rows = session.query(Watchlist).all()
        watchlist = [{"ticker": r.ticker, "market": r.market} for r in rows]

    if not watchlist:
        logger.info("No watchlist entries; skipping brief.")
        return

    shared_macro = USDataSource().get_macro_data()

    # 종목별 개별 저장
    import hashlib
    from datetime import datetime as _dt
    from app.core.algo_pipeline import classify_news_algo

    for stock in watchlist:
        ticker, market = stock["ticker"], stock["market"]
        try:
            result = analyze_stock_algo(ticker, market, date_str, macro_data=shared_macro)
            advice = result.get("advice", {})
            with factory() as session:
                # 이전 보고서 삭제 후 새 보고서 저장
                session.query(AnalysisReport).filter(
                    AnalysisReport.ticker == ticker,
                    AnalysisReport.market == market,
                ).delete(synchronize_session=False)
                session.add(AnalysisReport(
                    ticker=ticker, market=market, date=date_str,
                    verdict=advice.get("verdict"),
                    confidence=advice.get("confidence"),
                    report_md=advice.get("brief_section", ""),
                ))
                # 뉴스 저장 (url_hash dedup)
                for item in result.get("raw_news", []):
                    url = item.get("url", "")
                    headline = item.get("headline", "")
                    url_hash = hashlib.md5((url or headline).encode()).hexdigest()
                    if session.query(NewsItem).filter_by(url_hash=url_hash).first():
                        continue
                    pub_str = item.get("published_at", "")
                    try:
                        pub_dt = _dt.fromisoformat(pub_str)
                    except (ValueError, TypeError):
                        pub_dt = _dt.utcnow()
                    session.add(NewsItem(
                        ticker=ticker, market=market,
                        headline=headline,
                        summary=item.get("summary", ""),
                        impact=classify_news_algo(headline, item.get("summary", "")),
                        source=item.get("source", ""),
                        url=url,
                        published_at=pub_dt,
                        url_hash=url_hash,
                    ))
                session.commit()
        except Exception as e:
            logger.error("Brief analyze %s failed: %s", ticker, e)

    # 전체 브리핑 요약 저장
    brief_md = morning_brief_algo(watchlist, date_str)
    with factory() as session:
        # 이전 브리핑 삭제 후 새 브리핑 저장
        session.query(AnalysisReport).filter(
            AnalysisReport.ticker == "_BRIEF_",
            AnalysisReport.market == "ALL",
        ).delete(synchronize_session=False)
        session.add(AnalysisReport(
            ticker="_BRIEF_",
            market="ALL",
            date=date_str,
            verdict="브리핑",
            confidence="-",
            report_md=brief_md,
        ))
        session.commit()
    logger.info("Morning brief saved for %s", date_str)


def _run_watcher() -> None:
    """
    Lightweight news watcher (§5.5).
    Fetches latest news, deduplicates via url_hash, saves to NewsItem table.
    Triggers full deep analysis when impact is 촉매 or 저해 (auto-trigger rule).
    """
    import hashlib
    from app.core.datasources.us import USDataSource
    from app.core.datasources.kr import KRDataSource
    from app.db.models import NewsItem

    create_all_tables(settings.db_path)
    factory = get_session_factory(settings.db_path)

    with factory() as session:
        rows = session.query(Watchlist).all()
        watchlist = [{"ticker": r.ticker, "market": r.market} for r in rows]

    if not watchlist:
        return

    client = None  # 키워드 분류 — API 불필요

    for stock in watchlist:
        ticker = stock["ticker"]
        market = stock["market"]
        ds = USDataSource() if market == "US" else KRDataSource()
        try:
            items = ds.get_news(ticker, days=1)
        except Exception as e:
            logger.warning("Watcher news fetch failed for %s: %s", ticker, e)
            continue

        for item in items:
            url = item.get("url", "")
            headline = item.get("headline", "")
            url_hash = hashlib.md5((url or headline).encode()).hexdigest()

            # Deduplication: skip already-seen items
            with factory() as session:
                existing = session.query(NewsItem).filter_by(url_hash=url_hash).first()
                if existing:
                    continue

            impact = _quick_classify(client, headline, item.get("summary", ""))

            # Persist to DB
            with factory() as session:
                try:
                    from datetime import datetime
                    pub_str = item.get("published_at", "")
                    pub_dt = datetime.fromisoformat(pub_str) if pub_str else datetime.utcnow()
                    session.add(NewsItem(
                        ticker=ticker,
                        market=market,
                        headline=headline,
                        summary=item.get("summary", ""),
                        impact=impact,
                        source=item.get("source", ""),
                        url=url,
                        published_at=pub_dt,
                        url_hash=url_hash,
                    ))
                    session.commit()
                except Exception as e:
                    logger.warning("NewsItem save failed for %s: %s", ticker, e)

            if impact in ("촉매", "저해"):
                logger.warning("ALERT [%s/%s] %s: %s", ticker, market, impact, headline)
                # Auto-trigger full deep analysis for actionable news (§5.5)
                _trigger_deep_analysis(ticker, market, client, factory)


def _trigger_deep_analysis(ticker: str, market: str, client, factory) -> None:
    """Run algorithmic analysis for a ticker and save the report."""
    import hashlib
    from datetime import date, datetime as _dt
    from app.core.algo_pipeline import analyze_stock_algo, classify_news_algo
    from app.db.models import AnalysisReport

    date_str = date.today().isoformat()
    logger.info("Auto algo-analysis triggered: %s [%s]", ticker, market)
    try:
        result = analyze_stock_algo(ticker, market, date_str)
        advice = result.get("advice", {})
        with factory() as session:
            # 이전 보고서 삭제 후 새 보고서 저장
            session.query(AnalysisReport).filter(
                AnalysisReport.ticker == ticker,
                AnalysisReport.market == market,
            ).delete(synchronize_session=False)
            session.add(AnalysisReport(
                ticker=ticker,
                market=market,
                date=date_str,
                verdict=advice.get("verdict", ""),
                confidence=advice.get("confidence", ""),
                report_md=advice.get("brief_section", ""),
            ))
            # 뉴스 저장 (url_hash dedup)
            for item in result.get("raw_news", []):
                url = item.get("url", "")
                headline = item.get("headline", "")
                url_hash = hashlib.md5((url or headline).encode()).hexdigest()
                if session.query(NewsItem).filter_by(url_hash=url_hash).first():
                    continue
                pub_str = item.get("published_at", "")
                try:
                    pub_dt = _dt.fromisoformat(pub_str)
                except (ValueError, TypeError):
                    pub_dt = _dt.utcnow()
                session.add(NewsItem(
                    ticker=ticker, market=market,
                    headline=headline,
                    summary=item.get("summary", ""),
                    impact=classify_news_algo(headline, item.get("summary", "")),
                    source=item.get("source", ""),
                    url=url,
                    published_at=pub_dt,
                    url_hash=url_hash,
                ))
            session.commit()
        logger.info("Auto algo-analysis saved: %s [%s] → %s", ticker, market, advice.get("verdict"))
    except Exception as e:
        logger.error("Auto algo-analysis failed for %s: %s", ticker, e)


def _quick_classify(client, headline: str, summary: str) -> str:
    """키워드 기반 분류 (LLM 없음). Returns 촉매/중립/저해."""
    from app.core.algo_pipeline import classify_news_algo
    return classify_news_algo(headline, summary)


def _auto_apply_recurring() -> None:
    """매월 1일 KST 00:05 — 활성 고정거래를 해당 월 가계부에 자동 등록."""
    import calendar
    from app.db.models import LedgerTransaction, RecurringTransaction

    today = date.today()
    year, month = today.year, today.month
    max_day = calendar.monthrange(year, month)[1]

    create_all_tables(settings.db_path)
    factory = get_session_factory(settings.db_path)

    with factory() as session:
        templates = session.query(RecurringTransaction).filter_by(is_active=1).all()
        count = 0
        for t in templates:
            existing = session.query(LedgerTransaction).filter(
                LedgerTransaction.source_recurring_id == t.id,
                LedgerTransaction.date.startswith(f"{year:04d}-{month:02d}"),
            ).first()
            if existing:
                continue
            day = min(t.day_of_month, max_day)
            session.add(LedgerTransaction(
                date=f"{year:04d}-{month:02d}-{day:02d}",
                type=t.type,
                category=t.category,
                amount=t.amount,
                memo=t.memo or "[고정]",
                source_recurring_id=t.id,
            ))
            count += 1
        session.commit()
    logger.info("고정거래 자동 적용: %d건 (%04d-%02d)", count, year, month)


def _cleanup_old_news() -> None:
    """2주(14일) 지난 뉴스 항목을 DB에서 삭제."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    create_all_tables(settings.db_path)
    factory = get_session_factory(settings.db_path)
    with factory() as session:
        deleted = (
            session.query(NewsItem)
            .filter(NewsItem.published_at < cutoff)
            .delete(synchronize_session=False)
        )
        session.commit()
    if deleted:
        logger.info("Cleanup: deleted %d news items older than 14 days", deleted)


def _prewarm_caches() -> None:
    """거시지표·포트폴리오 캐시를 미리 갱신 — 사용자 요청이 항상 즉답되도록."""
    try:
        import asyncio

        from app.entrypoints.web import (
            _get_macro_cached,
            get_portfolio,
            market_outlook,
            portfolio_analytics,
        )

        _get_macro_cached(force=True)

        async def _warm():
            await get_portfolio(refresh=True)
            try:
                await portfolio_analytics(refresh=True)  # SPY 벤치·상관·백테스트 캐시까지
            except Exception as e:
                logger.debug("prewarm analytics skipped: %s", e)
            try:
                await market_outlook(refresh=True)  # 시장 전망 (섹터 스캔)
            except Exception as e:
                logger.debug("prewarm outlook skipped: %s", e)

        try:
            asyncio.run(_warm())
        except Exception as e:
            logger.debug("prewarm portfolio skipped: %s", e)
        logger.info("caches pre-warmed (macro + portfolio + analytics)")
    except Exception as e:
        logger.warning("prewarm failed: %s", e)


def _translate_news_job() -> None:
    """번역 안 된 외신 뉴스를 한국어로 채운다 (LLM 미사용, 자체 번역 워크플로우).

    무료 엔드포인트 한도를 넘지 않도록 8건씩만 처리 — 백로그는 여러 주기에 걸쳐 소진.
    """
    try:
        from app.entrypoints.web import _translate_pending_news

        n = _translate_pending_news(max_items=8)
        if n:
            logger.info("translated %d foreign news items", n)
    except Exception as e:
        logger.warning("news translation job failed: %s", e)


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone=KST)

    # 캐시 프리워밍: 시작 10초 후 + 이후 12분마다
    _scheduler.add_job(
        _prewarm_caches,
        trigger=IntervalTrigger(minutes=12),
        id="prewarm_caches",
        next_run_time=datetime.now(KST) + timedelta(seconds=3),
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # 외신 뉴스 한국어 번역: 시작 20초 후 + 이후 6분마다 (밀린 것 25건씩 처리)
    _scheduler.add_job(
        _translate_news_job,
        trigger=IntervalTrigger(minutes=6),
        id="translate_news",
        next_run_time=datetime.now(KST) + timedelta(seconds=20),
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Morning brief: KST 07:00
    _scheduler.add_job(
        _run_morning_brief,
        trigger=CronTrigger(hour=7, minute=0, timezone=KST),
        id="morning_brief",
        replace_existing=True,
    )

    # News watcher: KST 12:00, 18:00
    _scheduler.add_job(
        _run_watcher,
        trigger=CronTrigger(hour=12, minute=0, timezone=KST),
        id="news_watcher_noon",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_watcher,
        trigger=CronTrigger(hour=18, minute=0, timezone=KST),
        id="news_watcher_evening",
        replace_existing=True,
    )

    # Old news cleanup: KST 03:00 매일 (14일 이상 된 기사 삭제)
    _scheduler.add_job(
        _cleanup_old_news,
        trigger=CronTrigger(hour=3, minute=0, timezone=KST),
        id="news_cleanup",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started. Brief: KST 07:00 | Watcher: KST 12:00, 18:00 | Cleanup: KST 03:00",
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
