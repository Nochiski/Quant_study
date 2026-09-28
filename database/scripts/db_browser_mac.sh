#!/usr/bin/env bash
# 맥북에서 실행: 서버 DB 조회 화면(scripts/db_browser.py)을 SSH 터널로 열고 브라우저를 띄운다.
#   사용: database/scripts/db_browser_mac.sh            (끝낼 때 Ctrl+C — 서버 쪽도 같이 끝난다)
#         PORT=4214 database/scripts/db_browser_mac.sh  (4213 이 이미 쓰이고 있을 때)
# 데이터는 서버에 그대로 있고, 맥북에는 화면과 조회 결과만 온다. 전부 읽기 전용.
set -u
PORT="${PORT:-4213}"
SCRIPT="${DB_BROWSER_SCRIPT:-scripts/db_browser.py}"
( sleep 8; open "http://localhost:$PORT" ) &
exec ssh -t -L "$PORT:localhost:$PORT" kael-server \
  "cd ~/quant-ledger && .venv/bin/python $SCRIPT --port $PORT"
