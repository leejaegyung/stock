"""번역 유틸 — 순수 로직만 테스트 (네트워크 호출 없음)."""

from app.core import translate
from app.core.translate import _chunks, detect_lang, translate_to_ko


def test_detect_lang():
    assert detect_lang("삼성전자 HBM4 공급 확대") == "ko"
    assert detect_lang("Nvidia beats earnings estimates") == "en"
    assert detect_lang("코스피 3,200 Nvidia") == "ko"  # 한글 우선
    assert detect_lang("") == "unknown"
    assert detect_lang("123 !!! ...") == "unknown"


def test_chunks_respects_length_and_sentences():
    long = ". ".join([f"Sentence number {i} here" for i in range(200)])
    parts = _chunks(long, size=200)
    assert len(parts) > 1
    assert all(len(p) <= 400 for p in parts)  # 문장 단위라 약간 초과 가능
    assert "".join(parts).replace(" ", "") != ""


def test_translate_skips_korean_text():
    out, ok = translate_to_ko("이미 한국어 기사입니다")
    assert out == "이미 한국어 기사입니다"
    assert ok is False


def test_translate_returns_original_on_total_failure(monkeypatch):
    monkeypatch.setattr(translate, "_google_gtx", lambda *a, **k: None)
    monkeypatch.setattr(translate, "_mymemory", lambda *a, **k: None)
    out, ok = translate_to_ko("Nvidia beats earnings")
    assert out == "Nvidia beats earnings"
    assert ok is False


def test_translate_uses_fallback_backend(monkeypatch):
    monkeypatch.setattr(translate, "_google_gtx", lambda *a, **k: None)
    monkeypatch.setattr(translate, "_mymemory", lambda text, src: "엔비디아 실적 호조")
    out, ok = translate_to_ko("Nvidia beats earnings")
    assert ok is True
    assert "엔비디아" in out


def test_translate_empty():
    assert translate_to_ko("") == ("", False)
    assert translate_to_ko(None) == ("", False)
