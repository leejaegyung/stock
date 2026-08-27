import logging
from datetime import datetime, timedelta, timezone

import yfinance as yf

from app.core.datasources.base import DataSourceBase

logger = logging.getLogger(__name__)


def _fred_macro(api_key: str) -> dict:
    """
    FRED API → Fed Funds Rate·CPI·PCE·실업률.
    https://fred.stlouisfed.org/docs/api/api_key.html (무료 등록)
    """
    import requests

    base = "https://api.stlouisfed.org/fred/series/observations"
    result: dict = {}

    series_map = {
        "fed_funds_rate": ("FEDFUNDS",  "%",       "연방기금금리"),
        "cpi_yoy":        ("CPIAUCSL",  "전년비 %", "미국 CPI"),
        "pce_yoy":        ("PCEPI",     "전년비 %", "미국 PCE"),
        "unemployment":   ("UNRATE",    "%",        "실업률"),
        "gdp_growth":     ("A191RL1Q225SBEA", "%",  "실질 GDP 성장률"),
    }

    for key, (series_id, unit, label) in series_map.items():
        try:
            r = requests.get(base, params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 2,
            }, timeout=8)
            if r.ok:
                obs = r.json().get("observations", [])
                vals = [o for o in obs if o.get("value") not in (".", None, "")]
                if vals:
                    latest = vals[0]
                    prev   = vals[1] if len(vals) > 1 else None
                    value  = float(latest["value"])
                    result[key] = {
                        "value":  value,
                        "period": latest.get("date", ""),
                        "unit":   unit,
                        "label":  label,
                        "change": round(value - float(prev["value"]), 3) if prev else None,
                        "source": "FRED",
                    }
        except Exception as e:
            logger.warning("FRED %s failed: %s", series_id, e)

    return result


# Sector peer map — expanded on demand
_SECTOR_PEERS: dict[str, list[str]] = {
    "Technology": ["AAPL", "MSFT", "GOOGL", "META", "NVDA"],
    "Consumer Cyclical": ["AMZN", "TSLA", "HD", "MCD"],
    "Financial Services": ["JPM", "BAC", "WFC", "GS"],
    "Healthcare": ["JNJ", "PFE", "UNH", "ABBV"],
    "Energy": ["XOM", "CVX", "COP", "SLB"],
}


