#!/usr/bin/env python3
"""
로컬 DB 초기화 — 모든 행 삭제, 테이블 스키마는 유지.

관심종목 · 분석 보고서 · 뉴스 · 가계부 · 자산 · 고정거래 · API 키 · 순자산 스냅샷을
전부 비운다. 커밋/푸시 전에 "깨끗한 상태"로 되돌리고 싶을 때 사용.

사용:
    python scripts/reset_db.py            # 확인 후 삭제
    python scripts/reset_db.py --yes      # 확인 없이 삭제
    python scripts/reset_db.py --keep-keys  # user_api_key 는 보존
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.client import get_engine  # noqa: E402
from app.db.models import Base, create_all_tables  # noqa: E402


def main() -> None:
    args = set(sys.argv[1:])
    keep_keys = "--keep-keys" in args
    db_path = settings.db_path

    if "--yes" not in args:
        resp = input(f"'{db_path}' 의 모든 데이터를 삭제합니다. 계속하시겠습니까? [y/N] ")
        if resp.strip().lower() != "y":
            print("취소되었습니다.")
            return

    create_all_tables(db_path)
    engine = get_engine(db_path)

    with engine.begin() as conn:
        has_seq = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'")
        ).first()
        for table in reversed(Base.metadata.sorted_tables):
            if keep_keys and table.name == "user_api_key":
                continue
            conn.execute(text(f'DELETE FROM "{table.name}"'))
            if has_seq:
                conn.execute(text("DELETE FROM sqlite_sequence WHERE name = :n"), {"n": table.name})
    with engine.begin() as conn:
        conn.execute(text("VACUUM"))

    kept = " (API 키 보존)" if keep_keys else ""
    print(f"초기화 완료 — {db_path}{kept}")


if __name__ == "__main__":
    main()
