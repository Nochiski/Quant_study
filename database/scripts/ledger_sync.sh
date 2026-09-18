#!/usr/bin/env bash
# 카엘 서버 SFTP → 로컬 equity 층 동기화 CLI 래퍼 (bash). Windows 는 ledger_sync.ps1.
#
#   database/scripts/ledger_sync.sh plan
#   database/scripts/ledger_sync.sh sync            # pull → verify → catalog
#   database/scripts/ledger_sync.sh verify --level hash
#   database/scripts/ledger_sync.sh status --remote
#
# backend 프로젝트 환경(duckdb·pyarrow)에 paramiko 만 얹어 `python -m ledger_sync` 를 돈다.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT/database/src"
export PYTHONUTF8=1
exec uv run --project "$REPO_ROOT/backend" --with paramiko python -m ledger_sync "$@"
