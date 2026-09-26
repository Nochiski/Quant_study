"""backfill_wise.probe_coverage — 커버 판정은 대상 3개년을 다 본 뒤에만 none 이다.

검수 D H1(2026-09-10): 당해 연도(ymms[0]) 의 cF5001 만 보고 none 을 확정해 09-10 커버 상실 4건 중
3건(036010·190510·342870)이 오탐이었다 — FY2027 추정치가 실재했다. 연말로 갈수록 당해 연도가
먼저 비므로 오탐이 늘어난다. false-none 은 그날 스냅샷의 영구 손실이다.
"""
import json

import backfill_wise as bw

YMMS = ["202612", "202712", "202812"]


def _fetch(covered_years, fail_years=()):
    calls = []

    def fetch(cmp_cd, ep, pk, url):
        calls.append((ep, pk))
        if pk in fail_years:
            return cmp_cd, "http/500", b"", 0, 1
        c1 = {"select_item": [1 if pk in covered_years else None], "target_price": [None]}
        body = json.dumps({"chart1": json.dumps(c1), "chart2": json.dumps({"select_item": [None]})}).encode()
        return cmp_cd, "ok", body, len(body), 1
    return fetch, calls


def test_later_year_coverage_counts_as_covered_and_fetches_full_set():
    fetch, calls = _fetch({"202712"})
    covered, out = bw.probe_coverage("036010", YMMS, fetch)
    assert covered
    # 판정 순서: cF5001 을 연도순으로 보다가 커버가 나오면 나머지 연도·cF5002 전부 받는다 (총 6콜 = 종전과 같다)
    assert calls == [("cF5001", "202612"), ("cF5001", "202712"), ("cF5001", "202812"),
                     ("cF5002", "202612"), ("cF5002", "202712"), ("cF5002", "202812")]
    assert len(out) == 6


def test_none_is_declared_only_after_all_three_years():
    fetch, calls = _fetch(set())
    covered, out = bw.probe_coverage("000000", YMMS, fetch)
    assert not covered
    assert calls == [("cF5001", y) for y in YMMS] and len(out) == 3


def test_probe_fetch_failure_is_treated_as_covered():
    # 오판 비용이 비대칭(false-none = 영구 손실)이라 판정 불능은 covered 로 둔다 (is_covered 와 같은 원칙)
    fetch, calls = _fetch(set(), fail_years={"202612"})
    covered, _ = bw.probe_coverage("000000", YMMS, fetch)
    assert covered
    assert calls[0] == ("cF5001", "202612") and len(calls) == 6


def test_request_budget_per_stock():
    # ledger_health.wise.req_identity 의 상수와 맞물린다: 커버 15 · 무커버 4(목록 1 + cF5001 3)
    assert bw.REQ_COVERED == 15 and bw.REQ_NONE == 4


def test_fin_screens_request_main_basis_not_consolidated_only():
    """DQ-5(2026-09-26): cF3002/cF4002 를 연결(IFRSL) 고정으로 부르면 별도만 내는 회사(예 샘씨엔에스
    252990)의 값이 전부 NULL 로 온다. 주재무제표(MAIN)로 불러야 연결 있는 회사는 연결, 없는 회사는
    별도가 온다(09-26 실측: 252990 MAIN=별도 순이익 150.6억, 005930 MAIN=연결=IFRSL 과 동일)."""
    import inspect
    assert bw.FIN_GUBUN == "MAIN"
    src = inspect.getsource(bw)
    assert '"finGubun": "IFRSL"' not in src, "cF3002/cF4002 요청이 다시 연결 고정으로 돌아갔다"
    assert src.count('"finGubun": FIN_GUBUN') == 2
