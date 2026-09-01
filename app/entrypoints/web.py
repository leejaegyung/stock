"""
Layer 6 — FastAPI web entrypoint.
Scheduler starts in lifespan; nginx proxies /api/* to this app.
"""

import json
import logging
import logging.config
import os
import re
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
from app.db.models import (
    AnalysisReport,
    AssetItem,
    LedgerTransaction,
    NetWorthSnapshot,
    NewsItem,
    RecurringTransaction,
    UserApiKey,
    Watchlist,
    create_all_tables,
)
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
    # DeepL 키가 있으면 번역 유틸이 쓸 수 있도록 프로세스 환경에 노출
    if settings.deepl_api_key:
        os.environ.setdefault("DEEPL_API_KEY", settings.deepl_api_key)
    start_scheduler()
    _kick_news_backfill()
    yield
    stop_scheduler()


def _kick_news_backfill() -> None:
    """기동 시 밀린 외신 뉴스 번역을 백그라운드로 처리 (요청 블로킹 없음).

    DeepL 키가 있으면 한도가 넉넉하니 여러 배치를 이어서, 없으면 1배치만.
    """
    import threading

    batches = 12 if settings.deepl_api_key else 1

    def _run():
        try:
            for _ in range(batches):
                if _translate_pending_news(max_items=20) == 0:
                    break
        except Exception as e:
            logger.warning("news backfill failed: %s", e)

    threading.Thread(target=_run, daemon=True).start()


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
        _bust_market_caches()
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
        _bust_market_caches()
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
        _bust_market_caches()
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
                "metrics": _row_metrics(r),
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
            "metrics": _row_metrics(row),
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


# ── 분석 구조화 지표 ──────────────────────────────────────────────────────────


def _report_metrics(result: dict) -> dict:
    """analyze_stock_algo 결과에서 카드 UI용 구조화 지표를 뽑아낸다."""
    scores = result.get("scores", {}) or {}
    quant = result.get("quant_risk", {}) or {}
    ta = result.get("technical", {}) or {}
    advice = result.get("advice", {}) or {}
    bulls = result.get("bull_signals", []) or []
    bears = result.get("bear_signals", []) or []
    conf = result.get("confidence", {}) or {}
    price = ta.get("price")
    ev = quant.get("ev")
    target = round(price * (1 + ev / 100), 2) if (price and ev is not None) else None
    return {
        "score_total": scores.get("total"),
        "confidence_score": conf.get("score"),
        "confidence_grade": conf.get("grade"),
        "confidence_factors": conf.get("factors"),
        "confidence_reasons": conf.get("reasons"),
        "confidence_hints": conf.get("hints"),
        "score_technical": scores.get("technical"),
        "score_fundamental": scores.get("fundamental"),
        "score_macro": scores.get("macro"),
        "score_news": scores.get("news"),
        "ev": ev,
        "half_kelly": quant.get("half_kelly"),
        "valuation": quant.get("valuation"),
        "cashflow_pattern": quant.get("cashflow_pattern"),
        "price": price,
        "target": target,
        "gap_pct": round((target / price - 1) * 100, 1) if (target and price) else None,
        "trade_plan": result.get("trade_plan"),
        "bull_count": len(bulls),
        "bear_count": len(bears),
        "key_reasons": (advice.get("key_reasons") or [])[:3],
        "warnings": (advice.get("warnings") or [])[:2],
        "duration_sec": result.get("_duration_sec"),
    }


def _parse_metrics_md(md: str) -> dict:
    """구 보고서(metrics_json 없음) — report_md 텍스트에서 지표 역추출."""
    md = md or ""
    out: dict[str, Any] = {}
    m = re.search(r"종합점수:\s*(\d+)/100", md)
    if m:
        out["score_total"] = int(m.group(1))
    m = re.search(
        r"기술\s*(\d+)/30\s*·\s*펀더멘털\s*(\d+)/40\s*·\s*거시\s*(\d+)/20\s*·\s*뉴스\s*(\d+)/10", md
    )
    if m:
        out["score_technical"], out["score_fundamental"], out["score_macro"], out["score_news"] = (
            int(x) for x in m.groups()
        )
    m = re.search(r"기대값:\s*([+-]?[\d.]+)%\s*\|\s*켈리 권장비중:\s*([\d.]+)%", md)
    if m:
        out["ev"] = float(m.group(1))
        out["half_kelly"] = float(m.group(2))
    m = re.search(r"현재가\s*([\d,]+\.?\d*)", md)
    if m:
        out["price"] = float(m.group(1).replace(",", ""))
    m = re.search(r"밸류에이션:\s*(.+)", md)
    if m:
        out["valuation"] = m.group(1).strip()
    m = re.search(r"확신도:\s*([상중하])\s*\((\d+)/100\)", md)
    if m:
        out["confidence_grade"] = m.group(1)
        out["confidence_score"] = int(m.group(2))
    facs = re.findall(r"([가-힣 ]+?)\s+(\d+)/(\d+)", md)
    known = {"데이터 커버리지", "신호 일치도", "신호 우위", "점수 확신", "뉴스 근거"}
    parsed_fac = [
        {"name": n.strip(), "score": int(s), "max": int(mx)}
        for (n, s, mx) in facs
        if n.strip() in known
    ]
    if parsed_fac:
        out["confidence_factors"] = parsed_fac
    bull = re.findall(r"^\s*-\s*🟢\s*(.+?)\s*$", md, re.M)
    bear = re.findall(r"^\s*-\s*🔴\s*(.+?)\s*$", md, re.M)
    out["bull_count"] = len(bull)
    out["bear_count"] = len(bear)
    out["key_reasons"] = (bull or bear)[:3]
    out["warnings"] = bear[:2]
    price, ev = out.get("price"), out.get("ev")
    if price and ev is not None:
        tgt = round(price * (1 + ev / 100), 2)
        out["target"] = tgt
        out["gap_pct"] = round((tgt / price - 1) * 100, 1)
    return out


