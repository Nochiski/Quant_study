#!/bin/bash
# survey v2 러너 — 원장 5 DB 전 테이블·전 컬럼 전수 어휘 측정 (STAGE_DESIGN v2.2 §10 선행 조건 2).
#   체크포인트 없음(전체 20~30분). 종료 코드 1 = 커버리지 미달(미조사 테이블/컬럼 존재).
#   산출: survey_out/v2/{db}.{table}.json · ps_table.json · coverage.json
cd "$HOME/quant-ledger"
export QL_HOME="$HOME/quant-ledger"
{
echo "════ survey v2 시작 $(TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST') ════"
.venv/bin/python survey/survey_v2.py "$@"
echo "════ 종료 rc=$? $(TZ=Asia/Seoul date '+%H:%M:%S KST') ════"
} >> logs/survey_v2.log 2>&1
