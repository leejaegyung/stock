"""
LLM-free algorithmic analysis pipeline.
STOCK_ANALYST_SERVICE.md §4 공식을 그대로 코드로 구현.
Claude API 없이 동작. formulas.py의 순수 함수 사용.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pandas as pd

from app.core.confidence import analysis_confidence, improvement_hints
from app.core.formulas import (
    cashflow_pattern, cape_judgment, per_roe_judgment,
    expected_value, half_kelly,
)
from app.core.datasources.us import USDataSource
from app.core.datasources.kr import KRDataSource

logger = logging.getLogger(__name__)


# ── 긍정/부정 키워드 (한영) ──────────────────────────────────────────────────

_POS_KW = [
    "beat", "exceed", "record", "growth", "surge", "rally", "upgrade",
    "dividend", "buyback", "expansion", "partnership", "acquisition",
    "호실적", "실적 초과", "신기록", "성장", "급등", "배당", "자사주",
    "상향", "확장", "파트너십", "인수", "수익 증가", "매출 증가",
    "영업이익 증가", "흑자전환", "신제품", "수주", "흑자",
]
_NEG_KW = [
    "miss", "decline", "fall", "drop", "lawsuit", "investigation", "recall",
    "downgrade", "bankruptcy", "loss", "fraud", "warning", "cut", "risk",
    "실적 부진", "어닝쇼크", "감소", "하락", "소송", "조사", "리콜",
    "하향", "파산", "적자", "분식", "경고", "구조조정",
    "매출 감소", "영업손실", "이익 감소", "가이던스 하향",
]


# ── 1. 가격 데이터 ───────────────────────────────────────────────────────────

def _yf_symbol(ticker: str, market: str) -> str:
    return f"{ticker}.KS" if market.upper() == "KR" else ticker


def _fetch_price_df(ticker: str, market: str, period: str = "1y") -> pd.DataFrame:
    try:
        import yfinance as yf
        sym = _yf_symbol(ticker, market)
        df = yf.download(sym, period=period, auto_adjust=True, progress=False)
        if df.empty and market.upper() == "KR":
            df = yf.download(f"{ticker}.KQ", period=period, auto_adjust=True, progress=False)
        # flatten MultiIndex (yfinance ≥0.2.x sometimes returns it)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        logger.warning("Price fetch failed %s: %s", ticker, e)
        return pd.DataFrame()


# ── 2. 기술적 지표 계산 ──────────────────────────────────────────────────────

def _rsi(series: pd.Series, n: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if len(rsi) else 50.0


def _macd(series: pd.Series) -> tuple[float, float, float]:
    """Returns (macd_line, signal_line, histogram)."""
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    sig  = macd.ewm(span=9, adjust=False).mean()
    return float(macd.iloc[-1]), float(sig.iloc[-1]), float((macd - sig).iloc[-1])


def _technical_indicators(df: pd.DataFrame) -> dict:
    if df.empty or "Close" not in df.columns or len(df) < 30:
        return {"ok": False}

    close = df["Close"].dropna()
    if len(close) < 30:
        return {"ok": False}

    price   = float(close.iloc[-1])
    sma20   = float(close.rolling(20).mean().iloc[-1])
    sma50   = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else sma20
    sma200  = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
    bb_std  = float(close.rolling(20).std().iloc[-1])
    bb_upper = sma20 + 2 * bb_std
    bb_lower = sma20 - 2 * bb_std

    rsi_val      = _rsi(close)
    macd_l, macd_s, macd_h = _macd(close)

    ret_1m  = (price / float(close.iloc[-22]) - 1) * 100 if len(close) >= 22 else None
    ret_3m  = (price / float(close.iloc[-66]) - 1) * 100 if len(close) >= 66 else None
    ret_6m  = (price / float(close.iloc[-130]) - 1) * 100 if len(close) >= 130 else None

    vol = df["Volume"].dropna() if "Volume" in df.columns else pd.Series()
    vol_ratio = (float(vol.iloc[-1]) / float(vol.rolling(20).mean().iloc[-1])
                 if not vol.empty and len(vol) >= 20 else None)

    return {
        "ok": True, "price": price,
        "rsi": round(rsi_val, 1),
        "macd": round(macd_l, 4), "macd_sig": round(macd_s, 4), "macd_hist": round(macd_h, 4),
        "sma20": round(sma20, 2), "sma50": round(sma50, 2),
        "sma200": round(sma200, 2) if sma200 else None,
        "bb_upper": round(bb_upper, 2), "bb_lower": round(bb_lower, 2),
        "ret_1m": round(ret_1m, 1) if ret_1m is not None else None,
        "ret_3m": round(ret_3m, 1) if ret_3m is not None else None,
        "ret_6m": round(ret_6m, 1) if ret_6m is not None else None,
        "vol_ratio": round(vol_ratio, 2) if vol_ratio else None,
    }


# ── 3. 기술적 점수 (0 ~ 30) ──────────────────────────────────────────────────

def _score_technical(ta: dict) -> tuple[int, list[str]]:
    if not ta.get("ok"):
        return 15, ["가격 데이터 부족 — 기본값"]

    score = 0
    notes: list[str] = []

    # RSI (0-10)
    r = ta["rsi"]
    if   r < 25:  score += 10; notes.append(f"RSI {r} — 극도 과매도 (반등 신호)")
    elif r < 35:  score += 8;  notes.append(f"RSI {r} — 과매도")
    elif r < 50:  score += 6;  notes.append(f"RSI {r} — 중립-강세")
    elif r < 65:  score += 4;  notes.append(f"RSI {r} — 중립")
    elif r < 75:  score += 2;  notes.append(f"RSI {r} — 과매수 경계")
    else:         score += 0;  notes.append(f"RSI {r} — 과매수 (조정 위험)")

    # MACD (0-10)
    h = ta["macd_hist"]
    m = ta["macd"]
    if   h > 0 and m > 0:  score += 10; notes.append("MACD 골든크로스 + 양전환")
    elif h > 0:             score += 6;  notes.append("MACD 골든크로스")
    elif h < 0 and m < 0:  score += 0;  notes.append("MACD 데드크로스 + 음전환")
    else:                   score += 2;  notes.append("MACD 약세")

    # SMA 포지션 (0-10)
    p = ta["price"]
    s200 = ta.get("sma200")
    s50  = ta["sma50"]
    if s200:
        if p > s50 > s200:  score += 10; notes.append(f"주가 > SMA50 > SMA200 (강한 상승추세)")
        elif p > s200:      score += 6;  notes.append(f"주가 200일선 위 (중기 강세)")
        elif p > s50:       score += 3;  notes.append(f"주가 50일선 위, 200일선 아래")
        else:               score += 0;  notes.append(f"주가 200일선 아래 (약세)")
    else:
        score += 5  # 데이터 부족 시 중립

    return min(score, 30), notes


# ── 4. 펀더멘털 점수 (0 ~ 40) ────────────────────────────────────────────────

def _score_fundamental(fund: dict) -> tuple[int, list[str]]:
    score = 0
    notes: list[str] = []
    info = fund.get("info", {})

    # P/E (0-10)
    pe = info.get("trailingPE") or info.get("forwardPE")
    if pe and pe > 0:
        if   pe < 10:  score += 10; notes.append(f"P/E {pe:.1f} — 매우 저평가")
        elif pe < 15:  score += 8;  notes.append(f"P/E {pe:.1f} — 저평가")
        elif pe < 20:  score += 6;  notes.append(f"P/E {pe:.1f} — 적정")
        elif pe < 25:  score += 4;  notes.append(f"P/E {pe:.1f} — 다소 고평가")
        elif pe < 35:  score += 2;  notes.append(f"P/E {pe:.1f} — 고평가")
        else:          score += 0;  notes.append(f"P/E {pe:.1f} — 매우 고평가")
    else:
        score += 4

    # ROE (0-10)
    roe = info.get("returnOnEquity")
    if roe is not None:
        rp = roe * 100
        if   rp > 25:  score += 10; notes.append(f"ROE {rp:.1f}% — 최우량")
        elif rp > 20:  score += 8;  notes.append(f"ROE {rp:.1f}% — 우량")
        elif rp > 15:  score += 6;  notes.append(f"ROE {rp:.1f}% — 양호")
        elif rp > 10:  score += 4;  notes.append(f"ROE {rp:.1f}% — 보통")
        elif rp > 0:   score += 2;  notes.append(f"ROE {rp:.1f}% — 낮음")
        else:          score += 0;  notes.append(f"ROE {rp:.1f}% — 자본 훼손")
    else:
        score += 4

    # 부채비율 (0-8)
    de = info.get("debtToEquity")
    if de is not None:
        if   de < 30:   score += 8; notes.append(f"부채비율 {de:.0f}% — 매우 안정")
        elif de < 70:   score += 6; notes.append(f"부채비율 {de:.0f}% — 안정")
        elif de < 120:  score += 4; notes.append(f"부채비율 {de:.0f}% — 보통")
        elif de < 200:  score += 2; notes.append(f"부채비율 {de:.0f}% — 높음")
        else:           score += 0; notes.append(f"부채비율 {de:.0f}% — 위험")
    else:
        score += 3

    # 이익 성장률 (0-8)
    growth = info.get("earningsGrowth") or info.get("revenueGrowth")
    if growth is not None:
        gp = growth * 100
        if   gp > 20:  score += 8; notes.append(f"이익성장 {gp:.1f}% — 강한 성장")
        elif gp > 10:  score += 6; notes.append(f"이익성장 {gp:.1f}% — 양호")
        elif gp > 5:   score += 4; notes.append(f"이익성장 {gp:.1f}% — 보통")
        elif gp > 0:   score += 2; notes.append(f"이익성장 {gp:.1f}% — 저성장")
        else:          score += 0; notes.append(f"이익성장 {gp:.1f}% — 역성장")
    else:
        score += 3

    # 순이익률 보너스 (0-4)
    margin = info.get("profitMargins")
    if margin is not None:
        mp = margin * 100
        if   mp > 20:  score += 4; notes.append(f"순이익률 {mp:.1f}% — 우수")
        elif mp > 10:  score += 2; notes.append(f"순이익률 {mp:.1f}% — 양호")
        elif mp < 0:   score -= 2; notes.append(f"순이익률 {mp:.1f}% — 적자 수익성")

    # 배당 보너스 (0-4)
    div = info.get("dividendYield")
    if div and div > 0:
        dp = div * 100
        if   dp > 4:  score += 4; notes.append(f"배당수익률 {dp:.2f}% — 고배당")
        elif dp > 2:  score += 2; notes.append(f"배당수익률 {dp:.2f}% — 양호")
        else:         score += 1; notes.append(f"배당수익률 {dp:.2f}%")

    return min(max(score, 0), 40), notes


# ── 5. 거시 점수 (0 ~ 20) ────────────────────────────────────────────────────

def _parse_float(val) -> float | None:
    try:
        return float(str(val).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _score_macro(macro: dict) -> tuple[int, list[str]]:
    score = 0
    notes: list[str] = []

    # VIX (0-8)
    vix = _parse_float(macro.get("VIX"))
    if vix is not None:
        if   vix < 15:  score += 8; notes.append(f"VIX {vix:.1f} — 시장 안정")
        elif vix < 20:  score += 6; notes.append(f"VIX {vix:.1f} — 보통")
        elif vix < 25:  score += 4; notes.append(f"VIX {vix:.1f} — 경계")
        elif vix < 30:  score += 2; notes.append(f"VIX {vix:.1f} — 공포")
        else:           score += 0; notes.append(f"VIX {vix:.1f} — 극도의 공포")
    else:
        score += 4

    # CAPE (0-8)
    cape = _parse_float(macro.get("CAPE"))
    if cape is not None:
        j = cape_judgment(cape)
        if   cape < 20:  score += 8; notes.append(f"CAPE {cape:.1f} — {j}")
        elif cape < 25:  score += 6; notes.append(f"CAPE {cape:.1f} — {j}")
        elif cape < 30:  score += 4; notes.append(f"CAPE {cape:.1f} — {j}")
        elif cape < 35:  score += 2; notes.append(f"CAPE {cape:.1f} — {j}")
        else:            score += 0; notes.append(f"CAPE {cape:.1f} — {j} (시장 위험)")
    else:
        score += 4

    # 10Y 금리 (0-4)  높을수록 주식 밸류에이션 부담
    rate = _parse_float(macro.get("10Y 금리") or macro.get("10Y"))
    if rate is not None:
        if   rate < 3:   score += 4; notes.append(f"10Y 금리 {rate:.2f}% — 주식 우호적")
        elif rate < 4:   score += 2; notes.append(f"10Y 금리 {rate:.2f}% — 중립")
        elif rate < 5:   score += 1; notes.append(f"10Y 금리 {rate:.2f}% — 부담")
        else:            score += 0; notes.append(f"10Y 금리 {rate:.2f}% — 높은 금리 부담")
    else:
        score += 2

    return min(score, 20), notes


# ── 6. 뉴스 점수 (0 ~ 10) ────────────────────────────────────────────────────

def _score_news(news_items: list[dict]) -> tuple[int, list[str]]:
    if not news_items:
        return 5, []

    pos = neg = 0
    headlines: list[str] = []

    for item in news_items[:10]:
        text = (item.get("headline", "") + " " + item.get("summary", "")).lower()
        p = sum(1 for kw in _POS_KW if kw.lower() in text)
        n = sum(1 for kw in _NEG_KW if kw.lower() in text)
        tag = "촉매" if p > n else ("저해" if n > p else "중립")
        if p > n:  pos += 1
        if n > p:  neg += 1
        h = item.get("headline", "")[:60]
        headlines.append(f"[{tag}] {h}")

    total = pos + neg or 1
    score = int(5 + (pos - neg) / total * 5)
    return max(0, min(10, score)), headlines[:5]


# ── 7. Bull / Bear 신호 추출 ─────────────────────────────────────────────────

def _bull_signals(ta: dict, fund: dict, macro: dict) -> list[str]:
    sigs: list[str] = []
    info = fund.get("info", {})

    if ta.get("ok"):
        if ta["rsi"] < 35:
            sigs.append(f"RSI {ta['rsi']} — 과매도 반등 잠재력")
        if ta["macd_hist"] > 0:
            sigs.append("MACD 골든크로스 — 단기 상승 모멘텀")
        if ta.get("sma200") and ta["price"] > ta["sma200"]:
            sigs.append("200일 이동평균 위 — 중기 강세 구간")
        if ta.get("ret_1m") and ta["ret_1m"] > 5:
            sigs.append(f"1개월 수익률 +{ta['ret_1m']}% — 강한 모멘텀")

    pe  = info.get("trailingPE") or info.get("forwardPE")
    roe = info.get("returnOnEquity")
    div = info.get("dividendYield")

    if pe and 0 < pe < 15:
        sigs.append(f"P/E {pe:.1f} — 저평가 밸류에이션")
    if roe and roe > 0.20:
        sigs.append(f"ROE {roe*100:.1f}% — 높은 자본 수익성")
    if div and div > 0.03:
        sigs.append(f"배당수익률 {div*100:.2f}% — 안정적 수익원")

    cf = fund.get("cashflow", {})
    if cf.get("operating") and cf["operating"] > 0:
        pat = cashflow_pattern(cf.get("operating", 0), cf.get("investing", 0), cf.get("financing", 0))
        if "우량" in pat or "성장" in pat:
            sigs.append(f"현금흐름 패턴: {pat}")

    return sigs


def _bear_signals(ta: dict, fund: dict, macro: dict) -> list[str]:
    sigs: list[str] = []
    info = fund.get("info", {})

    if ta.get("ok"):
        if ta["rsi"] > 70:
            sigs.append(f"RSI {ta['rsi']} — 과매수 (단기 조정 위험)")
        if ta["macd_hist"] < 0 and ta["macd"] < 0:
            sigs.append("MACD 데드크로스 — 하락 추세 지속")
        if ta.get("sma200") and ta["price"] < ta["sma200"]:
            sigs.append("200일 이동평균 아래 — 중기 약세")
        if ta.get("ret_3m") and ta["ret_3m"] < -10:
            sigs.append(f"3개월 수익률 {ta['ret_3m']}% — 하락 추세")

    pe = info.get("trailingPE") or info.get("forwardPE")
    de = info.get("debtToEquity")
    margin = info.get("profitMargins")
    eg = info.get("earningsGrowth")

    if pe and pe > 35:
        sigs.append(f"P/E {pe:.1f} — 고평가 위험")
    if de and de > 200:
        sigs.append(f"부채비율 {de:.0f}% — 재무 레버리지 위험")
    if margin is not None and margin < 0:
        sigs.append(f"순이익률 {margin*100:.1f}% — 수익성 적자")
    if eg is not None and eg < -0.1:
        sigs.append(f"이익 성장률 {eg*100:.1f}% — 역성장")

    vix = _parse_float(macro.get("VIX"))
    if vix and vix > 25:
        sigs.append(f"VIX {vix:.1f} — 시장 공포 구간")

    cape = _parse_float(macro.get("CAPE"))
    if cape and cape > 35:
        sigs.append(f"CAPE {cape:.1f} — 시장 전체 버블 경고")

    return sigs


# ── 8. 계량 검증 §4.1-4.5 ────────────────────────────────────────────────────

def _quant_metrics(ts: int, fs: int, ms: int, fund: dict, macro: dict) -> dict:
    """
    §4.1 기대값, §4.2 하프켈리, §4.3 현금흐름 패턴, §4.4 CAPE, §4.5 PER/ROE.
    승률·손익비는 추정치임을 명시 (STOCK_ANALYST_SERVICE.md §4.2 주의).
    """
    total = ts + fs + ms

    # 승률 추정: 종합점수 0-100 → 0.25~0.75 선형 매핑
    win_prob = round(0.25 + total / 100 * 0.50, 2)
    win_prob = max(0.25, min(0.75, win_prob))

    # 기대이익·손실 추정 (펀더멘털 품질 반영)
    exp_gain = 8.0 + (fs / 40) * 12.0   # 8~20%
    exp_loss = 15.0 - (fs / 40) * 5.0   # 10~15%

    ev  = expected_value(win_prob, exp_gain, exp_loss)
    hk  = half_kelly(win_prob, exp_gain / exp_loss)
    hk  = max(0.0, min(hk, 0.25))        # 최대 25% 캡

    # §4.5 밸류에이션
    info = fund.get("info", {})
    pe   = info.get("trailingPE") or info.get("forwardPE")
    roe  = info.get("returnOnEquity")
    val_parts: list[str] = []
    if pe and roe:
        val_parts.append(per_roe_judgment(pe, roe * 100, pe * 1.1, roe * 90))
    cape_val = _parse_float(macro.get("CAPE"))
    if cape_val:
        val_parts.append(f"시장 CAPE {cape_val:.1f}: {cape_judgment(cape_val)}")

    # §4.3 현금흐름 패턴
    cf = fund.get("cashflow", {})
    cf_pat = None
    if cf.get("operating") is not None:
        cf_pat = cashflow_pattern(
            cf.get("operating", 0), cf.get("investing", 0), cf.get("financing", 0)
        )

    return {
        "win_prob": win_prob,
        "ev": round(ev, 2),
        "half_kelly": round(hk * 100, 1),
        "valuation": " | ".join(val_parts) if val_parts else "-",
        "cashflow_pattern": cf_pat,
        "ev_assumption": (
            f"승률 {win_prob*100:.0f}% · 기대이익 {exp_gain:.0f}% · "
            f"기대손실 {exp_loss:.0f}% — 추정치, 투자자문 아님"
        ),
    }


# ── 9. 판정 ──────────────────────────────────────────────────────────────────

def _verdict(total: int) -> tuple[str, str]:
    """§4.1 EV > 0 기준을 점수로 근사. Returns (verdict, confidence)."""
    if   total >= 72:  return "매수",    "상"
    elif total >= 60:  return "추가매수","중"
    elif total >= 45:  return "보유",    "중"
    elif total >= 30:  return "보유",    "하"
    else:              return "매도",    "하"


# ── 10. 리포트 템플릿 ────────────────────────────────────────────────────────

def _stock_report_md(
    ticker: str, market: str, date_str: str,
    ta: dict, fund: dict, macro: dict,
    ts: int, fs: int, ms: int, ns: int,
    bulls: list[str], bears: list[str],
    quant: dict, news_lines: list[str],
    conf: dict | None = None,
) -> str:
    total = ts + fs + ms + ns
    vd, _band_cf = _verdict(total)
    cf = (conf or {}).get("grade", _band_cf)
    emoji = {"매수": "🟢", "추가매수": "🔵", "보유": "🟡", "매도": "🔴"}.get(vd, "⚪")
    name = fund.get("info", {}).get("shortName", ticker)
    info = fund.get("info", {})

    conf_str = f"확신도: {cf}"
    if conf and conf.get("score") is not None:
        conf_str = f"확신도: {cf} ({conf['score']}/100)"

    L: list[str] = [
        f"## {ticker} [{market}] — {name}",
        f"- **결론**: {emoji} **{vd}** ({conf_str}) | 종합점수: {total}/100",
        f"  - 기술 {ts}/30 · 펀더멘털 {fs}/40 · 거시 {ms}/20 · 뉴스 {ns}/10",
        "",
    ]

    # 확신도 분석
    if conf:
        L.append("**확신도 분석**")
        fac = " · ".join(f"{f['name']} {f['score']}/{f['max']}" for f in conf.get("factors", []))
        if fac:
            L.append(f"- {fac}")
        for r in conf.get("reasons", []):
            L.append(f"  - {r}")
        for h in conf.get("hints", []):
            L.append(f"  - 💡 {h}")
        L.append("")

    # 기술적
    if ta.get("ok"):
        s200 = f" · SMA200 {ta['sma200']:,.0f}" if ta.get("sma200") else ""
        L += [
            "**기술적 지표**",
            f"- 현재가 {ta['price']:,.2f} | RSI {ta['rsi']} | MACD hist {ta['macd_hist']:+.4f}",
            f"- SMA20 {ta['sma20']:,.0f} · SMA50 {ta['sma50']:,.0f}{s200}",
            f"- BB 상단 {ta['bb_upper']:,.0f} / 하단 {ta['bb_lower']:,.0f}",
        ]
        rets = []
        if ta.get("ret_1m") is not None:  rets.append(f"1M {ta['ret_1m']:+.1f}%")
        if ta.get("ret_3m") is not None:  rets.append(f"3M {ta['ret_3m']:+.1f}%")
        if ta.get("ret_6m") is not None:  rets.append(f"6M {ta['ret_6m']:+.1f}%")
        if rets:  L.append(f"- 수익률: {' / '.join(rets)}")
        L.append("")

    # 펀더멘털
    pe  = info.get("trailingPE") or info.get("forwardPE")
    roe = info.get("returnOnEquity")
    de  = info.get("debtToEquity")
    div = info.get("dividendYield")
    eg  = info.get("earningsGrowth") or info.get("revenueGrowth")
    L.append("**펀더멘털**")
    parts = []
    if pe:   parts.append(f"P/E {pe:.1f}")
    if roe:  parts.append(f"ROE {roe*100:.1f}%")
    if de:   parts.append(f"부채비율 {de:.0f}%")
    if eg:   parts.append(f"이익성장 {eg*100:.1f}%")
    if div:  parts.append(f"배당 {div*100:.2f}%")
    L.append(f"- {' | '.join(parts)}" if parts else "- 재무 지표 미수신")
    if quant.get("cashflow_pattern"):
        L.append(f"- 현금흐름 패턴: {quant['cashflow_pattern']}")
    L.append("")

    # 계량 검증 (§4)
    L += [
        "**계량 검증 (§4 공식)**",
        f"- 기대값: {quant['ev']:+.1f}% | 켈리 권장비중: {quant['half_kelly']:.1f}% (하프켈리)",
        f"- 밸류에이션: {quant['valuation']}",
        f"- _{quant['ev_assumption']}_",
        "",
    ]

    # Bull / Bear
    if bulls:
        L.append("**강세 신호 (Bull)**")
        for s in bulls[:4]:  L.append(f"  - 🟢 {s}")
    if bears:
        L.append("**약세·리스크 신호 (Bear)**")
        for s in bears[:4]:  L.append(f"  - 🔴 {s}")
    if bulls or bears:  L.append("")

    # 뉴스
    if news_lines:
        L.append("**최근 뉴스**")
        for n in news_lines:  L.append(f"  - {n}")
        L.append("")

    return "\n".join(L)


# ── 11. 개별 종목 분석 ───────────────────────────────────────────────────────

def analyze_stock_algo(
    ticker: str,
    market: str,
    date_str: str,
    macro_data: dict | None = None,
) -> dict:
    """
    LLM-free stock analysis. Same output contract as pipeline.analyze_stock().
    """
    ds = USDataSource() if market.upper() != "KR" else KRDataSource()

    def _fund():   return ds.get_financials(ticker)
    def _news():   return ds.get_news(ticker, days=7)   # 3→7일: 심리 신호 근거 보강
    def _macro():  return macro_data or USDataSource().get_macro_data()
    def _price():  return _fetch_price_df(ticker, market)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {
            pool.submit(_fund):  "fund",
            pool.submit(_news):  "news",
            pool.submit(_macro): "macro",
            pool.submit(_price): "price",
        }
        raw: dict = {}
        for fut in list(futs):
            key = futs[fut]
            try:
                raw[key] = fut.result(timeout=30)
            except Exception as e:
                logger.warning("Fetch %s/%s failed: %s", ticker, key, e)
                raw[key] = {} if key != "news" else []

    fund   = raw.get("fund", {})
    news   = raw.get("news", [])
    macro  = raw.get("macro", {})
    df     = raw.get("price", pd.DataFrame())

    # Layer 1 — 4개 분야 점수
    ta               = _technical_indicators(df)
    ts, t_notes      = _score_technical(ta)
    fs, f_notes      = _score_fundamental(fund)
    ms, m_notes      = _score_macro(macro)
    ns, news_lines   = _score_news(news)

    total = ts + fs + ms + ns
    vd, _band_cf = _verdict(total)

    # Layer 2 — Bull / Bear 신호
    bulls = _bull_signals(ta, fund, macro)
    bears = _bear_signals(ta, fund, macro)

    # Layer 3 — 계량 검증 (§4)
    quant = _quant_metrics(ts, fs, ms, fund, macro)

    # Layer 3b — 확신도 (데이터 커버리지 · 신호 일치 · 우위 · 경계선 · 뉴스)
    _info = fund.get("info", {}) or {}
    coverage = {
        "technical_ok": bool(ta.get("ok")),
        "has_ma200": ta.get("sma200") is not None,
        "has_pe": bool(_info.get("trailingPE") or _info.get("forwardPE")),
        "has_roe": _info.get("returnOnEquity") is not None,
        "has_cashflow": (fund.get("cashflow", {}) or {}).get("operating") is not None,
    }
    conf = analysis_confidence(
        {"technical": ts, "fundamental": fs, "macro": ms, "news": ns},
        coverage, len(bulls), len(bears), len(news),
    )
    conf["hints"] = improvement_hints(conf, coverage, len(news), market)
    cf = conf["grade"]

    # Layer 4 — 리포트
    report_md = _stock_report_md(
        ticker, market, date_str,
        ta, fund, macro,
        ts, fs, ms, ns,
        bulls, bears, quant, news_lines, conf,
    )

    key_reasons = (bulls if vd in ("매수", "추가매수") else bears)[:3]
    warnings    = bears[:2] if vd in ("매수", "추가매수") else []

    return {
        "ticker":  ticker,
        "market":  market,
        "date":    date_str,
        "scores":  {"technical": ts, "fundamental": fs, "macro": ms, "news": ns, "total": total},
        "technical":       ta,
        "fundamental":     fund,
        "macro":           macro,
        "news_sentiment":  {"items": news_lines, "score": ns},
        "raw_news":        news,
        "bull_signals":    bulls,
        "bear_signals":    bears,
        "quant_risk":      quant,
        "confidence":      conf,
        "research_summary": {
            "verdict": f"강세 신호 {len(bulls)}개 / 약세 신호 {len(bears)}개",
        },
        "advice": {
            "verdict":    vd,
            "confidence": cf,
            "key_reasons": key_reasons,
            "warnings":    warnings,
            "brief_section": report_md,
        },
    }


# ── 12. 뉴스 분류 (스케줄러 watcher용) ──────────────────────────────────────

def classify_news_algo(headline: str, summary: str) -> str:
    """촉매/중립/저해 — LLM 없는 키워드 매칭."""
    text = (headline + " " + summary).lower()
    pos = sum(1 for kw in _POS_KW if kw.lower() in text)
    neg = sum(1 for kw in _NEG_KW if kw.lower() in text)
    if pos > neg:   return "촉매"
    if neg > pos:   return "저해"
    return "중립"


# ── 13. 아침 브리핑 ──────────────────────────────────────────────────────────

def morning_brief_algo(watchlist: list[dict], date_str: str | None = None) -> str:
    if date_str is None:
        date_str = date.today().isoformat()

    shared_macro = USDataSource().get_macro_data()
    results: list[dict] = []

    for stock in watchlist:
        ticker, market = stock["ticker"], stock["market"]
        logger.info("Algo analyzing %s [%s]", ticker, market)
        try:
            r = analyze_stock_algo(ticker, market, date_str, macro_data=shared_macro)
            results.append(r)
        except Exception as e:
            logger.error("Algo analysis failed %s: %s", ticker, e)
            results.append({"ticker": ticker, "market": market, "error": str(e)})

    return _format_brief(date_str, shared_macro, results)


def _format_brief(date_str: str, macro: dict, results: list[dict]) -> str:
    vix  = macro.get("VIX", "-")
    cape = macro.get("CAPE", "-")
    gold = macro.get("Gold", "-")
    ukrw = macro.get("USD/KRW", "-")
    r10y = macro.get("10Y 금리", "-")

    cape_j = ""
    c = _parse_float(macro.get("CAPE"))
    if c:  cape_j = f" → {cape_judgment(c)}"

    L = [
        f"# 📈 아침 브리핑 (알고리즘) — {date_str}",
        "",
        "## 🌍 오늘의 시장",
        f"- VIX: **{vix}** | CAPE: **{cape}**{cape_j} | 금: {gold}",
        f"- USD/KRW: {ukrw} | 미국 10Y 금리: {r10y}",
        "",
        "## 📊 보유 종목별 브리핑",
    ]

    for r in results:
        ticker = r["ticker"]
        market = r.get("market", "")
        if "error" in r:
            L.append(f"### {ticker} [{market}] — ⚠️ 오류: {r['error']}")
            continue

        advice = r.get("advice", {})
        vd     = advice.get("verdict", "보류")
        cf     = advice.get("confidence", "-")
        scores = r.get("scores", {})
        quant  = r.get("quant_risk", {})
        emoji  = {"매수": "🟢", "추가매수": "🔵", "보유": "🟡", "매도": "🔴"}.get(vd, "⚪")

        L.append(f"### {ticker} [{market}]")
        L.append(f"- **결론**: {emoji} **{vd}** (확신도: {cf}) | 종합점수: {scores.get('total', '-')}/100")
        for reason in advice.get("key_reasons", [])[:3]:
            L.append(f"- {reason}")
        L.append(
            f"- 기대값: {quant.get('ev', '-')}% | 켈리 권장비중: {quant.get('half_kelly', '-')}% (하프켈리)"
        )
        L.append(f"- 밸류에이션: {quant.get('valuation', '-')}")
        if quant.get("cashflow_pattern"):
            L.append(f"- 현금흐름: {quant['cashflow_pattern']}")
        for w in advice.get("warnings", [])[:2]:
            L.append(f"- ⚠️ {w}")
        L.append("")

    L += [
        "## ⚠️ 면책",
        "본 브리핑은 알고리즘 기반 정보 제공 목적이며 투자 자문이 아닙니다. "
        "모든 수치·승률·기대값은 가정에 근거한 추정치이며 "
        "최종 투자 판단과 책임은 사용자에게 있습니다.",
    ]

    return "\n".join(L)