def _row_metrics(row: AnalysisReport) -> dict:
    if getattr(row, "metrics_json", ""):
        try:
            return json.loads(row.metrics_json)
        except (ValueError, TypeError):
            pass
    return _parse_metrics_md(row.report_md)


# ── Analysis Trigger ──────────────────────────────────────────────────────────

def _bg_analyze(ticker: str, market: str, date_str: str) -> None:
    import hashlib
    from datetime import datetime as _dt
    from app.core.algo_pipeline import analyze_stock_algo, classify_news_algo

    import time as _t
    _t0 = _t.perf_counter()
    result = analyze_stock_algo(ticker, market, date_str)
    result["_duration_sec"] = round(_t.perf_counter() - _t0, 1)
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
            metrics_json=json.dumps(_report_metrics(result), ensure_ascii=False),
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
                    metrics_json=json.dumps(_report_metrics(result), ensure_ascii=False),
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
_MACRO_TTL = 1800  # 30분 — 대시보드 지표는 실시간일 필요 없음
_macro_refreshing = {"v": False}

_cape_cache: dict = {"v": None, "ts": 0.0}
_CAPE_TTL = 12 * 3600  # CAPE 는 하루 단위로 변함

_MACRO_SYMBOLS = {
    "sp500": "^GSPC", "nasdaq": "^IXIC", "vix": "^VIX", "dxy": "DX-Y.NYB",
    "bonds_10y": "^TNX", "gold": "GC=F", "oil_wti": "CL=F", "usd_krw": "KRW=X",
}


def _fast_us_macro() -> dict:
    """yf.download 배치 1회로 미국 거시지표 + 환율. .info 루프 대비 5~8배 빠름."""
    import time

    import yfinance as yf

    out: dict = {}
    try:
        df = yf.download(
            list(_MACRO_SYMBOLS.values()), period="5d",
            progress=False, group_by="ticker", threads=True, auto_adjust=False,
        )
        multi = hasattr(df.columns, "levels")
        for name, sym in _MACRO_SYMBOLS.items():
            try:
                closes = (df[sym]["Close"] if multi else df["Close"]).dropna()
                if len(closes) == 0:
                    continue
                last = float(closes.iloc[-1])
                prev = float(closes.iloc[-2]) if len(closes) > 1 else last
                out[name] = {
                    "price": round(last, 2),
                    "change_pct": round((last / prev - 1) * 100, 2) if prev else None,
                    "source": "yfinance",
                }
            except Exception:
                continue
    except Exception as e:
        logger.warning("fast macro batch failed: %s", e)

    # CAPE — 12시간 캐시
    if not _cape_cache["v"] or time.time() - _cape_cache["ts"] > _CAPE_TTL:
        try:
            import requests
            from bs4 import BeautifulSoup

            r = requests.get(
                "https://www.multpl.com/shiller-pe",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=6,
            )
            if r.ok:
                el = BeautifulSoup(r.text, "html.parser").select_one("#current")
                if el:
                    m = re.search(r"(\d+\.\d+)", el.get_text())
                    if m:
                        _cape_cache["v"] = {"value": float(m.group(1)), "source": "multpl.com"}
                        _cape_cache["ts"] = time.time()
        except Exception as e:
            logger.debug("CAPE fetch failed: %s", e)
    if _cape_cache["v"]:
        out["cape"] = _cape_cache["v"]
    return out


def _refresh_macro() -> dict:
    """미국(배치) + 한국(Naver) 거시지표 동시 수집."""
    from concurrent.futures import ThreadPoolExecutor

    def _kr():
        try:
            from app.core.datasources.kr import KRDataSource
            d = KRDataSource().get_macro_data()
            return {k: d[k] for k in ("kospi", "kosdaq") if k in d}
        except Exception as e:
            logger.warning("KR macro failed: %s", e)
            return {}

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_us = pool.submit(_fast_us_macro)
        f_kr = pool.submit(_kr)
        us, kr = f_us.result(), f_kr.result()
    return {**kr, **us}


def _macro_bg_refresh() -> None:
    """중복 방지하며 백그라운드에서 거시지표 갱신."""
    import threading
    import time

    if _macro_refreshing["v"]:
        return
    _macro_refreshing["v"] = True

    def _bg():
        try:
            d = _refresh_macro()
            if d:
                _macro_cache["data"] = d
                _macro_cache["ts"] = time.time()
        finally:
            _macro_refreshing["v"] = False

    threading.Thread(target=_bg, daemon=True).start()


def _get_macro_cached(force: bool = False) -> dict:
    """
    절대 요청을 블로킹하지 않는다(force=True 제외).
    - 신선하면 캐시 반환
    - 오래됐거나 비어있으면: 캐시(있으면) 즉시 반환 + 백그라운드 갱신
    - force=True(프리워밍/스케줄러): 동기 갱신
    """
    import time

    now = time.time()
    fresh = _macro_cache["data"] and now - _macro_cache["ts"] < _MACRO_TTL

    if force:
        try:
            d = _refresh_macro()
            if d:
                _macro_cache["data"] = d
                _macro_cache["ts"] = now
        except Exception as e:
            logger.warning("macro refresh failed: %s", e)
        return _macro_cache["data"] or {}

    if not fresh:
        _macro_bg_refresh()
    return _macro_cache["data"] or {}


