#!/usr/bin/env python3
"""
민감정보 가드 — 커밋·푸시 전에 실행 (pre-commit / pre-push 훅).

차단 대상:
  1. DB 파일(*.db / *.db-wal / *.db-shm / *.sqlite) 이 git 에 추적되는 경우
     → 개인 관심종목·가계부·자산·API 키가 들어있음. data/ 는 .gitignore 대상이고
       앱 기동 시 `create_all_tables()` 가 빈 스키마를 자동 생성하므로 커밋 불필요.
  2. .env (.env.example 제외) 가 추적되는 경우.
  3. 스테이징된 텍스트 파일에 API 키/개인 키로 보이는 문자열이 포함된 경우.

문제가 없으면 exit 0, 있으면 exit 1 + 해결 안내.
"""

import re
import subprocess
import sys

BLOCKED_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3")
BLOCKED_BASENAMES = {".env"}
ALLOWED_BASENAMES = {".env.example", ".env.sample"}

SECRET_PATTERNS = [
    (re.compile(r"sk-ant-(?:api\d\d-)?[A-Za-z0-9_\-]{24,}"), "Anthropic API 키"),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b"), "OpenAI API 키"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "개인 키"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS 액세스 키"),
]


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line]


def main() -> int:
    tracked = set(_git("ls-files"))
    staged = set(_git("diff", "--cached", "--name-only"))
    problems: list[str] = []

    for path in sorted(tracked | staged):
        base = path.rsplit("/", 1)[-1]
        if path.endswith(BLOCKED_SUFFIXES):
            problems.append(
                f"{path} — DB 파일은 커밋 금지. 개인 데이터 포함. "
                f"`git rm --cached {path}` 후 .gitignore 확인 (앱이 빈 DB 자동 생성)"
            )
        if base in BLOCKED_BASENAMES and base not in ALLOWED_BASENAMES:
            problems.append(f"{path} — .env 는 커밋 금지. .env.example 만 공유하세요")

    # 스테이징된 텍스트 파일 내용 스캔 (빠르게, staged 만)
    for path in sorted(staged):
        base = path.rsplit("/", 1)[-1]
        if path.endswith(BLOCKED_SUFFIXES) or base in ALLOWED_BASENAMES:
            continue
        try:
            content = subprocess.run(
                ["git", "show", f":{path}"], capture_output=True, text=True
            ).stdout
        except Exception:
            continue
        if "\x00" in content[:1024]:  # 바이너리 스킵
            continue
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(content):
                problems.append(f"{path} — {label} 로 보이는 문자열 포함")
                break

    if problems:
        print("커밋/푸시 차단 — 민감정보가 감지되었습니다:", file=sys.stderr)
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
