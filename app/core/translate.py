"""
외신 뉴스 한국어 번역 — LLM 미사용.

전용 기계번역(NMT) 백엔드를 순서대로 시도한다:
  1. DeepL   (DEEPL_API_KEY 설정 시 — 품질·안정성 우수, 무료 50만자/월)
  2. Google 비공식 gtx 엔드포인트 (키 불필요)
  3. MyMemory (키 불필요, 한도 빡빡)

무료 엔드포인트는 한도가 있어, 429 를 받으면 `_backoff_until` 까지 전 백엔드
호출을 건너뛴다. 결과는 web 레이어에서 NewsItem 에 캐시되므로 기사당 1회만 호출.
원문·원문 URL 은 손대지 않는다.

레이어: 유틸 (app import 없음, httpx + 표준 라이브러리만).
"""

from __future__ import annotations

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

_backoff_until = 0.0        # 이 시각까지 무료 엔드포인트 호출 중단
_last_call = 0.0            # 마지막 호출 시각 (요청 간 최소 간격 확보)
_MIN_INTERVAL = 1.2        # 초


def detect_lang(text: str) -> str:
    """단순 판별 — 한글이 있으면 ko, 라틴 문자가 있으면 en, 그 외 unknown."""
    if not text:
        return "unknown"
    hangul = sum(1 for c in text if 0xAC00 <= ord(c) <= 0xD7A3)
    if hangul >= 2:
        return "ko"
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    return "en" if latin >= 3 else "unknown"


def _chunks(text: str, size: int = 1600) -> list[str]:
    """문장 경계를 존중하며 분할."""
    out: list[str] = []
    cur = ""
    for part in text.replace("\n", " ").split(". "):
        seg = part + ". "
        if len(cur) + len(seg) > size and cur:
            out.append(cur)
            cur = seg
        else:
            cur += seg
    if cur.strip():
        out.append(cur)
    return out or [text]


def _throttle() -> None:
    global _last_call
    wait = _MIN_INTERVAL - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def _deepl(text: str, src: str) -> str | None:
    key = os.environ.get("DEEPL_API_KEY", "").strip()
    if not key:
        return None
    host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
    try:
        r = httpx.post(
            f"https://{host}/v2/translate",
            data={"text": text, "source_lang": src.upper(), "target_lang": "KO"},
            headers={"Authorization": f"DeepL-Auth-Key {key}"},
            timeout=10.0,
        )
        if r.status_code == 200:
            trs = r.json().get("translations") or []
            return trs[0]["text"] if trs else None
        logger.debug("deepl status %s", r.status_code)
    except Exception as e:
        logger.debug("deepl failed: %s", e)
    return None


def _mark_backoff(resp: httpx.Response) -> None:
    global _backoff_until
    ra = resp.headers.get("Retry-After")
    secs = 900.0
    if ra and ra.isdigit():
        secs = min(6 * 3600, max(300, float(ra)))
    _backoff_until = time.time() + secs
    logger.warning("translate backoff %ds (429 from %s)", int(secs), resp.url.host)


def _google_gtx(text: str, src: str) -> str | None:
    try:
        r = httpx.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": src, "tl": "ko", "dt": "t", "q": text},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8.0,
        )
        if r.status_code == 429:
            _mark_backoff(r)
        elif r.status_code == 200:
            data = r.json()
            joined = "".join(seg[0] for seg in data[0] if seg and seg[0])
            return joined or None
    except Exception as e:
        logger.debug("gtx failed: %s", e)
    return None


def _mymemory(text: str, src: str) -> str | None:
    try:
        r = httpx.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:500], "langpair": f"{src}|ko"},
            timeout=8.0,
        )
        if r.status_code == 429:
            _mark_backoff(r)
        elif r.status_code == 200:
            t = (r.json().get("responseData") or {}).get("translatedText")
            if t and "MYMEMORY WARNING" not in t.upper():
                return t
    except Exception as e:
        logger.debug("mymemory failed: %s", e)
    return None


def _translate_chunk(text: str, src: str) -> str | None:
    # DeepL 은 백오프 무관 (별도 한도)
    res = _deepl(text, src)
    if res:
        return res
    if time.time() < _backoff_until:
        return None
    _throttle()
    return _google_gtx(text, src) or _mymemory(text, src)


def translate_to_ko(text: str, src: str = "en") -> tuple[str, bool]:
    """(번역문, 성공여부). 이미 한국어거나 번역 실패 시 원문 그대로."""
    text = (text or "").strip()
    if not text or detect_lang(text) == "ko":
        return text, False

    pieces: list[str] = []
    ok_any = False
    for p in _chunks(text):
        res = _translate_chunk(p, src)
        if res:
            pieces.append(res.strip())
            ok_any = True
        else:
            pieces.append(p.strip())

    joined = " ".join(x for x in pieces if x).strip()
    return (joined, True) if (ok_any and joined) else (text, False)


def backoff_active() -> bool:
    """무료 엔드포인트가 현재 백오프 중인지 (스케줄러가 헛돌지 않도록)."""
    return os.environ.get("DEEPL_API_KEY", "").strip() == "" and time.time() < _backoff_until