@app.get("/api/macro")
async def macro_data() -> dict:
    """거시지표만 별도 조회 (티커 바 지연 로딩용)."""
    return _get_macro_cached()


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
                    "metrics": _row_metrics(row),
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


def _translate_pending_news(max_items: int = 8) -> int:
    """번역 안 된 외신 뉴스를 한국어로 채운다. 번역 건수 반환.

    한국어 원문은 lang='ko' 로 확정, 번역 실패분은 lang 을 비워둬 다음 주기에 재시도.
    """
    from sqlalchemy import or_

    from app.core.translate import backoff_active, detect_lang, translate_to_ko

    if backoff_active():
        return 0

    done = 0
    with _session() as session:
        rows = (
            session.query(NewsItem)
            .filter(or_(NewsItem.lang.is_(None), NewsItem.lang == ""))
            .order_by(NewsItem.published_at.desc())
            .limit(max_items)
            .all()
        )
        for r in rows:
            head = (r.headline or "").strip()
            if not head:
                r.lang = "unknown"
                continue
            if detect_lang(head) == "ko":
                r.lang = "ko"
                continue
            hk, ok1 = translate_to_ko(head)
            sk, ok2 = translate_to_ko(r.summary or "") if r.summary else ("", False)
            if ok1:
                r.headline_ko = hk
                r.summary_ko = sk if ok2 else r.summary_ko
                r.lang = "en"
                done += 1
            # 실패 시 lang 그대로(None) → 다음 주기에 재시도
        session.commit()
    return done


@app.get("/api/news")
async def recent_news(limit: int = 20) -> list[dict]:
    """
    최근 뉴스 — 외신은 한국어 번역본 우선, 원문 링크·원문 텍스트 함께 제공.
    번역은 스케줄러(6분 주기)와 기동 시 백필이 채운다 — 이 응답은 DB만 읽는다.
    """
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
                "headline": r.headline_ko or r.headline,
                "summary": r.summary_ko or r.summary,
                "headline_orig": r.headline,
                "summary_orig": r.summary,
                "translated": bool(r.headline_ko),
                "lang": r.lang or "",
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
_PORTFOLIO_TTL = 90  # 1.5분 — 관심목록 시세 준실시간 갱신 (프런트 60초 폴링)

_pxdf_cache: dict = {}      # "ticker:market:period" -> (ts, DataFrame)
_PXDF_TTL = 1800           # 30분 — history/analytics/backtest/sparkline 공용

_div_cache: dict = {}       # "ticker:market" -> (ts, {rate, yield, months})
_DIV_TTL = 24 * 3600       # 배당 정보는 하루 1회면 충분


def _price_df(ticker: str, market: str, period: str = "1y"):
    """가격 시계열 통합 캐시 — history·analytics·backtest·스파크라인이 공유."""
    import time

    from app.core.algo_pipeline import _fetch_price_df

    key = f"{ticker}:{market}:{period}"
    now = time.time()
    hit = _pxdf_cache.get(key)
    if hit and now - hit[0] < _PXDF_TTL:
        return hit[1]
    df = _fetch_price_df(ticker, market, period=period)
    _pxdf_cache[key] = (now, df)
    return df


def _dividends(ticker: str, market: str) -> dict:
    """주당 배당·수익률·지급월 — 24시간 캐시."""
    import time

    key = f"{ticker}:{market}"
    now = time.time()
    hit = _div_cache.get(key)
    if hit and now - hit[0] < _DIV_TTL:
        return hit[1]
    out = {"rate": 0.0, "yield": 0.0, "months": []}
    try:
        import yfinance as yf

        yft = yf.Ticker(ticker if market == "US" else f"{ticker}.KS")
        try:
            divs = yft.dividends
            if divs is not None and len(divs):
                out["months"] = sorted({int(d.month) for d in divs.tail(6).index})
        except Exception:
            pass
        info = yft.info or {}
        out["rate"] = float(info.get("dividendRate") or 0)
        _dy = float(info.get("dividendYield") or 0)
        out["yield"] = _dy / 100 if _dy > 1 else _dy
    except Exception as e:
        logger.debug("dividend fetch failed %s: %s", key, e)
    _div_cache[key] = (now, out)
    return out


def _us_market_state() -> str:
    """ET 근사로 미국 장 상태 추정 (API 호출 없이, 표시용)."""
    from datetime import datetime, timedelta, timezone

    et = datetime.now(timezone.utc) - timedelta(hours=5)
    if et.weekday() >= 5:
        return "CLOSED"
    hm = et.hour * 60 + et.minute
    if hm < 4 * 60:
        return "CLOSED"
    if hm < 9 * 60 + 30:
        return "PRE"
    if hm < 16 * 60:
        return "REGULAR"
    if hm < 20 * 60:
        return "POST"
    return "CLOSED"