class USDataSource(DataSourceBase):
    """
    Layer 3 — US market data via yfinance.
    KR datasource is handled separately in kr.py.
    """

    def get_financials(self, ticker: str) -> dict:
        t = yf.Ticker(ticker)
        info = t.info or {}

        try:
            financials = t.financials
            if financials is not None and not financials.empty:
                fin_dict = {str(col): {str(k): v for k, v in col_data.items()}
                            for col, col_data in financials.to_dict().items()}
            else:
                fin_dict = {}
        except Exception:
            fin_dict = {}

        try:
            cashflow = t.cashflow
            cf_latest = {}
            if cashflow is not None and not cashflow.empty:
                col = cashflow.columns[0]
                cf_latest = {
                    "operating": float(cashflow.loc["Operating Cash Flow", col])
                    if "Operating Cash Flow" in cashflow.index else None,
                    "investing": float(cashflow.loc["Investing Cash Flow", col])
                    if "Investing Cash Flow" in cashflow.index else None,
                    "financing": float(cashflow.loc["Financing Cash Flow", col])
                    if "Financing Cash Flow" in cashflow.index else None,
                }
        except Exception:
            cf_latest = {}

        try:
            balance = t.balance_sheet
            bal_dict = balance.iloc[:, 0].to_dict() if balance is not None and not balance.empty else {}
        except Exception:
            bal_dict = {}

        info_slice = {
            k: info.get(k)
            for k in [
                "shortName", "sector", "industry",
                "trailingPE", "forwardPE", "returnOnEquity",
                "debtToEquity", "dividendYield", "earningsGrowth",
                "revenueGrowth", "profitMargins", "operatingMargins",
                "marketCap", "currentPrice", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
            ]
        }
        # yfinance ≥0.2.5x: dividendYield가 이미 퍼센트(예: 2.31)로 옴 → 소수(0.0231)로 정규화
        _dy = info_slice.get("dividendYield")
        if _dy is not None and _dy > 1:
            info_slice["dividendYield"] = _dy / 100

        return {
            "ticker": ticker,
            "market": "US",
            "info": info_slice,
            "financials": fin_dict,
            "cashflow": cf_latest,
            "balance_sheet": {str(k): v for k, v in bal_dict.items()},
        }

    def get_news(self, ticker: str, days: int = 3) -> list[dict]:
        t = yf.Ticker(ticker)
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        items = []
        try:
            for article in t.news or []:
                content = article.get("content", {})
                # pubDate (ISO string) is the canonical field; fall back to providerPublishTime (int)
                pub_date_str = content.get("pubDate") or content.get("displayTime", "")
                pub_ts = article.get("providerPublishTime") or 0
                try:
                    if pub_date_str:
                        pub_dt = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
                    elif pub_ts:
                        pub_dt = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
                    else:
                        pub_dt = datetime.now(tz=timezone.utc)
                except (ValueError, TypeError):
                    pub_dt = datetime.now(tz=timezone.utc)
                if pub_dt < cutoff:
                    continue
                items.append({
                    "headline": content.get("title") or article.get("title", ""),
                    "source": content.get("provider", {}).get("displayName", ""),
                    "url": content.get("canonicalUrl", {}).get("url", ""),
                    "published_at": pub_dt.isoformat(),
                    "summary": content.get("summary", ""),
                })
        except Exception as e:
            logger.warning("Failed to fetch news for %s: %s", ticker, e)
        return items

    def get_price(self, ticker: str) -> dict:
        t = yf.Ticker(ticker)
        info = t.info or {}
        return {
            "ticker": ticker,
            "market": "US",
            "current_price": info.get("currentPrice"),
            "previous_close": info.get("previousClose"),
            "change_pct": (
                (info["currentPrice"] - info["previousClose"]) / info["previousClose"] * 100
                if info.get("currentPrice") and info.get("previousClose")
                else None
            ),
            "volume": info.get("volume"),
            "avg_volume": info.get("averageVolume"),
        }

    def get_peers(self, ticker: str) -> dict:
        t = yf.Ticker(ticker)
        info = t.info or {}
        sector = info.get("sector", "")
        peer_tickers = [p for p in _SECTOR_PEERS.get(sector, []) if p != ticker][:3]

        peers = []
        for pt in peer_tickers:
            try:
                pi = yf.Ticker(pt).info or {}
                peers.append({
                    "ticker": pt,
                    "name": pi.get("shortName", pt),
                    "per": pi.get("trailingPE"),
                    "roe": pi.get("returnOnEquity"),
                    "revenue_growth": pi.get("revenueGrowth"),
                    "profit_margin": pi.get("profitMargins"),
                })
            except Exception:
                pass

        return {
            "ticker": ticker,
            "sector": sector,
            "industry": info.get("industry", ""),
            "peers": peers,
        }

    def get_macro_data(self) -> dict:
        """
        US 거시 데이터.
        - 기본: yfinance (주가지수·VIX·달러인덱스·채권·원자재)
        - CAPE: multpl.com 스크래핑 (키 불필요)
        - FRED: FRED_API_KEY 설정 시 → Fed금리·CPI·실업률 실데이터 추가
        """
        result: dict = {}

        # ── 1) yfinance 프록시 ─────────────────────────────────────────────────
        symbols = {
            "sp500":     "^GSPC",
            "nasdaq":    "^IXIC",
            "vix":       "^VIX",
            "dxy":       "DX-Y.NYB",
            "bonds_10y": "^TNX",
            "gold":      "GC=F",
            "oil_wti":   "CL=F",
        }
        for name, sym in symbols.items():
            try:
                info = yf.Ticker(sym).info or {}
                result[name] = {
                    "symbol": sym,
                    "price": info.get("regularMarketPrice") or info.get("currentPrice"),
                    "change_pct": info.get("regularMarketChangePercent"),
                }
            except Exception as e:
                logger.warning("Macro fetch failed for %s: %s", sym, e)
                result[name] = None

        # ── 2) CAPE (Shiller PE) — multpl.com ─────────────────────────────────
        try:
            import re as _re
            import requests
            from bs4 import BeautifulSoup
            r = requests.get(
                "https://www.multpl.com/shiller-pe",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=8,
            )
            if r.ok:
                soup = BeautifulSoup(r.text, "html.parser")
                el = soup.select_one("#current")
                if el:
                    m = _re.search(r"(\d+\.\d+)", el.get_text())
                    if m:
                        cape_val = float(m.group(1))
                        result["cape"] = {
                            "value": cape_val,
                            "note": "Shiller CAPE >30 고평가, <15 저평가 기준",
                            "source": "multpl.com",
                        }
        except Exception as e:
            logger.warning("CAPE fetch failed: %s", e)

        # ── 2b) USD/KRW 환율 — yfinance ────────────────────────────────────────
        try:
            info = yf.Ticker("KRW=X").info or {}
            price = info.get("regularMarketPrice") or info.get("currentPrice")
            if price:
                result["usd_krw"] = {
                    "price": price,
                    "change_pct": info.get("regularMarketChangePercent"),
                    "source": "yfinance",
                }
        except Exception as e:
            logger.warning("USD/KRW fetch failed: %s", e)

        # ── 3) FRED API (선택 — FRED_API_KEY 설정 시) ─────────────────────────
        try:
            from app.config import settings
            fred_key = getattr(settings, "fred_api_key", "")
            if fred_key:
                result.update(_fred_macro(fred_key))
        except Exception as e:
            logger.warning("FRED macro failed: %s", e)

        return result
