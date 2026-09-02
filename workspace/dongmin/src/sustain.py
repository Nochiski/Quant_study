"""지속 부하 + 실제 수집. ka10014(공매도) · ka20068(대차잔고) 전종목 병렬.
버리는 콜이 아니다 — 응답을 JSONL 로 남겨 R6-4/R6-5 백필의 일부로 재사용한다.
목적: 일일 누적 한도의 존재 여부를 실측한다."""
import time, json, sys, os, threading
import requests
sys.path.insert(0, "/private/tmp/claude-501/-Users-claudeoscarmonet-Desktop-Quant-study/ce1c4d93-92e2-41b2-8b7a-02d1b0e30c5e/scratchpad/xcheck")
import api as A

BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, "raw"); os.makedirs(RAW, exist_ok=True)
KW = "https://api.kiwoom.com"
TOK = A._kw_token()
TICKERS = [l.strip() for l in open(os.path.join(BASE, "tickers.txt")) if l.strip()]

END, STRT = "20260820", "20250101"
JOBS = {
    "ka10014": ("/api/dostk/shsa", lambda t: {"stk_cd": t, "tm_tp": "1", "strt_dt": STRT, "end_dt": END}),
    "ka20068": ("/api/dostk/slb",  lambda t: {"stk_cd": t, "strt_dt": STRT, "end_dt": END, "all_tp": "0"}),
}
RATE = 4.4          # TR 당 목표. 실측 상한 5.0 아래로 잡는다
STAT = {}
SL = threading.Lock()

def run(api_id):
    url, mk = JOBS[api_id]
    f = open(os.path.join(RAW, f"{api_id}.jsonl"), "w")
    h = {"Content-Type": "application/json;charset=UTF-8",
         "authorization": f"Bearer {TOK}", "api-id": api_id}
    gap = 1.0 / RATE
    n = ok = r429 = err = rows = retry = 0
    t0 = time.time()
    for i, tk in enumerate(TICKERS):
        s = time.time()
        for attempt in range(4):                    # 429 는 500ms 백오프 재시도
            try:
                r = requests.post(KW + url, json=mk(tk), headers=h, timeout=30)
                n += 1
                if r.status_code == 429:
                    r429 += 1; retry += 1; time.sleep(0.5); continue
                j = r.json()
                if j.get("return_code") == 0:
                    ok += 1
                    lst = next((v for v in j.values() if isinstance(v, list)), [])
                    rows += len(lst)
                    f.write(json.dumps({"tk": tk, "n": len(lst), "d": lst}, ensure_ascii=False) + "\n")
                else:
                    err += 1
                break
            except Exception:
                n += 1; err += 1; time.sleep(0.3)
        with SL:
            STAT[api_id] = dict(i=i+1, n=n, ok=ok, r429=r429, err=err, rows=rows,
                                rate=round(n/(time.time()-t0), 2), el=int(time.time()-t0))
        time.sleep(max(0, gap - (time.time() - s)))
    f.close()

def reporter(stop):
    while not stop.is_set():
        with SL: snap = dict(STAT)
        line = " | ".join(f"{k} {v['i']}/{len(TICKERS)} ok={v['ok']} 429={v['r429']} {v['rate']}/s" for k, v in sorted(snap.items()))
        open(os.path.join(BASE, "progress.txt"), "w").write(
            f"{time.strftime('%H:%M:%S')}  {line}\n" + json.dumps(snap, indent=1))
        stop.wait(5)

if __name__ == "__main__":
    stop = threading.Event()
    rp = threading.Thread(target=reporter, args=(stop,), daemon=True); rp.start()
    ts = [threading.Thread(target=run, args=(a,)) for a in JOBS]
    t0 = time.time()
    for t in ts: t.start()
    for t in ts: t.join()
    stop.set(); dur = time.time() - t0
    tot = sum(v["n"] for v in STAT.values())
    print(f"\n완료 {dur:.0f}초, 총 {tot}콜, 합산 {tot/dur:.2f}콜/s")
    for a, v in sorted(STAT.items()):
        print(f"  {a}: {v['n']}콜 ok={v['ok']} 429={v['r429']} err={v['err']} rows={v['rows']:,} {v['rate']}/s")
    json.dump(STAT, open(os.path.join(BASE, "sustain_result.json"), "w"), indent=1)