def _kr_market_state() -> str:
    """KST 근사로 한국(KRX) 장 상태 추정 (API 호출 없이, 표시용). 공휴일 미반영."""
    from datetime import datetime, timedelta, timezone

    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    if kst.weekday() >= 5:
        return "CLOSED"
    hm = kst.hour * 60 + kst.minute
    if hm < 8 * 60:
        return "CLOSED"
    if hm < 9 * 60:                 # 08:00~09:00 장 시작 동시호가
        return "PRE"
    if hm < 15 * 60 + 30:           # 09:00~15:30 정규장
        return "REGULAR"
    if hm < 18 * 60:               # 15:30~18:00 시간외
        return "POST"
    return "CLOSED"


def _batch_prices(items: list[tuple]) -> dict:
    """관심목록 시세 배치 조회.

    1) 일봉 1개월 다운로드 → 전일종가·스파크라인, 장 마감 시 현재가
    2) 1분봉 당일 다운로드 → 장중 현재가로 덮어씀 (yfinance 기준 ≈15분 지연)
    """
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timedelta, timezone

    us = [t for (t, m, *_) in items if m == "US"]
    kr = [f"{t}.KS" for (t, m, *_) in items if m == "KR"]
    px: dict = {}
    et_today = (datetime.now(timezone.utc) - timedelta(hours=5)).date()

    def _daily(syms):
        if not syms:
            return
        try:
            df = yf.download(
                syms, period="1mo", interval="1d", progress=False,
                group_by="ticker", threads=True, auto_adjust=False,
            )
            multi = hasattr(df.columns, "levels")
            for s in syms:
                try:
                    c = (df[s]["Close"] if multi else df["Close"]).dropna()
                    if not len(c):
                        continue
                    last_is_today = c.index[-1].date() >= et_today
                    prev = (
                        float(c.iloc[-2])
                        if (last_is_today and len(c) > 1)
                        else float(c.iloc[-1])
                    )
                    px[s] = {
                        "last": float(c.iloc[-1]),
                        "prev": prev,
                        "spark": [round(float(x), 4) for x in c.tolist()][-30:],
                    }
                except Exception:
                    continue
        except Exception as e:
            logger.warning("batch daily price failed (%s): %s", syms, e)

    def _intraday(syms):
        if not syms:
            return
        try:
            df = yf.download(
                syms, period="1d", interval="1m", progress=False,
                group_by="ticker", threads=True, auto_adjust=False,
            )
            multi = hasattr(df.columns, "levels")
            for s in syms:
                try:
                    c = (df[s]["Close"] if multi else df["Close"]).dropna()
                    if len(c) and s in px:
                        px[s]["last"] = float(c.iloc[-1])
                except Exception:
                    continue
        except Exception as e:
            logger.debug("batch intraday price failed (%s): %s", syms, e)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(_daily, [us, kr]))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(_intraday, [us, kr]))
    return px


def _bust_market_caches() -> None:
    """관심종목·보유수량 변경 시 시세/분석 캐시를 무효화 (다음 조회에서 재계산)."""
    _portfolio_cache["data"] = []
    _portfolio_cache["ts"] = 0.0
    _analytics_cache.clear()


@app.get("/api/portfolio")
async def get_portfolio(refresh: bool = False) -> list[dict]:
    """관심종목 전체의 현재가·스파크라인·배당 + (보유 시) 평가금액. 원화(KRW) 기준."""
    import time
    now = time.time()
    if not refresh and now - _portfolio_cache["ts"] < _PORTFOLIO_TTL and _portfolio_cache["data"]:
        return _portfolio_cache["data"]

    # USD/KRW 환율 (매크로 캐시에서, 없으면 기본값)
    usd_krw = _macro_cache.get("data", {}).get("usd_krw", {}).get("price") or 1380.0

    with _session() as session:
        rows = session.query(Watchlist).all()   # 미보유(수량 0) 종목도 현재가는 채운다
        items = [
            (r.ticker, r.market, r.name, float(r.quantity or 0), float(r.avg_price or 0))
            for r in rows
        ]

    if not items:
        _portfolio_cache["data"] = []
        _portfolio_cache["ts"] = now
        return []

    px = _batch_prices(items)
    us_state = _us_market_state()
    kr_state = _kr_market_state()

    from concurrent.futures import ThreadPoolExecutor

    tm_keys = [(t, m) for (t, m, *_) in items]
    with ThreadPoolExecutor(max_workers=4) as pool:
        div_map = dict(zip(tm_keys, pool.map(lambda tm: _dividends(*tm), tm_keys), strict=False))

    result = []
    for (ticker, market, name, qty, avg_p) in items:
        sym = ticker if market == "US" else f"{ticker}.KS"
        lp = px.get(sym)
        if lp is None:
            df = _price_df(ticker, market, "1y")
            if df is not None and not getattr(df, "empty", True) and "Close" in df.columns:
                cl = df["Close"].dropna()
                if len(cl):
                    lp = {
                        "last": float(cl.iloc[-1]),
                        "prev": float(cl.iloc[-2]) if len(cl) > 1 else float(cl.iloc[-1]),
                        "spark": [round(float(x), 4) for x in cl.tolist()][-30:],
                    }

        price_raw = lp["last"] if lp else None
        prev_close_raw = lp["prev"] if lp else None
        div = div_map.get((ticker, market), {"rate": 0.0, "yield": 0.0, "months": []})

        pd_data = {
            "price": price_raw,
            "prev_close": prev_close_raw,
            "market_state": us_state if market == "US" else kr_state,
            "dividend_rate": div["rate"],
            "dividend_yield": div["yield"],
            "dividend_months": div["months"],
            "sparkline": lp["spark"] if lp else [],
            "currency": "USD" if market == "US" else "KRW",
        }
        # 현재가 → 원화 환산
        price_krw = None
        if price_raw is not None:
            price_krw = round(price_raw * usd_krw) if market == "US" else round(price_raw)

        div_rate = pd_data["dividend_rate"]   # 주당 연간 배당 (현지통화)
        div_rate_krw = round(div_rate * usd_krw) if (div_rate and market == "US") else div_rate

        curr_val = round(price_krw * qty) if (price_krw and qty > 0) else None
        cost = round(avg_p * qty) if (avg_p and qty > 0) else None
        gl = round(curr_val - cost) if (curr_val is not None and cost) else None
        gl_pct = round(gl / cost * 100, 2) if (gl is not None and cost) else None

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
            "sparkline": pd_data.get("sparkline", []),
            "dividend_months": pd_data.get("dividend_months", []),
            "usd_krw": usd_krw,
        })

    result.sort(key=lambda x: x["ticker"])
    _portfolio_cache["data"] = result
    _portfolio_cache["ts"] = now

    # 순자산 스냅샷 기록 (하루 1건 upsert)
    try:
        stock_value = sum(p["current_value"] or 0 for p in result)
        stock_cost = sum(p["cost"] or 0 for p in result)
        with _session() as s:
            assets_total = sum(a.amount or 0 for a in s.query(AssetItem).all())
        _record_networth_snapshot(assets_total, stock_value, stock_cost)
    except Exception as e:
        logger.debug("networth snapshot skipped: %s", e)

    return result


