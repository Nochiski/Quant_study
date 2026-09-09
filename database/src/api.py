"""정합성 검증용 공통 API 클라이언트. 조회 전용."""
import json
import os
import sys
import time
import warnings

import requests

warnings.filterwarnings("ignore")

# 경로를 박아두면 서버에서 뜨지 않는다. 맥(~/Desktop/...)과 서버(~/...)가 다르므로 후보를 순회한다.
def _find_env():
    for p in (os.environ.get("QL_ENV"),
              os.path.expanduser("~/kael-system-v3/.env"),
              os.path.expanduser("~/Desktop/kael-system-v3/.env")):
        if p and os.path.exists(p):
            return p
    raise FileNotFoundError("kael .env 를 찾을 수 없다. QL_ENV 로 지정하라")
ENV = _find_env()
_K = {}
with open(ENV, encoding="utf-8") as _env_f:
    _ENV_LINES = _env_f.read().splitlines()
for _l in _ENV_LINES:
    _l = _l.strip()
    for _k in ("KRX_API_KEY","KRX_ID","KRX_PW","KIS_APP_KEY","KIS_APP_SECRET",
               "KIWOOM_APP_KEY","KIWOOM_SECRET_KEY","DART_API_KEY","DART_API_KEY_2",
               "DART_API_KEY_3","DART_API_KEY_4","DART_API_KEY_5"):
        if _l.startswith(_k + "="):
            _K[_k] = _l.split("=", 1)[1].strip().strip('"\'')
os.environ["KRX_ID"] = _K.get("KRX_ID",""); os.environ["KRX_PW"] = _K.get("KRX_PW","")

def num(v):
    """'1,234' / '+1,234' / '-' / '' → float"""
    s = str(v).replace(",", "").replace("+", "").strip()
    if s in ("", "-", "None"): return None
    try: return float(s)
    except ValueError:
        # 조용히 None 을 돌려주면 파싱 실패가 결측으로 위장된다 (키움 '--1234' 이중부호 등)
        print(f"[num] 파싱 실패 → None: {v!r}", file=sys.stderr)
        return None

# ── KRX 공식 OPEN API (기준 소스) ────────────────────────────────
_krx = requests.Session(); _krx.headers.update({"AUTH_KEY": _K["KRX_API_KEY"]})
KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis"

def krx(path, basDd):
    """KRX 공식. 반환: list[dict]. 휴장/미래/포맷오류는 모두 빈 리스트."""
    r = _krx.get(f"{KRX_BASE}/{path}", params={"basDd": basDd}, timeout=90)
    r.raise_for_status()
    return r.json().get("OutBlock_1") or []

# ── KIS ──────────────────────────────────────────────────────────
KIS_BASE = "https://openapi.koreainvestment.com:9443"
_KIS_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".kis_token.json")
_kis_tok = None
def _kis_token():
    """KIS 토큰은 24h 유효하고 재발급 호출 자체에 제한이 있다 → 파일 캐싱 필수."""
    global _kis_tok
    if _kis_tok: return _kis_tok
    import json as _json
    if os.path.exists(_KIS_CACHE):
        try:
            with open(_KIS_CACHE) as _f:
                c = _json.load(_f)
            if time.time() < c["issued_at"] + 23 * 3600:
                _kis_tok = c["token"]; return _kis_tok
        except Exception:  # noqa: BLE001, S110  # reason: 토큰 캐시 손상·부재는 아래 재발급으로 복구된다
            pass
    r = requests.post(f"{KIS_BASE}/oauth2/tokenP", timeout=30,
        json={"grant_type":"client_credentials","appkey":_K["KIS_APP_KEY"],
              "appsecret":_K["KIS_APP_SECRET"]}).json()
    if "access_token" not in r:
        raise RuntimeError(f"KIS 토큰 발급 실패: {r}")
    _kis_tok = r["access_token"]
    with open(_KIS_CACHE, "w") as _f:
        _json.dump({"token": _kis_tok, "issued_at": time.time()}, _f)
    return _kis_tok

def kis(url, tr_id, params):
    h = {"content-type":"application/json; charset=utf-8",
         "authorization": f"Bearer {_kis_token()}", "appkey": _K["KIS_APP_KEY"],
         "appsecret": _K["KIS_APP_SECRET"], "tr_id": tr_id, "custtype":"P"}
    r = requests.get(f"{KIS_BASE}{url}", headers=h, params=params, timeout=30)
    time.sleep(0.3)
    return r.json()

