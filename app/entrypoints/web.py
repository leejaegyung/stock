"""
Layer 6 — FastAPI web entrypoint.
Scheduler starts in lifespan; nginx proxies /api/* to this app.
"""

import logging
import logging.config
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import settings
from app.db.client import get_session_factory
from app.db.models import AnalysisReport, AssetItem, LedgerTransaction, NewsItem, RecurringTransaction, UserApiKey, Watchlist, create_all_tables
from app.entrypoints.scheduler import start_scheduler, stop_scheduler

# 앱 전체 로거를 INFO로 설정 (uvicorn 기본은 WARNING)
logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"default": {"format": "%(levelname)s [%(name)s] %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "default"}},
    "loggers": {
        "app": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "apscheduler": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
})

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(application: FastAPI):
    create_all_tables(settings.db_path)
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Stock Analyst Service", lifespan=lifespan)

_session_factory = None


def _session():
    global _session_factory
    if _session_factory is None:
        _session_factory = get_session_factory(settings.db_path)
    return _session_factory()


# ── HTML ──────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


# ── Watchlist API ─────────────────────────────────────────────────────────────

class WatchlistCreate(BaseModel):
    ticker: str
    market: str
    name: str


@app.get("/api/watchlist")
async def get_watchlist() -> list[dict[str, Any]]:
    with _session() as session:
        rows = session.query(Watchlist).all()
        return [
            {
                "id": r.id, "ticker": r.ticker, "market": r.market,
                "name": r.name, "added_at": str(r.added_at),
                "quantity": float(r.quantity or 0),
                "avg_price": float(r.avg_price or 0),
            }
            for r in rows
        ]


@app.post("/api/watchlist", status_code=201)
async def add_to_watchlist(body: WatchlistCreate) -> dict:
    with _session() as session:
        existing = session.query(Watchlist).filter_by(
            ticker=body.ticker.upper(), market=body.market.upper()
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail="Already exists")
        row = Watchlist(ticker=body.ticker.upper(), name=body.name, market=body.market.upper())
        session.add(row)
        session.commit()
        return {"ticker": row.ticker, "market": row.market, "name": row.name}


class HoldingUpdate(BaseModel):
    quantity: float
    avg_price: float = 0.0
    market: str = "US"