def _record_networth_snapshot(total_assets: int, stock_value: int, stock_cost: int) -> None:
    today = date.today().isoformat()
    nw = int(total_assets) + int(stock_value)
    with _session() as s:
        row = s.query(NetWorthSnapshot).filter_by(date=today).first()
        if row:
            row.total_assets = int(total_assets)
            row.stock_value = int(stock_value)
            row.stock_cost = int(stock_cost)
            row.net_worth = nw
        else:
            s.add(
                NetWorthSnapshot(
                    date=today,
                    total_assets=int(total_assets),
                    stock_value=int(stock_value),
                    stock_cost=int(stock_cost),
                    net_worth=nw,
                )
            )
        s.commit()


@app.get("/api/networth/history")
async def networth_history(days: int = 180) -> list[dict]:
    """순자산 추이 (일별 스냅샷)."""
    with _session() as s:
        rows = s.query(NetWorthSnapshot).order_by(NetWorthSnapshot.date).all()
        out = [
            {
                "date": r.date,
                "net_worth": r.net_worth,
                "total_assets": r.total_assets,
                "stock_value": r.stock_value,
                "stock_cost": r.stock_cost,
            }
            for r in rows
        ]
    return out[-days:]



# ── 가격 히스토리 (차트·스파크라인·OHLC) ─────────────────────────────────────

_history_cache: dict = {}
_HISTORY_TTL = 600  # 10분

_RANGE_MAP = {
    "1일": "5d",
    "1주": "5d",
    "1개월": "1mo",
    "3개월": "3mo",
    "1년": "1y",
    "전체": "max",
    "1d": "5d",
    "1w": "5d",
    "5d": "5d",
    "1mo": "1mo",
    "1m": "1mo",
    "3mo": "3mo",
    "3m": "3mo",
    "6mo": "6mo",
    "1y": "1y",
    "max": "max",
    "all": "max",
}
_RANGE_WINDOW = {"5d": 6, "1mo": 22, "3mo": 66, "6mo": 132, "1y": 252, "max": 100000}


@app.get("/api/history/{ticker}")
async def price_history(ticker: str, market: str = "US", range: str = "3개월") -> dict:
    """종가·OHLC·거래량·MA20·52주 고저. 차트/스파크라인 공용."""
    import time

    t, m = ticker.upper(), market.upper()
    rng = _RANGE_MAP.get(range, "3mo")
    ck = f"{t}:{m}:{rng}"
    now = time.time()
    hit = _history_cache.get(ck)
    if hit and now - hit[0] < _HISTORY_TTL:
        return hit[1]

    period = "max" if rng == "max" else ("2y" if rng == "1y" else "1y")
    try:
        df = _price_df(t, m, period=period)
    except Exception as e:
        logger.warning("history fetch failed %s: %s", t, e)
        df = None

    empty = {
        "ticker": t,
        "market": m,
        "range": rng,
        "points": [],
        "ma20": [],
        "week52_high": None,
        "week52_low": None,
        "last_close": None,
        "prev_close": None,
        "day_open": None,
        "day_high": None,
        "day_low": None,
        "day_volume": None,
        "change_pct": None,
    }
    if df is None or getattr(df, "empty", True):
        return empty

    try:
        import math

        need = {"Open", "High", "Low", "Close", "Volume"}
        if not need.issubset(set(df.columns)):
            return empty

        # 진행 중인 당일 등 OHLC 가 비어 있는 꼬리 행 제거 (차트 급락·시고저 공백 방지)
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if df.empty:
            return empty

        ma20_full = df["Close"].rolling(20).mean()
        window = _RANGE_WINDOW.get(rng, 66)
        wdf = df if rng == "max" else df.tail(window)
        ma_w = ma20_full.reindex(wdf.index)

        rows = []
        for idx, (dt, r) in enumerate(wdf.iterrows()):
            rows.append(
                {
                    "d": dt.strftime("%Y-%m-%d"),
                    "o": round(float(r["Open"]), 4),
                    "h": round(float(r["High"]), 4),
                    "l": round(float(r["Low"]), 4),
                    "c": round(float(r["Close"]), 4),
                    "v": int(r["Volume"]) if not math.isnan(r["Volume"]) else 0,
                    "ma": (
                        round(float(ma_w.iloc[idx]), 4) if not math.isnan(ma_w.iloc[idx]) else None
                    ),
                }
            )

        # 다운샘플 (최대 ~130 포인트)
        if len(rows) > 130:
            step = math.ceil(len(rows) / 130)
            rows = rows[::step] + ([rows[-1]] if (len(rows) - 1) % step else [])

        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        last_c = float(last["Close"])
        prev_c = float(prev["Close"])
        y252 = df.tail(252)
        out = {
            "ticker": t,
            "market": m,
            "range": rng,
            "points": rows,
            "ma20": [x["ma"] for x in rows],
            "week52_high": round(float(y252["High"].max()), 4),
            "week52_low": round(float(y252["Low"].min()), 4),
            "last_close": round(last_c, 4),
            "prev_close": round(prev_c, 4),
            "day_open": round(float(last["Open"]), 4),
            "day_high": round(float(last["High"]), 4),
            "day_low": round(float(last["Low"]), 4),
            "day_volume": int(last["Volume"]) if not math.isnan(float(last["Volume"])) else 0,
            "change_pct": round((last_c / prev_c - 1) * 100, 2) if prev_c else None,
        }
        _history_cache[ck] = (now, out)
        return out
    except Exception as e:
        logger.warning("history build failed %s: %s", t, e)
        return empty