# ── 키움 (초당 5건 제한 준수) ────────────────────────────────────
KW_BASE = "https://api.kiwoom.com"
_KW_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".kw_token.json")
_kw_tok = None
def _kw_token(force=False):
    """서버가 주는 expires_dt 를 신뢰한다. 고정 TTL 로 캐싱하면
    14시간짜리 백필 도중 만료돼 전 종목이 8005 로 죽는다."""
    global _kw_tok
    if _kw_tok and not force:
        return _kw_tok
    if not force:
        try:
            with open(_KW_CACHE) as _f:
                c = json.load(_f)
            if c.get("exp") and time.time() < c["exp"] - 600:   # 만료 10분 전에 갱신
                _kw_tok = c["token"]; return _kw_tok
        except Exception:  # noqa: BLE001, S110  # reason: 토큰 캐시 손상·부재는 아래 재발급으로 복구된다
            pass
    r = requests.post(f"{KW_BASE}/oauth2/token", timeout=30,
        headers={"Content-Type":"application/json;charset=UTF-8"},
        json={"grant_type":"client_credentials","appkey":_K["KIWOOM_APP_KEY"],
              "secretkey":_K["KIWOOM_SECRET_KEY"]}).json()
    _kw_tok = r["token"]
    exp = None
    if r.get("expires_dt"):
        try:
            from datetime import datetime as _dt
            exp = _dt.strptime(str(r["expires_dt"]), "%Y%m%d%H%M%S").timestamp()  # noqa: DTZ007  # reason: 키움 expires_dt 는 KST 벽시계 문자열, 서버 TZ 기준 epoch 비교에만 쓴다
        except Exception:  # noqa: BLE001, S110  # reason: expires_dt 형식이 바뀌어도 토큰은 유효하다 — exp=None 으로 두고 다음 콜에서 재발급
            pass
    with open(_KW_CACHE, "w") as _f:
        json.dump({"token": _kw_tok, "exp": exp, "t": time.time()}, _f)
    return _kw_tok

def kiwoom(api_id, url, body, cont=None, next_key=None):
    """반환: (json, headers)

    8005(토큰 무효)는 캐시의 expires_dt 가 아직 남았어도 서버가 먼저 폐기하면 나온다
    (2026-09-02 실측: 만료 9시간 전에 8005 — 마스터 스냅샷 0행). expires_dt 만
    믿으면 그날 관측이 통째로 유실되므로, 이 코드만 강제 재발급 후 1회 재시도한다.
    """
    def _call():
        h = {"Content-Type":"application/json;charset=UTF-8",
             "authorization": f"Bearer {_kw_token()}", "api-id": api_id}
        if cont: h["cont-yn"] = cont
        if next_key: h["next-key"] = next_key
        r = requests.post(f"{KW_BASE}{url}", json=body, headers=h, timeout=30)
        time.sleep(0.25)
        return r.json(), r.headers

    j, hdr = _call()
    if j.get("return_code") == 3 and "8005" in str(j.get("return_msg", "")):
        _kw_token(force=True)
        j, hdr = _call()
    return j, hdr

# ── DART (키당 일 20,000콜 공식 한도) ─────────────────────────────
# 키는 순차 폴백으로 쓴다. 병렬로 쏘지 않는 이유는 한 키가 막혔을 때
# 다른 키까지 같은 시각·같은 패턴으로 노출되는 것을 피하기 위함이다.
DART_BASE = "https://opendart.fss.or.kr/api"

def dart_keys():
    """(key_id, key) 순서 목록. 앞이 1순위. 여기서 순서가 곧 소진 순서다.

    우리 키(DART_API_KEY_2~_5)만 번호순으로 돌려준다 — 키가 늘면 .env 에 넣기만 하면 된다.
    카엘 프로덕션 키(DART_API_KEY)는 **목록에 넣지 않는다**(2026-09-09). 예전엔 마지막 폴백이었는데
    08-28 에 우리 키 2개가 소진되자 그 키로 넘어가 하루 한도의 89% 를 태웠다.
    """
    out = []
    for n in range(2, 6):                       # _2 .. _5
        k = _K.get(f"DART_API_KEY_{n}")
        if k:
            out.append((f"k{n}", k))
    # v3 프로덕션 키(DART_API_KEY)는 폴백에 넣지 않는다 — 08-28 에 우리 키 소진 뒤 자동 폴백으로
    # 그 키를 35,588콜 태운 사고(플랜 P0 Task 0.4). 우리 키가 없으면 빈 목록이라 호출 쪽이 실패한다.
    return out

class DartError(Exception):
    """DART 호출 실패. 메시지에서 crtfc_key 가 마스킹된 상태로만 올라온다."""


def dart(path, key=None, **params):
    """DART OpenAPI. path 예: 'list.json', 'fnlttSinglAcntAll.json', 'corpCode.xml'.
    .json 은 dict, .xml/.zip 은 bytes 를 돌려준다.
    key 를 주면 그 키로, 없으면 1순위 키로 나간다.

    예외는 전부 DartError 로 감싸 나간다. requests 가 만드는 메시지는
    ConnectionError·HTTPError 어느 쪽이든 요청 URL 을 통째로 담는데,
    crtfc_key 가 query string 에 있어 그대로 로그에 평문으로 박힌다.
    (카엘 dart.log 에 같은 사고가 2,198회 실재한다.)"""
    q = dict(params)
    k = key or dart_keys()[0][1]
    q["crtfc_key"] = k
    safe = {kk: vv for kk, vv in q.items() if kk != "crtfc_key"}
    try:
        r = requests.get(f"{DART_BASE}/{path}", params=q, timeout=60)
        if r.status_code >= 400:
            raise DartError(f"HTTP {r.status_code} — path={path} params={safe}")
        time.sleep(0.2)
        return r.json() if path.endswith(".json") else r.content
    except DartError:
        raise
    except Exception as e:  # noqa: BLE001  # reason: requests 예외 전부를 DartError 로 감싸 crtfc_key 를 마스킹한다
        msg = str(e).replace(k, "***") if k else str(e)
        raise DartError(f"{type(e).__name__} — path={path} params={safe} :: {msg}") from None
