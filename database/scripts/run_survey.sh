#!/bin/bash
# 전수조사 러너 — ledger 조사 후 cross 대조. 재실행 시 체크포인트로 이어감.
cd "$HOME/quant-ledger"
export QL_HOME="$HOME/quant-ledger"
{
echo "════ 전수조사 시작 $(TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST') ════"
.venv/bin/python survey/survey_ledger.py
echo "──── cross ────"
.venv/bin/python survey/survey_cross.py
echo "════ 종료 $(TZ=Asia/Seoul date '+%H:%M:%S KST') ════"
} >> logs/survey.log 2>&1