# ── 포트폴리오 계량 분석 (gs-quant 스타일) ───────────────────────────────────

_BENCHMARK = "SPY"  # 글로벌 주식 벤치마크
_analytics_cache: dict = {}
_ANALYTICS_TTL = 1800  # 30분


def _rf_annual() -> float:
    """무위험수익률 추정: 미국 10Y 금리(캐시), 없으면 4%."""
    try:
        v = _get_macro_cached().get("bonds_10y", {}).get("price")
        if v:
            return float(v) / 100.0
    except Exception:
        pass
    return 0.04


def _aligned_returns(tickers: list[tuple[str, str]], period: str = "1y"):
    """
    보유 종목 + 벤치마크의 일간 종가를 받아 공통 날짜로 정렬한 수익률을 반환.
    returns (labels, returns_matrix, bench_returns) — 실패 종목은 제외.
    """
    import pandas as pd

    series: dict[str, pd.Series] = {}
    from concurrent.futures import ThreadPoolExecutor

    def _one(tm):
        t, m = tm
        try:
            df = _price_df(t, m, period=period)
            if df is not None and not getattr(df, "empty", True) and "Close" in df.columns:
                return t, df["Close"].dropna()
        except Exception as e:
            logger.debug("analytics price fetch failed %s: %s", t, e)
        return t, None

    jobs = list(tickers) + [(_BENCHMARK, "US")]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for t, s in pool.map(_one, jobs):
            if s is not None and len(s) > 30:
                series[t] = s

    if _BENCHMARK not in series or len(series) < 2:
        return [], [], []

    frame = pd.concat(series, axis=1).dropna()
    if len(frame) < 30:
        return [], [], []

    rets = frame.pct_change().dropna()
    bench = [float(x) for x in rets[_BENCHMARK].tolist()]
    labels = [t for (t, _) in tickers if t in rets.columns]
    matrix = [[float(x) for x in rets[t].tolist()] for t in labels]
    return labels, matrix, bench


