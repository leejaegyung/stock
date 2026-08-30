"""
외신 뉴스 한국어 번역 — LLM 미사용.

전용 기계번역(NMT) 엔드포인트를 백엔드 체인으로 호출한다.
결과는 web 레이어에서 NewsItem 에 캐시되므로 기사당 1회만 호출된다.
원문 URL 은 손대지 않는다 — 사용자는 항상 원문으로 이동할 수 있다.

레이어: 유틸 (app import 없음, httpx + 표준 라이브러리만).
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


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
    """문장 경계를 존중하며 분할 (번역 엔드포인트 길이 제한 회피)."""
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


def _google_gtx(text: str, src: str) -> str | None:
    try:
        r = httpx.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": src, "tl": "ko", "dt": "t", "q": text},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8.0,
        )
        if r.status_code == 200:
            data = r.json()
            joined = "".join(seg[0] for seg in data[0] if seg and seg[0])
            return joined or None
    except Exception as e:
        logger.debug("gtx translate failed: %s", e)
    return None


def _mymemory(text: str, src: str) -> str | None:
    try:
        r = httpx.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:500], "langpair": f"{src}|ko"},
            timeout=8.0,
        )
        if r.status_code == 200:
            t = (r.json().get("responseData") or {}).get("translatedText")
            if t and "MYMEMORY WARNING" not in t.upper():
                return t
    except Exception as e:
        logger.debug("mymemory translate failed: %s", e)
    return None


def translate_to_ko(text: str, src: str = "en") -> tuple[str, bool]:
    """
    (번역문, 성공여부). 이미 한국어거나 번역 실패 시 원문을 그대로 돌려준다.
    """
    text = (text or "").strip()
    if not text or detect_lang(text) == "ko":
        return text, False

    parts = _chunks(text)
    pieces: list[str] = []
    ok_any = False
    for p in parts:
        res = _google_gtx(p, src) or _mymemory(p, src)
        if res:
            pieces.append(res.strip())
            ok_any = True
        else:
            pieces.append(p.strip())

    joined = " ".join(x for x in pieces if x).strip()
    return (joined, True) if (ok_any and joined) else (text, False)
