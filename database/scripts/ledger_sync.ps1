# 카엘 서버 SFTP → 로컬 equity 층 동기화 CLI 래퍼 (Windows).
#
#   database\scripts\ledger_sync.ps1 plan
#   database\scripts\ledger_sync.ps1 sync            # pull → catalog → verify (일일 작업이 부르는 동사)
#   database\scripts\ledger_sync.ps1 verify --level hash
#   database\scripts\ledger_sync.ps1 status --remote
#
# backend 프로젝트 환경(duckdb·pyarrow — `uv sync --extra parquet --extra equity`)에 paramiko 만
# 얹어 `python -m ledger_sync` 를 돈다. `equity catalog` 위임도 같은 인터프리터를 쓴다.
# 서버 주소는 기본값이 없다 — QL_SYNC_HOST 필수(공개 저장소). 계정·키 기본값: quantshare / ~/.ssh/kael_quant (QL_SYNC_*).
# 로컬 루트 기본값: ~/quant-ledger/data (QL_SYNC_ROOT).
$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$backend = Join-Path $repoRoot "backend"
# 환경변수는 프로세스 단위라 호출한 셸에도 남는다 — 끝나면 원래 값으로 되돌린다.
$savedPythonPath = $env:PYTHONPATH
$savedUtf8 = $env:PYTHONUTF8
try {
    $env:PYTHONPATH = (Join-Path $repoRoot "database\src")
    $env:PYTHONUTF8 = "1"
    & uv run --project $backend --with "paramiko>=3.4,<4" python -m ledger_sync @args
    $code = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $savedPythonPath
    $env:PYTHONUTF8 = $savedUtf8
}
exit $code