@app.get("/api/portfolio/analytics")
async def portfolio_analytics(refresh: bool = False, period: str = "1y") -> dict:
    """보유 포트폴리오의 위험·분산·비중 최적화 분석."""
    import time

    from app.core import quant

    ck = f"analytics:{period}"
    now = time.time()
    hit = _analytics_cache.get(ck)
    if not refresh and hit and now - hit[0] < _ANALYTICS_TTL:
        return hit[1]

    with _session() as session:
        rows = session.query(Watchlist).filter(Watchlist.quantity > 0).all()
        holds = [
            (r.ticker, r.market, float(r.quantity or 0), float(r.avg_price or 0)) for r in rows
        ]

    if len(holds) < 2:
        return {
            "ok": False,
            "reason": "보유 종목이 2개 이상이어야 분석할 수 있습니다.",
            "holdings": len(holds),
        }

    tickers = [(t, m) for (t, m, _, _) in holds]
    labels, matrix, bench = _aligned_returns(tickers, period)
    if not labels or len(labels) < 2:
        return {
            "ok": False,
            "reason": "가격 시계열을 충분히 확보하지 못했습니다.",
            "holdings": len(holds),
        }

    # 현재 비중 (평가금액 기준) — /api/portfolio 재사용
    pf = await get_portfolio()
    val_by_ticker = {p["ticker"]: (p.get("current_value") or 0) for p in pf}
    raw_w = [val_by_ticker.get(t, 0) for t in labels]
    tot = sum(raw_w) or 1.0
    cur_w = [w / tot for w in raw_w]
    if sum(cur_w) == 0:
        cur_w = quant.equal_weights(len(labels))

    rf = _rf_annual()
    rba = {t: matrix[i] for i, t in enumerate(labels)}

    def _scheme(name, w):
        pr = quant.portfolio_returns(matrix, w)
        m = quant.portfolio_metrics(pr, rf)
        m["diversification_ratio"] = round(quant.diversification_ratio(matrix, w), 2)
        m["effective_holdings"] = round(quant.effective_holdings(w), 2)
        m["weights"] = {labels[i]: round(w[i], 4) for i in range(len(labels))}
        m["risk_contributions"] = {
            labels[i]: round(rc, 4) for i, rc in enumerate(quant.risk_contributions(matrix, w))
        }
        m["name"] = name
        return m

    schemes = {
        "current": _scheme("현재 비중", cur_w),
        "equal": _scheme("동일 비중", quant.equal_weights(len(labels))),
        "inverse_vol": _scheme("역변동성", quant.inverse_vol_weights(matrix)),
        "risk_parity": _scheme("리스크 패리티", quant.risk_parity_weights(matrix)),
        "min_variance": _scheme("최소 분산", quant.min_variance_weights(matrix)),
    }

    per_asset = []
    for i, t in enumerate(labels):
        per_asset.append(
            {
                "ticker": t,
                "weight": round(cur_w[i], 4),
                "volatility": round(quant.annualized_volatility(matrix[i]), 4),
                "beta": round(quant.beta(matrix[i], bench), 2),
                "sharpe": round(quant.sharpe_ratio(matrix[i], rf), 2),
                "max_drawdown": round(quant.max_drawdown(matrix[i]), 4),
                "corr_benchmark": round(quant.correlation(matrix[i], bench), 2),
            }
        )

    out = {
        "ok": True,
        "period": period,
        "as_of": date.today().isoformat(),
        "benchmark": _BENCHMARK,
        "rf_annual": round(rf, 4),
        "labels": labels,
        "observations": len(bench),
        "current": schemes["current"],
        "schemes": schemes,
        "benchmark_metrics": quant.portfolio_metrics(bench, rf),
        "per_asset": per_asset,
        "correlation": quant.correlation_matrix(rba),
        "avg_correlation": round(quant.average_pairwise_correlation(rba), 2),
        "portfolio_beta": round(
            sum(cur_w[i] * quant.beta(matrix[i], bench) for i in range(len(labels))), 2
        ),
        "concentration_hhi": round(quant.herfindahl_index(cur_w), 3),
    }
    _analytics_cache[ck] = (now, out)
    return out


class BacktestReq(BaseModel):
    schemes: list[str] = ["current", "equal", "risk_parity"]
    period: str = "1y"
    lookback_days: int = 63
    rebalance_days: int = 21


@app.post("/api/backtest")
async def run_backtest(body: BacktestReq) -> dict:
    """보유 종목으로 여러 비중 전략을 주기적 리밸런싱 백테스트."""
    from app.core import quant

    with _session() as session:
        rows = session.query(Watchlist).filter(Watchlist.quantity > 0).all()
        holds = [(r.ticker, r.market) for r in rows]

    if len(holds) < 2:
        return {"ok": False, "reason": "보유 종목이 2개 이상이어야 백테스트할 수 있습니다."}

    labels, matrix, bench = _aligned_returns(holds, body.period)
    if not labels or len(labels) < 2:
        return {"ok": False, "reason": "가격 시계열을 확보하지 못했습니다."}

    pf = await get_portfolio()
    val = {p["ticker"]: (p.get("current_value") or 0) for p in pf}
    raw = [val.get(t, 0) for t in labels]
    tot = sum(raw) or 1.0
    cur_w = [w / tot for w in raw] if tot else quant.equal_weights(len(labels))

    rf = _rf_annual()
    valid = [
        s
        for s in body.schemes
        if s in ("current", "equal", "inverse_vol", "risk_parity", "min_variance")
    ]
    results = []
    for s in valid or ["current", "equal", "risk_parity"]:
        r = quant.backtest_rebalance(
            matrix,
            scheme=s,
            current_weights=cur_w,
            lookback=max(20, body.lookback_days),
            rebalance_every=max(5, body.rebalance_days),
            rf_annual=rf,
        )
        # equity 다운샘플 (최대 120 포인트)
        eq = r["equity"]
        if len(eq) > 120:
            step = (len(eq) + 119) // 120
            eq = eq[::step] + ([eq[-1]] if (len(eq) - 1) % step else [])
        results.append(
            {
                "scheme": s,
                "name": {
                    "current": "현재 비중",
                    "equal": "동일 비중",
                    "inverse_vol": "역변동성",
                    "risk_parity": "리스크 패리티",
                    "min_variance": "최소 분산",
                }.get(s, s),
                "equity": eq,
                "metrics": r["metrics"],
                "final_weights": {labels[i]: r["final_weights"][i] for i in range(len(labels))},
            }
        )

    # 벤치마크(SPY) 비교 — 전략과 동일 구간으로 정렬
    _lb = max(20, body.lookback_days)
    _bt_start = min(_lb, max(0, len(bench) - 1))
    bench_eq = quant.equity_curve(bench[_bt_start:])
    if len(bench_eq) > 120:
        step = (len(bench_eq) + 119) // 120
        bench_eq = bench_eq[::step] + ([bench_eq[-1]] if (len(bench_eq) - 1) % step else [])

    return {
        "ok": True,
        "period": body.period,
        "labels": labels,
        "lookback_days": body.lookback_days,
        "rebalance_days": body.rebalance_days,
        "results": results,
        "benchmark": {
            "ticker": _BENCHMARK,
            "equity": bench_eq,
            "metrics": quant.portfolio_metrics(bench[_bt_start:], rf),
        },
    }


