"""키움 REST API 유량 한도 실측.
계측 원칙: HTTP status 와 return_code 를 둘 다 본다.
키움은 200 + return_code:5 로 거부하는 경로가 있어서 status 만 보면 놓친다."""
import time, json, sys, os, threading
from collections import Counter
import requests

sys.path.insert(0, "/private/tmp/claude-501/-Users-claudeoscarmonet-Desktop-Quant-study/ce1c4d93-92e2-41b2-8b7a-02d1b0e30c5e/scratchpad/xcheck")
import api as A

KW = "https://api.kiwoom.com"
TOK = A._kw_token()
LOG = []
LK = threading.Lock()

# 테스트용 TR (카엘이 실제로 쓰는 것과 동일)
TRS = {
    "ka10001": ("/api/dostk/stkinfo", {"stk_cd": "005930"}),          # 주식기본정보
    "ka10081": ("/api/dostk/chart",   {"stk_cd": "005930", "base_dt": "20260820", "upd_stkpc_tp": "1"}),  # 일봉
    "ka10014": ("/api/dostk/shsa",    {"stk_cd": "005930", "tm_tp": "1", "strt_dt": "20260801", "end_dt": "20260820"}),  # 공매도추이
    "ka10059": ("/api/dostk/stkinfo", {"dt": "20260820", "stk_cd": "005930", "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1000"}),  # 투자자별
    "ka20068": ("/api/dostk/slb",     {"stk_cd": "005930", "strt_dt": "20260801", "end_dt": "20260820", "all_tp": "0"}),  # 대차잔고
}

def raw(api_id, tag=""):
    """슬립 없는 순수 1콜. 반환 (status, return_code, elapsed, msg)"""
    url, body = TRS[api_id]
    h = {"Content-Type": "application/json;charset=UTF-8",
         "authorization": f"Bearer {TOK}", "api-id": api_id}
    t0 = time.time()
    try:
        r = requests.post(KW + url, json=body, headers=h, timeout=30)
        el = time.time() - t0
        st = r.status_code
        try:
            j = r.json(); rc = j.get("return_code"); msg = str(j.get("return_msg", ""))[:60]
        except Exception:
            rc = None; msg = r.text[:60]
    except Exception as e:
        st, rc, el, msg = -1, None, time.time() - t0, f"{type(e).__name__}:{e}"[:60]
    rec = dict(t=t0, api=api_id, tag=tag, st=st, rc=rc, el=round(el, 3), msg=msg)
    with LK: LOG.append(rec)
    return st, rc, el, msg

def blocked(st, rc):
    """한도 거부 판정: HTTP 429 또는 return_code 가 0 이 아닌 유량 계열"""
    return st == 429 or (rc not in (0, None) and st == 200)

def save(name):
    p = f"/private/tmp/claude-501/-Users-claudeoscarmonet-Desktop-Quant-study/ce1c4d93-92e2-41b2-8b7a-02d1b0e30c5e/scratchpad/ratetest/{name}.json"
    json.dump(LOG, open(p, "w"), ensure_ascii=False, indent=1)
    print(f"  → {p} ({len(LOG)}건)")
