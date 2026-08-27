#!/bin/sh
# Git 훅 설치 — 푸시/커밋 전 민감정보(DB·.env·API 키) 가드.
# pre-commit 패키지가 있으면 그걸 쓰고, 없으면 네이티브 훅을 심는다.
set -e
cd "$(dirname "$0")/.."

if command -v pre-commit >/dev/null 2>&1; then
  pre-commit install --hook-type pre-commit --hook-type pre-push
  echo "pre-commit 훅 설치 완료 (pre-commit + pre-push)"
else
  HOOK=".git/hooks/pre-push"
  cat > "$HOOK" <<'EOF'
#!/bin/sh
# StockAI — 푸시 전 민감정보 가드
exec python3 scripts/guard_secrets.py
EOF
  chmod +x "$HOOK"
  echo "네이티브 pre-push 훅 설치 완료: $HOOK"
  echo "(pre-commit 패키지를 설치하면 커밋 단계 가드도 추가됩니다: pip install pre-commit)"
fi