# ── 시장 전망 (섹터 로테이션 + 종목 추천) ────────────────────────────────────

_SECTOR_ETFS = {
    "XLK": "기술", "XLC": "커뮤니케이션", "XLY": "임의소비재", "XLP": "필수소비재",
    "XLV": "헬스케어", "XLF": "금융", "XLI": "산업재", "XLE": "에너지",
    "XLB": "소재", "XLRE": "부동산", "XLU": "유틸리티",
}
_SECTOR_STOCKS = {
    "XLK": ["AAPL", "MSFT", "NVDA", "AVGO"],
    "XLC": ["GOOGL", "META", "NFLX"],
    "XLY": ["AMZN", "TSLA", "HD", "MCD"],
    "XLP": ["PG", "KO", "COST", "WMT"],
    "XLV": ["LLY", "UNH", "JNJ", "ABBV"],
    "XLF": ["JPM", "V", "MA", "BAC"],
    "XLI": ["GE", "CAT", "HON", "RTX"],
    "XLE": ["XOM", "CVX", "COP"],
    "XLB": ["LIN", "SHW", "FCX"],
    "XLRE": ["PLD", "AMT", "EQIX"],
    "XLU": ["NEE", "SO", "DUK"],
}

_outlook_cache: dict = {"data": None, "ts": 0.0}
_OUTLOOK_TTL = 3600  # 1시간


def _compute_outlook() -> dict:
    import yfinance as yf

    from app.core import market_scan as ms

    stocks = sorted({s for lst in _SECTOR_STOCKS.values() for s in lst})
    syms = ["SPY", *_SECTOR_ETFS.keys(), *stocks]

    try:
        df = yf.download(
            syms, period="1y", progress=False, group_by="ticker",
            threads=True, auto_adjust=True,
        )
    except Exception as e:
        logger.warning("outlook batch download failed: %s", e)
        return {"ok": False, "reason": "시세를 불러오지 못했습니다."}

    multi = hasattr(df.columns, "levels")

    def closes(sym: str) -> list[float]:
        try:
            s = (df[sym]["Close"] if multi else df["Close"]).dropna()
            return [float(x) for x in s.tolist()]
        except Exception:
            return []

    spx_c = closes("SPY")
    if len(spx_c) < 60:
        return {"ok": False, "reason": "지수 데이터가 부족합니다."}
    spx_mp = ms.momentum_profile(spx_c)

    sectors = []
    for etf, name in _SECTOR_ETFS.items():
        c = closes(etf)
        if len(c) < 60:
            continue
        mp = ms.momentum_profile(c)
        rs = ms.relative_strength(c, spx_c)
        score = ms.trend_score(mp, rs)
        grade, tone = ms.score_label(score)
        sectors.append({
            "etf": etf, "name": name, "score": score, "grade": grade, "tone": tone,
            "ret_1m": mp["ret_1m"], "ret_3m": mp["ret_3m"], "ret_6m": mp["ret_6m"],
            "rel_strength": rs, "rsi": mp["rsi"],
            "above_ma200": mp["above_ma200"],
        })
    sectors.sort(key=lambda s: s["score"], reverse=True)

    breadth = None
    if sectors:
        breadth = round(
            sum(1 for s in sectors if s["above_ma200"]) / len(sectors), 2
        )

    macro = _get_macro_cached()
    cape = (macro.get("cape") or {}).get("value")
    vix = (macro.get("vix") or {}).get("price")
    tnx = (macro.get("bonds_10y") or {}).get("price")
    regime = ms.market_regime(cape, vix, tnx, spx_mp, breadth)

    # 상위 섹터의 대표 종목 추천
    picks = []
    for sec in sectors[:3]:
        cand = []
        for tk in _SECTOR_STOCKS.get(sec["etf"], []):
            c = closes(tk)
            if len(c) < 60:
                continue
            mp = ms.momentum_profile(c)
            rs = ms.relative_strength(c, spx_c)
            sc = ms.trend_score(mp, rs)
            grade, tone = ms.score_label(sc)
            cand.append({
                "ticker": tk, "score": sc, "grade": grade, "tone": tone,
                "ret_3m": mp["ret_3m"], "rel_strength": rs, "rsi": mp["rsi"],
            })
        cand.sort(key=lambda x: x["score"], reverse=True)
        picks.append({
            "etf": sec["etf"], "name": sec["name"], "score": sec["score"],
            "grade": sec["grade"], "tone": sec["tone"], "stocks": cand[:3],
        })

    return {
        "ok": True,
        "as_of": date.today().isoformat(),
        "regime": regime,
        "spx": {
            "ret_1m": spx_mp["ret_1m"], "ret_3m": spx_mp["ret_3m"],
            "ret_6m": spx_mp["ret_6m"], "above_ma200": spx_mp["above_ma200"],
            "rsi": spx_mp["rsi"],
        },
        "breadth": breadth,
        "sectors": sectors,
        "picks": picks,
    }


@app.get("/api/market/outlook")
async def market_outlook(refresh: bool = False) -> dict:
    """현재 시장 국면 + 섹터 순위 + 유망 분야별 대표 종목."""
    import time

    now = time.time()
    if not refresh and _outlook_cache["data"] and now - _outlook_cache["ts"] < _OUTLOOK_TTL:
        return _outlook_cache["data"]
    data = _compute_outlook()
    if data.get("ok"):
        _outlook_cache["data"] = data
        _outlook_cache["ts"] = now
    return data


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