@app.patch("/api/watchlist/{ticker}/holding")
async def update_holding(ticker: str, body: HoldingUpdate) -> dict:
    """보유 수량 및 평균 매입가 업데이트."""
    with _session() as session:
        row = session.query(Watchlist).filter_by(
            ticker=ticker.upper(), market=body.market.upper()
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        row.quantity = body.quantity
        row.avg_price = body.avg_price
        session.commit()
        return {"ticker": row.ticker, "market": row.market,
                "quantity": row.quantity, "avg_price": row.avg_price}


@app.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(ticker: str, market: str = "US") -> dict:
    with _session() as session:
        row = session.query(Watchlist).filter_by(
            ticker=ticker.upper(), market=market.upper()
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        session.delete(row)
        session.commit()
        return {"deleted": ticker}


# ── Reports API ───────────────────────────────────────────────────────────────

@app.get("/api/reports")
async def list_reports(limit: int = 20) -> list[dict[str, Any]]:
    with _session() as session:
        rows = (
            session.query(AnalysisReport)
            .order_by(AnalysisReport.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "ticker": r.ticker,
                "market": r.market,
                "date": r.date,
                "verdict": r.verdict,
                "confidence": r.confidence,
                "created_at": str(r.created_at),
            }
            for r in rows
        ]


@app.get("/api/reports/{report_id}")
async def get_report(report_id: int) -> dict[str, Any]:
    with _session() as session:
        row = session.query(AnalysisReport).filter_by(id=report_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        return {
            "id": row.id,
            "ticker": row.ticker,
            "market": row.market,
            "date": row.date,
            "verdict": row.verdict,
            "confidence": row.confidence,
            "report_md": row.report_md,
            "created_at": str(row.created_at),
        }


# ── API 키 헬퍼 ───────────────────────────────────────────────────────────────

def _get_api_key(provider: str, user_id: str = "default") -> str:
    """활성화된 DB 키 우선, 없으면 .env 폴백 (anthropic만)."""
    with _session() as session:
        row = session.query(UserApiKey).filter_by(user_id=user_id, provider=provider).first()
        if row and row.api_key and row.is_active:
            return row.api_key
    if provider == "anthropic":
        return settings.anthropic_api_key
    return ""


# ── Analysis Trigger ──────────────────────────────────────────────────────────

def _bg_analyze(ticker: str, market: str, date_str: str) -> None:
    import hashlib
    from datetime import datetime as _dt
    from app.core.algo_pipeline import analyze_stock_algo, classify_news_algo

    result = analyze_stock_algo(ticker, market, date_str)
    advice = result.get("advice", {})

    with _session() as session:
        # 같은 ticker+market의 이전 보고서 전체 삭제
        session.query(AnalysisReport).filter(
            AnalysisReport.ticker == ticker,
            AnalysisReport.market == market,
        ).delete(synchronize_session=False)

        session.add(AnalysisReport(
            ticker=ticker,
            market=market,
            date=date_str,
            verdict=advice.get("verdict"),
            confidence=advice.get("confidence"),
            report_md=advice.get("brief_section", ""),
        ))

        # 분석 시 가져온 뉴스를 DB에 저장 (url_hash dedup)
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


@app.post("/api/analyze/{ticker}")
async def trigger_analysis(
    ticker: str,
    background_tasks: BackgroundTasks,
    market: str = "US",
    date_str: str | None = None,
) -> dict:
    if date_str is None:
        date_str = date.today().isoformat()
    background_tasks.add_task(_bg_analyze, ticker.upper(), market.upper(), date_str)
    return {"status": "queued", "ticker": ticker, "market": market, "date": date_str}


# ── Stock Info Lookup ──────────────────────────────────────────────────────────

@app.get("/api/stock-info/{ticker}")
async def stock_info(ticker: str, market: str = "US") -> dict:
    """종목명 자동 조회 (폼 입력 보조용)"""
    t = ticker.upper()
    m = market.upper()
    try:
        if m == "KR":
            import requests
            r = requests.get(
                f"https://m.stock.naver.com/api/stock/{t}/basic",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=5,
            )
            if r.ok:
                data = r.json()
                name = data.get("stockName") or data.get("itemName") or t
                return {"ticker": t, "market": m, "name": name}
        else:
            import yfinance as yf
            info = yf.Ticker(t).info
            name = info.get("shortName") or info.get("longName") or t
            return {"ticker": t, "market": m, "name": name}
    except Exception as e:
        logger.warning("stock_info lookup failed: %s", e)
    return {"ticker": t, "market": m, "name": t}


# ── Morning Brief Trigger ──────────────────────────────────────────────────────

def _bg_brief() -> None:
    import hashlib
    from datetime import datetime as _dt
    from app.core.algo_pipeline import analyze_stock_algo, morning_brief_algo, classify_news_algo
    from app.core.datasources.us import USDataSource

    date_str = date.today().isoformat()

    with _session() as session:
        watchlist = session.query(Watchlist).all()
        items = [{"ticker": w.ticker, "market": w.market} for w in watchlist]

    if not items:
        logger.info("brief: watchlist is empty, skipping")
        return

    shared_macro = USDataSource().get_macro_data()
    verdicts: list[str] = []

    for stock in items:
        ticker, market = stock["ticker"], stock["market"]
        try:
            result = analyze_stock_algo(ticker, market, date_str, macro_data=shared_macro)
            advice = result.get("advice", {})
            with _session() as session:
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
            verdicts.append(f"{ticker}[{market}] → {advice.get('verdict', '?')}")
        except Exception as e:
            logger.error("brief analyze %s failed: %s", ticker, e)

    brief_md = morning_brief_algo(items, date_str)
    with _session() as session:
        # 이전 브리핑 삭제 후 새 브리핑 저장
        session.query(AnalysisReport).filter(
            AnalysisReport.ticker == "_BRIEF_",
            AnalysisReport.market == "ALL",
        ).delete(synchronize_session=False)
        session.add(AnalysisReport(
            ticker="_BRIEF_", market="ALL", date=date_str,
            verdict="완료", confidence="high",
            report_md=brief_md,
        ))
        session.commit()
    logger.info("brief complete: %s", ", ".join(verdicts))


@app.post("/api/brief")
async def trigger_brief(background_tasks: BackgroundTasks) -> dict:
    background_tasks.add_task(_bg_brief)
    return {"status": "queued", "date": date.today().isoformat()}


# ── Home Dashboard API ─────────────────────────────────────────────────────────

_macro_cache: dict = {"data": {}, "ts": 0.0}
_MACRO_TTL = 900  # 15분


def _get_macro_cached() -> dict:
    import time
    now = time.time()
    if now - _macro_cache["ts"] < _MACRO_TTL and _macro_cache["data"]:
        return _macro_cache["data"]
    try:
        from app.core.datasources.us import USDataSource
        from app.core.datasources.kr import KRDataSource
        kr = KRDataSource().get_macro_data()
        us = USDataSource().get_macro_data()
        merged = {**kr, **us}
        _macro_cache["data"] = merged
        _macro_cache["ts"] = now
        return merged
    except Exception as e:
        logger.warning("macro cache refresh failed: %s", e)
        return _macro_cache["data"] or {}


@app.get("/api/home")
async def home_data() -> dict:
    """홈 대시보드 데이터: 거시지표 + 관심종목(최신 결론) + 오늘 브리핑 상태."""
    today = date.today().isoformat()

    with _session() as session:
        watchlist = session.query(Watchlist).all()
        ticker_keys = [(w.ticker, w.market) for w in watchlist]

        # 종목별 최신 보고서
        latest_reports: dict[str, Any] = {}
        for ticker, market in ticker_keys:
            row = (
                session.query(AnalysisReport)
                .filter(
                    AnalysisReport.ticker == ticker,
                    AnalysisReport.market == market,
                )
                .order_by(AnalysisReport.created_at.desc())
                .first()
            )
            if row:
                latest_reports[f"{ticker}-{market}"] = {
                    "id": row.id,
                    "verdict": row.verdict,
                    "confidence": row.confidence,
                    "date": row.date,
                }

        # 오늘 브리핑
        brief_row = (
            session.query(AnalysisReport)
            .filter(AnalysisReport.ticker == "_BRIEF_", AnalysisReport.date == today)
            .order_by(AnalysisReport.created_at.desc())
            .first()
        )

        watchlist_data = [
            {
                "ticker": w.ticker,
                "market": w.market,
                "name": w.name,
                "added_at": str(w.added_at)[:10],
                "report": latest_reports.get(f"{w.ticker}-{w.market}"),
            }
            for w in watchlist
        ]

    macro = _get_macro_cached()

    return {
        "today": today,
        "macro": macro,
        "watchlist": watchlist_data,
        "brief_today": brief_row.id if brief_row else None,
    }


@app.get("/api/news")
async def recent_news(limit: int = 20) -> list[dict]:
    """최근 뉴스 아이템 목록."""
    from app.db.models import NewsItem
    with _session() as session:
        rows = (
            session.query(NewsItem)
            .order_by(NewsItem.published_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "ticker": r.ticker,
                "market": r.market,
                "headline": r.headline,
                "summary": r.summary,
                "impact": r.impact,
                "source": r.source,
                "url": r.url or "",
                "published_at": str(r.published_at)[:16],
            }
            for r in rows
        ]


# ── Scheduler Status ───────────────────────────────────────────────────────────

@app.get("/api/scheduler/status")
async def scheduler_status() -> dict:
    """스케줄러 잡 상태 및 다음 실행 시간 조회."""
    from app.entrypoints.scheduler import _scheduler
    import pytz
    KST = pytz.timezone("Asia/Seoul")

    if not _scheduler or not _scheduler.running:
        return {"running": False, "jobs": []}

    jobs = []
    for job in _scheduler.get_jobs():
        nrt = job.next_run_time
        nrt_kst = nrt.astimezone(KST).isoformat() if nrt else None
        jobs.append({
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run_kst": nrt_kst,
        })

    return {"running": True, "jobs": jobs}


@app.post("/api/scheduler/run-brief-now")
async def run_brief_now(background_tasks: BackgroundTasks) -> dict:
    """테스트용: 모닝 브리핑 즉시 실행 (스케줄 무관)."""
    from app.entrypoints.scheduler import _run_morning_brief
    background_tasks.add_task(_run_morning_brief)
    return {"status": "triggered", "note": "로그에서 결과 확인: docker compose logs app"}


# ── 가계부 API ─────────────────────────────────────────────────────────────────

class LedgerCreate(BaseModel):
    date: str       # YYYY-MM-DD
    type: str       # 수입 | 지출
    category: str
    amount: int
    memo: str = ""


class AssetCreate(BaseModel):
    name: str
    asset_type: str  # 현금|주식|예금|부동산|기타
    amount: int
    note: str = ""


class AssetUpdate(BaseModel):
    name: str | None = None
    asset_type: str | None = None
    amount: int | None = None
    note: str | None = None


@app.get("/api/ledger/transactions")
async def list_transactions(year: int | None = None, month: int | None = None) -> list[dict]:
    from datetime import date as _date
    today = _date.today()
    y = year or today.year
    m = month or today.month
    prefix = f"{y:04d}-{m:02d}"

    with _session() as session:
        rows = (
            session.query(LedgerTransaction)
            .filter(LedgerTransaction.date.startswith(prefix))
            .order_by(LedgerTransaction.date.desc(), LedgerTransaction.created_at.desc())
            .all()
        )
        return [
            {
                "id": r.id,
                "date": r.date,
                "type": r.type,
                "category": r.category,
                "amount": r.amount,
                "memo": r.memo,
            }
            for r in rows
        ]


@app.post("/api/ledger/transactions", status_code=201)
async def create_transaction(body: LedgerCreate) -> dict:
    with _session() as session:
        row = LedgerTransaction(
            date=body.date,
            type=body.type,
            category=body.category,
            amount=body.amount,
            memo=body.memo,
        )
        session.add(row)
        session.commit()
        return {"id": row.id, "date": row.date, "type": row.type, "category": row.category, "amount": row.amount}


@app.delete("/api/ledger/transactions/{tx_id}")
async def delete_transaction(tx_id: int) -> dict:
    with _session() as session:
        row = session.query(LedgerTransaction).filter_by(id=tx_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        session.delete(row)
        session.commit()
        return {"deleted": tx_id}


@app.get("/api/ledger/summary")
async def ledger_summary(year: int | None = None, month: int | None = None) -> dict:
    """월별 수입/지출 합계 + 카테고리별 집계."""
    from datetime import date as _date
    from collections import defaultdict
    today = _date.today()
    y = year or today.year
    m = month or today.month
    prefix = f"{y:04d}-{m:02d}"

    # 이전 달 계산
    prev_m = m - 1 if m > 1 else 12
    prev_y = y if m > 1 else y - 1
    prev_prefix = f"{prev_y:04d}-{prev_m:02d}"

    with _session() as session:
        def month_rows(pfx: str):
            return (
                session.query(LedgerTransaction)
                .filter(LedgerTransaction.date.startswith(pfx))
                .all()
            )

        curr = month_rows(prefix)
        prev = month_rows(prev_prefix)

        def agg(rows):
            income = sum(r.amount for r in rows if r.type == "수입")
            expense = sum(r.amount for r in rows if r.type == "지출")
            by_cat: dict[str, int] = defaultdict(int)
            for r in rows:
                if r.type == "지출":
                    by_cat[r.category] += r.amount
            return {"income": income, "expense": expense, "by_category": dict(by_cat)}

        return {
            "year": y, "month": m,
            "current": agg(curr),
            "previous": agg(prev),
        }


# ── 자산 API ──────────────────────────────────────────────────────────────────

@app.get("/api/assets")
async def list_assets() -> list[dict]:
    with _session() as session:
        rows = session.query(AssetItem).order_by(AssetItem.asset_type, AssetItem.name).all()
        return [
            {"id": r.id, "name": r.name, "asset_type": r.asset_type, "amount": r.amount, "note": r.note}
            for r in rows
        ]


@app.post("/api/assets", status_code=201)
async def create_asset(body: AssetCreate) -> dict:
    with _session() as session:
        row = AssetItem(name=body.name, asset_type=body.asset_type, amount=body.amount, note=body.note)
        session.add(row)
        session.commit()
        return {"id": row.id, "name": row.name, "asset_type": row.asset_type, "amount": row.amount}


@app.put("/api/assets/{asset_id}")
async def update_asset(asset_id: int, body: AssetUpdate) -> dict:
    with _session() as session:
        row = session.query(AssetItem).filter_by(id=asset_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        if body.name is not None:
            row.name = body.name
        if body.asset_type is not None:
            row.asset_type = body.asset_type
        if body.amount is not None:
            row.amount = body.amount
        if body.note is not None:
            row.note = body.note
        session.commit()
        return {"id": row.id, "name": row.name, "asset_type": row.asset_type, "amount": row.amount}


@app.delete("/api/assets/{asset_id}")
async def delete_asset(asset_id: int) -> dict:
    with _session() as session:
        row = session.query(AssetItem).filter_by(id=asset_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        session.delete(row)
        session.commit()
        return {"deleted": asset_id}


# ── 고정 거래 API ─────────────────────────────────────────────────────────────

class RecurringCreate(BaseModel):
    type: str           # 수입 | 지출
    category: str
    amount: int
    memo: str = ""
    day_of_month: int = 1   # 1~28


class RecurringUpdate(BaseModel):
    is_active: int | None = None
    amount: int | None = None
    memo: str | None = None
    day_of_month: int | None = None


def _apply_recurring_to_month(year: int, month: int) -> int:
    """활성 고정거래를 지정 연월에 적용. 이미 적용된 항목은 건너뜀. 적용 건수 반환."""
    import calendar
    max_day = calendar.monthrange(year, month)[1]
    with _session() as session:
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
    return count


@app.get("/api/recurring")
async def list_recurring() -> list[dict]:
    with _session() as session:
        rows = session.query(RecurringTransaction).order_by(
            RecurringTransaction.type.desc(),   # 수입 먼저
            RecurringTransaction.day_of_month,
        ).all()
        return [
            {"id": r.id, "type": r.type, "category": r.category,
             "amount": r.amount, "memo": r.memo,
             "day_of_month": r.day_of_month, "is_active": r.is_active}
            for r in rows
        ]


@app.post("/api/recurring", status_code=201)
async def create_recurring(body: RecurringCreate) -> dict:
    with _session() as session:
        row = RecurringTransaction(
            type=body.type, category=body.category, amount=body.amount,
            memo=body.memo, day_of_month=max(1, min(28, body.day_of_month)),
        )
        session.add(row)
        session.commit()
        return {"id": row.id}


@app.patch("/api/recurring/{rid}")
async def update_recurring(rid: int, body: RecurringUpdate) -> dict:
    with _session() as session:
        row = session.query(RecurringTransaction).filter_by(id=rid).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        if body.is_active is not None:
            row.is_active = body.is_active
        if body.amount is not None:
            row.amount = body.amount
        if body.memo is not None:
            row.memo = body.memo
        if body.day_of_month is not None:
            row.day_of_month = max(1, min(28, body.day_of_month))
        session.commit()
        return {"ok": True}


@app.delete("/api/recurring/{rid}")
async def delete_recurring(rid: int) -> dict:
    with _session() as session:
        row = session.query(RecurringTransaction).filter_by(id=rid).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        session.delete(row)
        session.commit()
        return {"deleted": rid}


@app.post("/api/recurring/apply")
async def apply_recurring(year: int, month: int) -> dict:
    """지정 월에 활성 고정거래를 일괄 적용."""
    count = _apply_recurring_to_month(year, month)
    return {"applied": count}


# ── 포트폴리오 (보유 종목 평가 + 배당) ────────────────────────────────────────────

_portfolio_cache: dict = {"data": [], "ts": 0.0}
_PORTFOLIO_TTL = 300  # 5분


def _fetch_price_and_dividend(ticker: str, market: str) -> dict:
    """현재가 + 배당 정보 조회. 실패 시 None 반환."""
    out = {"price": None, "dividend_rate": 0.0, "dividend_yield": 0.0,
           "currency": "USD" if market == "US" else "KRW"}
    try:
        import yfinance as yf
        if market == "US":
            t = yf.Ticker(ticker)
            info = t.info
            market_state = info.get("marketState", "CLOSED")  # PRE|REGULAR|POST|CLOSED
            if market_state == "REGULAR":
                # 장중: 실시간 체결가
                out["price"] = info.get("currentPrice") or info.get("regularMarketPrice")
            else:
                # 프리/포스트마켓·장외: 전일 공식 종가 (증권사 기준과 일치)
                out["price"] = info.get("regularMarketPrice") or info.get("previousClose")
            out["prev_close"] = info.get("previousClose")
            out["market_state"] = market_state
            out["dividend_rate"] = float(info.get("dividendRate") or 0)
            out["dividend_yield"] = float(info.get("dividendYield") or 0)
        else:
            # KR 현재가: Naver 모바일 API
            import requests as _req
            r = _req.get(
                f"https://m.stock.naver.com/api/stock/{ticker}/basic",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=5,
            )
            if r.ok:
                data = r.json()
                price_str = data.get("closePrice", "0").replace(",", "")
                out["price"] = float(price_str) if price_str else None
            # KR 배당: yfinance (.KS 코드)
            t = yf.Ticker(f"{ticker}.KS")
            info = t.info
            out["dividend_rate"] = float(info.get("dividendRate") or 0)
            out["dividend_yield"] = float(info.get("dividendYield") or 0)
    except Exception as e:
        logger.debug("price/dividend fetch failed %s[%s]: %s", ticker, market, e)
    return out


@app.get("/api/portfolio")
async def get_portfolio(refresh: bool = False) -> list[dict]:
    """보유 종목 현재 평가금액 + 배당 정보. 모든 금액은 원화(KRW) 기준."""
    import time
    now = time.time()
    if not refresh and now - _portfolio_cache["ts"] < _PORTFOLIO_TTL and _portfolio_cache["data"]:
        return _portfolio_cache["data"]

    # USD/KRW 환율 (매크로 캐시에서, 없으면 기본값)
    usd_krw = _macro_cache.get("data", {}).get("usd_krw", {}).get("price") or 1380.0

    with _session() as session:
        rows = session.query(Watchlist).filter(Watchlist.quantity > 0).all()
        items = [
            (r.ticker, r.market, r.name, float(r.quantity or 0), float(r.avg_price or 0))
            for r in rows
        ]

    result = []
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_fetch_price_and_dividend, t, m): (t, m, n, q, ap)
                   for (t, m, n, q, ap) in items}
        for future in as_completed(futures):
            ticker, market, name, qty, avg_p = futures[future]
            pd_data = future.result()
            price_raw = pd_data["price"]   # US: USD, KR: KRW

            # 현재가 → 원화 환산
            if price_raw is not None:
                price_krw = round(price_raw * usd_krw) if market == "US" else round(price_raw)
            else:
                price_krw = None

            div_rate = pd_data["dividend_rate"]   # 주당 연간 배당 (현지통화)
            # 배당도 원화 환산
            div_rate_krw = round(div_rate * usd_krw) if (div_rate and market == "US") else div_rate

            curr_val = round(price_krw * qty) if price_krw else None   # 원화 평가금액
            cost     = round(avg_p * qty) if avg_p else None            # 원화 매입금액 (사용자 입력 기준)
            gl       = round(curr_val - cost) if (curr_val is not None and cost) else None
            gl_pct   = round(gl / cost * 100, 2) if (gl is not None and cost) else None

            result.append({
                "ticker": ticker,
                "market": market,
                "name": name,
                "quantity": qty,
                "avg_price": avg_p,
                "current_price_orig": price_raw,
                "current_price_krw": price_krw,
                "current_value": curr_val,
                "cost": cost,
                "gain_loss": gl,
                "gain_loss_pct": gl_pct,
                "dividend_rate_orig": div_rate,
                "dividend_rate_krw": div_rate_krw,
                "dividend_yield": pd_data["dividend_yield"],
                "annual_dividend_krw": round(div_rate_krw * qty) if div_rate_krw else 0,
                "currency": pd_data["currency"],
                "market_state": pd_data.get("market_state", ""),
                "prev_close": pd_data.get("prev_close"),
                "usd_krw": usd_krw,
            })

    result.sort(key=lambda x: x["ticker"])
    _portfolio_cache["data"] = result
    _portfolio_cache["ts"] = now
    return result


# ── API 키 설정 ────────────────────────────────────────────────────────────────

def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:7] + "…" + key[-4:]


class ApiKeySet(BaseModel):
    provider: str   # 'anthropic' | 'openai'
    api_key: str


@app.get("/api/settings/api-keys")
async def get_api_keys(user_id: str = "default") -> list[dict]:
    """저장된 API 키 목록 (마스킹된 값)."""
    with _session() as session:
        rows = session.query(UserApiKey).filter_by(user_id=user_id).all()
        stored = {r.provider: r for r in rows}

    result = []
    for provider in ("anthropic", "openai"):
        row = stored.get(provider)
        if row:
            result.append({
                "provider": provider,
                "masked": _mask_key(row.api_key),
                "configured": True,
                "is_active": bool(row.is_active),
                "updated_at": str(row.updated_at)[:16],
                "source": "db",
            })
        else:
            env_key = settings.anthropic_api_key if provider == "anthropic" else ""
            result.append({
                "provider": provider,
                "masked": _mask_key(env_key) if env_key else "",
                "configured": bool(env_key),
                "is_active": bool(env_key),   # .env 키는 항상 활성 상태로 표시
                "updated_at": None,
                "source": "env" if env_key else "none",
            })
    return result


@app.post("/api/settings/api-keys")
async def set_api_key(body: ApiKeySet, user_id: str = "default") -> dict:
    """API 키 저장 (upsert). 저장 시 자동으로 활성화."""
    if body.provider not in ("anthropic", "openai"):
        raise HTTPException(status_code=400, detail="provider must be 'anthropic' or 'openai'")
    if not body.api_key.strip():
        raise HTTPException(status_code=400, detail="api_key is required")

    from datetime import datetime, timezone
    with _session() as session:
        row = session.query(UserApiKey).filter_by(user_id=user_id, provider=body.provider).first()
        if row:
            row.api_key = body.api_key.strip()
            row.is_active = 1
            row.updated_at = datetime.now(timezone.utc)
        else:
            session.add(UserApiKey(
                user_id=user_id, provider=body.provider,
                api_key=body.api_key.strip(), is_active=1,
            ))
        session.commit()
    return {"provider": body.provider, "masked": _mask_key(body.api_key.strip()),
            "configured": True, "is_active": True}


@app.post("/api/settings/api-keys/{provider}/activate")
async def activate_api_key(provider: str, user_id: str = "default") -> dict:
    """저장된 API 키 활성화."""
    with _session() as session:
        row = session.query(UserApiKey).filter_by(user_id=user_id, provider=provider).first()
        if not row:
            raise HTTPException(status_code=404, detail="키가 저장되지 않았습니다. 먼저 키를 저장하세요.")
        row.is_active = 1
        session.commit()
    return {"provider": provider, "is_active": True}


@app.post("/api/settings/api-keys/{provider}/deactivate")
async def deactivate_api_key(provider: str, user_id: str = "default") -> dict:
    """저장된 API 키 비활성화."""
    with _session() as session:
        row = session.query(UserApiKey).filter_by(user_id=user_id, provider=provider).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        row.is_active = 0
        session.commit()
    return {"provider": provider, "is_active": False}


@app.delete("/api/settings/api-keys/{provider}")
async def delete_api_key(provider: str, user_id: str = "default") -> dict:
    """저장된 API 키 삭제."""
    with _session() as session:
        row = session.query(UserApiKey).filter_by(user_id=user_id, provider=provider).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        session.delete(row)
        session.commit()
    return {"deleted": provider}
