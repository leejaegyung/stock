"""
KR market datasource — Phase 2 실연결.

데이터 소스:
  - 시세·시가총액·거래량: pykrx (KRX 공식)
  - PER·PBR·EPS·ROE·배당수익률: 네이버 금융 (PC 스크래핑)
  - 매출·영업이익 추이: 네이버 증권 모바일 API
  - 뉴스: 네이버 증권 모바일 API (JSON, 인증 불필요)
  - 경쟁사 비교: 같은 섹터 종목 네이버 금융 반복 조회
  - 거시(KOSPI·KOSDAQ): 네이버 증권 모바일 API
  - 재무제표 (손익·현금흐름): DART API (DART_API_KEY 설정 시 활성화)
"""

import io
import logging
import re
import warnings
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from pykrx import stock as krx

from app.core.datasources.base import DataSourceBase

warnings.filterwarnings("ignore", category=UserWarning)

logger = logging.getLogger(__name__)

# ── 네이버 공통 ────────────────────────────────────────────────────────────────

_PC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://finance.naver.com/",
}
_MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko)"
    ),
    "Referer": "https://m.stock.naver.com/",
}

# ── 섹터 매핑 ─────────────────────────────────────────────────────────────────

_TICKER_TO_SECTOR: dict[str, str] = {
    "005930": "반도체", "000660": "반도체", "042700": "반도체",
    "005380": "자동차", "000270": "자동차", "012330": "자동차",
    "051910": "2차전지", "006400": "2차전지", "373220": "2차전지",
    "035420": "인터넷", "035720": "인터넷", "259960": "인터넷",
    "068270": "바이오", "207940": "바이오",
    "105560": "금융", "055550": "금융", "086790": "금융",
    "005490": "철강", "004020": "철강",
}

_KR_SECTOR_PEERS: dict[str, list[str]] = {
    "반도체": ["005930", "000660", "042700"],
    "자동차": ["005380", "000270", "012330"],
    "2차전지": ["051910", "006400", "373220"],
    "인터넷": ["035420", "035720", "259960"],
    "바이오": ["068270", "207940"],
    "금융": ["105560", "055550", "086790"],
    "철강": ["005490", "004020"],
}


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _safe_float(val) -> Optional[float]:
    try:
        s = str(val).replace(",", "").strip()
        f = float(s)
        return f if f != 0.0 else None
    except (TypeError, ValueError):
        return None


def _ecos_macro(api_key: str) -> dict:
    """
    한국은행 ECOS API → 기준금리·CPI.
    https://ecos.bok.or.kr/#/AuthKeyApply (무료 등록)
    """
    base = "https://ecos.bok.or.kr/api"
    result: dict = {}

    # 기준금리 (코드: 722Y001 / 항목: 0101000)
    try:
        url = f"{base}/StatisticSearch/{api_key}/json/kr/1/1/722Y001/M/2020010/99991231/0101000"
        with httpx.Client(timeout=8) as client:
            r = client.get(url)
            if r.status_code == 200:
                rows = r.json().get("StatisticSearch", {}).get("row", [])
                if rows:
                    latest = rows[-1]
                    result["kr_base_rate"] = {
                        "value": _safe_float(latest.get("DATA_VALUE")),
                        "period": latest.get("TIME", ""),
                        "unit": "%",
                        "source": "ECOS",
                    }
    except Exception as e:
        logger.warning("ECOS base_rate failed: %s", e)

    # 소비자물가지수 CPI (코드: 901Y009 / 항목: 0)
    try:
        url = f"{base}/StatisticSearch/{api_key}/json/kr/1/1/901Y009/M/2020010/99991231/0"
        with httpx.Client(timeout=8) as client:
            r = client.get(url)
            if r.status_code == 200:
                rows = r.json().get("StatisticSearch", {}).get("row", [])
                if rows:
                    latest = rows[-1]
                    result["kr_cpi"] = {
                        "value": _safe_float(latest.get("DATA_VALUE")),
                        "period": latest.get("TIME", ""),
                        "unit": "전년동월비 %",
                        "source": "ECOS",
                    }
    except Exception as e:
        logger.warning("ECOS CPI failed: %s", e)

    return result


def _latest_trading_date(lookback: int = 7) -> str:
    """pykrx로 조회 가능한 가장 최근 거래일 반환."""
    for i in range(lookback):
        d = (date.today() - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = krx.get_market_ohlcv_by_date(d, d, "005930")
            if not df.empty:
                return d
        except Exception:
            continue
    return (date.today() - timedelta(days=1)).strftime("%Y%m%d")


def _naver_ratios(ticker: str) -> dict:
    """
    네이버 금융 PC 페이지에서 PER·PBR·EPS·ROE·배당수익률을 추출.
    이 데이터는 pykrx보다 안정적으로 제공됨.
    """
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            r = client.get(
                "https://finance.naver.com/item/main.naver",
                params={"code": ticker},
                headers=_PC_HEADERS,
            )
            r.raise_for_status()

        text = r.text
        result: dict = {}
        patterns = {
            "per":  r"PER\(배\).*?<td[^>]*>\s*([\d,\.]+)",
            "pbr":  r"PBR\(배\).*?<td[^>]*>\s*([\d,\.]+)",
            "eps":  r"EPS\(원\).*?<td[^>]*>\s*([\d,\-]+)",
            "roe":  r"ROE\(지배주주\).*?<td[^>]*>\s*([\d\.\-]+)",
            "div":  r"배당금\(원\).*?<td[^>]*>\s*([\d,]+)",
        }
        for key, pat in patterns.items():
            m = re.search(pat, text, re.DOTALL)
            if m:
                result[key] = _safe_float(m.group(1).replace(",", ""))

        return result
    except Exception as e:
        logger.warning("Naver ratio scrape failed for %s: %s", ticker, e)
        return {}


def _naver_income_summary(ticker: str) -> dict:
    """
    네이버 증권 모바일 API에서 최근 매출·영업이익 추이 반환.
    """
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            r = client.get(
                f"https://m.stock.naver.com/api/stock/{ticker}/finance/summary",
                headers=_MOBILE_HEADERS,
            )
            r.raise_for_status()
            data = r.json()

        income = data.get("chartIncomeStatement", {}).get("annual", {})
        cols = income.get("columns", [])
        if len(cols) < 3:
            return {}

        dates = cols[0][1:]       # ['2023.12.', '2024.12.', ...]
        revenues = cols[1][1:]    # ['2589355', '3008709', ...]
        op_incomes = cols[2][1:]  # ['65670', '327260', ...]

        recent = {}
        for i, d in enumerate(dates[-3:]):  # 최근 3개년
            idx = len(dates) - 3 + i
            recent[d] = {
                "revenue": _safe_float(revenues[idx]) if idx < len(revenues) else None,
                "operating_income": _safe_float(op_incomes[idx]) if idx < len(op_incomes) else None,
            }

        return {"annual_income": recent}
    except Exception as e:
        logger.warning("Naver income summary failed for %s: %s", ticker, e)
        return {}


def _naver_basic(ticker: str) -> dict:
    """네이버 증권 모바일 기본 시세 (현재가·등락률·시장구분)."""
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            r = client.get(
                f"https://m.stock.naver.com/api/stock/{ticker}/basic",
                headers=_MOBILE_HEADERS,
            )
            r.raise_for_status()
            data = r.json()

        close_str = data.get("closePrice", "").replace(",", "")
        change_str = data.get("compareToPreviousClosePrice", "").replace(",", "")
        prev_close = _safe_float(close_str) - (_safe_float(change_str) or 0) if close_str else None

        return {
            "name": data.get("stockName", ""),
            "current_price": _safe_float(close_str),
            "change_pct": _safe_float(data.get("fluctuationsRatio")),
            "market": data.get("stockExchangeType", {}).get("nameKor", ""),
        }
    except Exception as e:
        logger.warning("Naver basic failed for %s: %s", ticker, e)
        return {}


# ── KRDataSource ──────────────────────────────────────────────────────────────

class KRDataSource(DataSourceBase):
    """Layer 3 — KR 시장 데이터 소스 (Phase 2 실연결)."""

    # ── 재무 데이터 ────────────────────────────────────────────────────────────

    def get_financials(self, ticker: str) -> dict:
        basic = _naver_basic(ticker)
        ratios = _naver_ratios(ticker)
        income = _naver_income_summary(ticker)

        # pykrx로 시가총액·거래량 보완
        market_cap = None
        volume = None
        try:
            trade_date = _latest_trading_date()
            cap_df = krx.get_market_cap_by_date(trade_date, trade_date, ticker)
            if not cap_df.empty:
                market_cap = _safe_float(cap_df["시가총액"].iloc[-1])
                volume = _safe_float(cap_df.get("거래량", cap_df.iloc[:, 1] if len(cap_df.columns) > 1 else cap_df).iloc[-1] if "거래량" in cap_df.columns else None)
        except Exception:
            pass

        # DART 재무제표 (API 키 있을 때만)
        dart = self._dart_financials(ticker)

        return {
            "ticker": ticker,
            "market": "KR",
            "info": {
                "shortName": basic.get("name") or ticker,
                "sector": _TICKER_TO_SECTOR.get(ticker, ""),
                "currentPrice": basic.get("current_price"),
                "change_pct": basic.get("change_pct"),
                "marketCap": market_cap,
                "per": ratios.get("per"),
                "pbr": ratios.get("pbr"),
                "eps": ratios.get("eps"),
                "roe": ratios.get("roe"),
                "dividendYield": ratios.get("div"),
            },
            "income_history": income.get("annual_income", {}),
            "cashflow": dart.get("cashflow", {}),
            "financials": dart.get("financials", {}),
            "balance_sheet": dart.get("balance_sheet", {}),
        }

    # ── 뉴스 ──────────────────────────────────────────────────────────────────

    def get_news(self, ticker: str, days: int = 3) -> list[dict]:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        items = []
        try:
            with httpx.Client(timeout=10, follow_redirects=True) as client:
                resp = client.get(
                    "https://m.stock.naver.com/api/news/list",
                    params={"stockcode": ticker, "pageSize": 20, "page": 1},
                    headers=_MOBILE_HEADERS,
                )
                resp.raise_for_status()
                articles = resp.json()

            for art in articles:
                dt_str = str(art.get("dt", ""))
                try:
                    pub_dt = datetime.strptime(dt_str, "%Y%m%d%H%M%S").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    pub_dt = datetime.now(tz=timezone.utc)

                if pub_dt < cutoff:
                    continue

                oid = art.get("oid", "")
                aid = art.get("aid", "")
                items.append({
                    "headline": art.get("tit", ""),
                    "summary": art.get("subcontent", ""),
                    "source": art.get("ohnm", ""),
                    "url": f"https://n.news.naver.com/mnews/article/{oid}/{aid}",
                    "published_at": pub_dt.isoformat(),
                })

        except Exception as e:
            logger.warning("KR news fetch failed for %s: %s", ticker, e)

        return items

    # ── 시세 ──────────────────────────────────────────────────────────────────

    def get_price(self, ticker: str) -> dict:
        # 네이버 모바일이 실시간, pykrx는 전일 마감 기준
        basic = _naver_basic(ticker)
        if basic.get("current_price"):
            return {
                "ticker": ticker,
                "market": "KR",
                "current_price": basic["current_price"],
                "change_pct": basic.get("change_pct"),
                "source": "naver_realtime",
            }

        # fallback: pykrx
        try:
            trade_date = _latest_trading_date()
            df = krx.get_market_ohlcv_by_date(trade_date, trade_date, ticker)
            if not df.empty:
                row = df.iloc[-1]
                close = _safe_float(row.get("종가"))
                open_ = _safe_float(row.get("시가"))
                return {
                    "ticker": ticker,
                    "market": "KR",
                    "current_price": close,
                    "open": open_,
                    "high": _safe_float(row.get("고가")),
                    "low": _safe_float(row.get("저가")),
                    "volume": _safe_float(row.get("거래량")),
                    "change_pct": ((close - open_) / open_ * 100) if open_ else None,
                    "trade_date": trade_date,
                    "source": "pykrx",
                }
        except Exception as e:
            logger.warning("pykrx price fallback failed for %s: %s", ticker, e)

        return {"ticker": ticker, "market": "KR"}

    # ── 경쟁사 비교 ────────────────────────────────────────────────────────────

    def get_peers(self, ticker: str) -> dict:
        sector = _TICKER_TO_SECTOR.get(ticker, "")
        peer_tickers = [p for p in _KR_SECTOR_PEERS.get(sector, []) if p != ticker][:3]

        peers = []
        for pt in peer_tickers:
            basic = _naver_basic(pt)
            ratios = _naver_ratios(pt)
            if basic or ratios:
                peers.append({
                    "ticker": pt,
                    "name": basic.get("name", pt),
                    "per": ratios.get("per"),
                    "pbr": ratios.get("pbr"),
                    "eps": ratios.get("eps"),
                    "roe": ratios.get("roe"),
                    "dividendYield": ratios.get("div"),
                    "current_price": basic.get("current_price"),
                    "change_pct": basic.get("change_pct"),
                })

        return {
            "ticker": ticker,
            "sector": sector,
            "peers": peers,
        }

    # ── 거시 데이터 ────────────────────────────────────────────────────────────

    def get_macro_data(self) -> dict:
        """KOSPI·KOSDAQ 지수 + USD/KRW 환율 + 기준금리·CPI (ECOS 선택)."""
        result: dict = {}

        # ── 1) 주가 지수 (Naver 모바일) ─────────────────────────────────────────
        for name, code in [("kospi", "KOSPI"), ("kosdaq", "KOSDAQ")]:
            try:
                with httpx.Client(timeout=10, follow_redirects=True) as client:
                    r = client.get(
                        f"https://m.stock.naver.com/api/index/{code}/basic",
                        headers=_MOBILE_HEADERS,
                    )
                    if r.status_code == 200:
                        d = r.json()
                        close_str = d.get("closePrice", "").replace(",", "")
                        result[name] = {
                            "price": _safe_float(close_str),
                            "change_pct": _safe_float(d.get("fluctuationsRatio")),
                        }
            except Exception as e:
                logger.warning("KR macro %s failed: %s", name, e)

        # pykrx fallback for index data
        if "kospi" not in result:
            try:
                trade_date = _latest_trading_date()
                from_date = (date.today() - timedelta(days=7)).strftime("%Y%m%d")
                for name, idx_code in [("kospi", "1001"), ("kosdaq", "2001")]:
                    df = krx.get_index_ohlcv_by_date(from_date, trade_date, idx_code)
                    if not df.empty:
                        row = df.iloc[-1]
                        result[name] = {"price": _safe_float(row.get("종가")), "source": "pykrx"}
            except Exception:
                pass

        # ── 2) 한국은행 ECOS API (선택 — ECOS_API_KEY 설정 시) ─────────────────
        try:
            from app.config import settings
            ecos_key = getattr(settings, "ecos_api_key", "")
            if ecos_key:
                result.update(_ecos_macro(ecos_key))
        except Exception as e:
            logger.warning("ECOS macro failed: %s", e)

        # ── 3) USD/KRW 환율 — yfinance (US datasource와 동일 소스) ──────────────
        try:
            import yfinance as yf
            info = yf.Ticker("KRW=X").info or {}
            price = info.get("regularMarketPrice") or info.get("currentPrice")
            if price:
                result["usd_krw"] = {
                    "price": price,
                    "change_pct": info.get("regularMarketChangePercent"),
                    "source": "yfinance",
                }
        except Exception as e:
            logger.warning("KR USD/KRW fetch failed: %s", e)

        return result

    # ── DART API (선택) ────────────────────────────────────────────────────────

    def _dart_financials(self, ticker: str) -> dict:
        """DART API 재무제표 — DART_API_KEY 환경변수 설정 시 활성화."""
        try:
            from app.config import settings
            dart_key = getattr(settings, "dart_api_key", "")
            if not dart_key:
                return {}
        except Exception:
            return {}

        try:
            corp_code = _dart_corp_code(ticker, dart_key)
            if not corp_code:
                return {}

            year = str(date.today().year - 1)
            with httpx.Client(timeout=15) as client:
                resp = client.get(
                    "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                    params={
                        "crtfc_key": dart_key,
                        "corp_code": corp_code,
                        "bsns_year": year,
                        "reprt_code": "11011",
                        "fs_div": "CFS",
                    },
                )
                data = resp.json()

            if data.get("status") != "000":
                return {}

            cashflow: dict = {}
            financials: dict = {}
            for item in data.get("list", []):
                label = item.get("account_nm", "")
                raw_val = item.get("thstrm_amount", "").replace(",", "")
                val = _safe_float(raw_val)
                if val is None:
                    continue
                if "영업활동" in label:
                    cashflow["operating"] = val
                elif "투자활동" in label:
                    cashflow["investing"] = val
                elif "재무활동" in label:
                    cashflow["financing"] = val
                elif "매출" in label and "financials" not in financials:
                    financials["revenue"] = val
                elif "영업이익" in label:
                    financials["operating_income"] = val
                elif "당기순이익" in label:
                    financials["net_income"] = val

            return {"cashflow": cashflow, "financials": financials, "balance_sheet": {}}

        except Exception as e:
            logger.warning("DART API failed for %s: %s", ticker, e)
            return {}


def _dart_corp_code(ticker: str, dart_key: str) -> Optional[str]:
    """종목코드(6자리) → DART corp_code 변환."""
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(
                "https://opendart.fss.or.kr/api/corpCode.xml",
                params={"crtfc_key": dart_key},
            )
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            xml_data = z.read(z.namelist()[0]).decode("utf-8")

        for pattern in [
            rf"<stock_code>\s*{re.escape(ticker)}\s*</stock_code>.*?<corp_code>(\d+)</corp_code>",
            rf"<corp_code>(\d+)</corp_code>.*?<stock_code>\s*{re.escape(ticker)}\s*</stock_code>",
        ]:
            m = re.search(pattern, xml_data, re.DOTALL)
            if m:
                return m.group(1)
        return None
    except Exception as e:
        logger.warning("DART corp_code lookup failed for %s: %s", ticker, e)
        return None
