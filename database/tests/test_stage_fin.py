"""S1b `stg_rcept_dt_map` + S2 `stg_fin`. 손계산 DART 픽스처 (DESIGN v2.2 §4 DART·§6·§9)."""
import sqlite3
from pathlib import Path

import duckdb
import pytest

from stage import build, gates, manifest, rules, snapshot

DISC_COLS = ["row_hash", "corp_cls", "corp_code", "corp_name", "flr_nm", "rcept_dt", "rcept_no",
             "report_nm", "rm", "stock_code", "req_bgn_de", "req_end_de", "req_page_no", "dup_seq",
             "collected_at"]
FIN_COLS = ["row_hash", "account_detail", "account_id", "account_nm", "bfefrmtrm_amount",
            "bfefrmtrm_nm", "bsns_year", "corp_code", "currency", "frmtrm_amount", "frmtrm_nm",
            "ord", "rcept_no", "reprt_code", "sj_div", "sj_nm", "thstrm_add_amount",
            "thstrm_amount", "thstrm_nm", "req_bsns_year", "req_corp_code", "req_fs_div",
            "req_reprt_code",
            "collected_at", "frmtrm_add_amount", "frmtrm_q_amount", "frmtrm_q_nm", "dup_seq"]

R_SAMSUNG = "20250311001085"   # 삼성전자 FY2024 사업보고서
R_OTHER = "20160108000502"
R_NO_MAP = "20200101000001"    # disclosure 에 없는 접수번호 → 참조표 미스
R_FUTURE = "29230101000001"    # 연도 범위 밖 (G7 격리)


def _disc(rcept_no: str, rcept_dt: str, collected: str = "2026-08-30T10:00:00",
          page: str = "1") -> tuple[str, ...]:
    return (f"h{rcept_no}{page}", "Y", "00126380", "삼성전자", "삼성전자", rcept_dt, rcept_no,
            "사업보고서", "", "005930", "20250101", "20250331", page, "0", collected)


def _fin(rcept_no: str, corp: str = "00126380", year: str = "2024", reprt: str = "11011",
         fs: str = "CFS", sj: str = "IS", acct: str = "ifrs-full_Revenue", detail: str = "-",
         ord_: str = "23", amt: str = "300870903000000", cur: str = "KRW",
         resp_year: str | None = None, collected: str = "2026-08-30T13:42:37",
         nm: str = "매출액") -> tuple[str, ...]:
    return (f"h{rcept_no}{acct}{detail}{ord_}{collected}", detail, acct, nm, "", "제 54 기",
            resp_year or year, corp, cur, "258935494000000", "제 55 기", ord_, rcept_no, reprt,
            sj, "손익계산서", "", amt, "제 56 기", year, corp, fs, reprt, collected, "", "",
            "제 55 기 반기", "0")


DISC_ROWS = [
    _disc(R_SAMSUNG, "20250311"),
    _disc(R_SAMSUNG, "20250311", collected="2026-09-01T17:46:42", page="2"),  # 페이지 경계 중복
    _disc(R_OTHER, "20160108"),
]
FIN_ROWS = [
    _fin(R_SAMSUNG),                                                    # 정상, 18자리 금액
    _fin(R_SAMSUNG, sj="BS", acct="ifrs-full_Assets", ord_="1", amt="", nm="자산총계"),  # 빈값
    # 센티널 account_id · 경로 detail · 콤마 금액
    _fin(R_SAMSUNG, sj="SCE", acct="-표준계정코드 미사용-",
         detail="자본 [member]|비지배지분 [member]", ord_="5", amt="1,234", nm="기타"),
    # 응답 bsns_year 불일치 · 외화
    _fin(R_OTHER, corp="00364254", year="2015", resp_year="2014", cur="USD", amt="99"),
    _fin(R_NO_MAP, corp="00364254", year="2019", amt="7"),          # 참조표 미스
    _fin(R_SAMSUNG, collected="2026-09-01T00:00:00"),              # 동일 payload 재수집 → 접기
]
FIN_FUTURE = _fin(R_FUTURE, corp="00364254", year="2922", amt="1")


def _write_dart(path: Path, disc: list, fin: list) -> None:
    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE dart_disclosure ({', '.join(c + ' TEXT' for c in DISC_COLS)})")
    con.executemany(f"INSERT INTO dart_disclosure VALUES ({','.join('?' * len(DISC_COLS))})", disc)
    con.execute(f"CREATE TABLE dart_fin_raw ({', '.join(c + ' TEXT' for c in FIN_COLS)})")
    con.executemany(f"INSERT INTO dart_fin_raw VALUES ({','.join('?' * len(FIN_COLS))})", fin)
    con.commit()
    con.close()


@pytest.fixture
def snap(tmp_path: Path) -> snapshot.Snapshot:
    d = tmp_path / "raw"
    d.mkdir()
    _write_dart(d / "dart.db", DISC_ROWS, FIN_ROWS)
    return snapshot.make_snapshot({"dart": d / "dart.db"}, tmp_path / "snapshots",
                                  snapshot_id="snap_dart")


def _build(table: str, snap: snapshot.Snapshot, tmp_path: Path, **kw: object) -> build.BuildResult:
    return build.build_table(rules.RULES[table], snap, tmp_path / "stage", **kw)


def _read(tmp_path: Path, r: build.BuildResult) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    glob = str(tmp_path / "stage" / r.table / f"v={r.build_id}" / "**" / "*.parquet")
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{glob}', hive_partitioning=true)")
    return con


def _gate(r: build.BuildResult, name: str) -> gates.GateResult:
    return next(g for g in r.gates if g.name == name)


# ── rules ──────────────────────────────────────────────────────────────────────
def test_rules_fin_declares_eight_key_columns_receipt_axis_and_lookup() -> None:
    rule = rules.RULES["stg_fin"]
    assert rule.natural_key == ("corp_code", "bsns_year", "reprt_code", "fs_div", "sj_div",
                                "account_id", "account_detail", "ord")
    assert rule.partition_class == "receipt_axis"
    assert rule.partition_expr == "substr(rcept_no, 1, 4)"
    assert rule.write_mode == "append_only"
    assert rule.available.kind == "lookup"
    assert rule.available.table == "stg_rcept_dt_map"
    assert rule.column("thstrm_amount").decimal_type == "DECIMAL(38,4)"
    assert rule.column("account_detail").normalize_text is False   # 키 구성원 — 원문 보존
    assert rule.column("account_nm").normalize_text is True
    assert rule.payload_exclude == ("row_hash", "dup_seq", "collected_at")


def test_rules_rcept_dt_map_is_a_whole_reference_table() -> None:
    rule = rules.RULES["stg_rcept_dt_map"]
    assert rule.partition_class == "whole"
    assert rule.natural_key == ("rcept_no",)
    assert rule.available.kind == "none"
    assert rule.payload_columns == ("rcept_no", "rcept_dt")
    assert rule.key_unique is True


# ── S1b: stg_rcept_dt_map ──────────────────────────────────────────────────────
def test_map_folds_page_duplicates_into_one_row_per_rcept_no(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    r = _build("stg_rcept_dt_map", snap, tmp_path)
    assert r.ok, [g for g in r.gates if g.status is gates.GateStatus.FAIL]
    assert r.n_rows == 2 and r.n_dedup == 1
    con = _read(tmp_path, r)
    row = con.execute("SELECT rcept_dt, observed_n, observed_date, available_date, available_basis"
                      f" FROM t WHERE rcept_no = '{R_SAMSUNG}'").fetchone()
    assert row is not None
    assert str(row[0]) == "2025-03-11" and row[1] == 2
    assert str(row[2]) == "2026-08-30"          # 접힌 그룹의 min(observed_date)
    assert row[3] is None and row[4] is None    # 참조표 — available_date 비부여


def test_map_writes_single_partition_without_year_dirs(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    r = _build("stg_rcept_dt_map", snap, tmp_path)
    vdir = tmp_path / "stage" / "stg_rcept_dt_map" / f"v={r.build_id}"
    assert sorted(p.name for p in vdir.iterdir()) == ["_meta.json", "part0.parquet"]
    m = manifest.load(vdir.parent / "MANIFEST.json")
    assert m.builds[-1].partitions == [{"path": f"v={r.build_id}", "n_rows": 2}]
    assert _gate(r, "G6").status is gates.GateStatus.PASS   # append_only → 판본 보존 게이트 실행


def test_map_conflicting_rcept_dt_on_the_same_day_folds_to_one_row(tmp_path: Path) -> None:
    """같은 rcept_no 가 같은 관측일에 다른 rcept_dt 로 두 번 오면(페이지 경계·재스윕) 예전엔 G3 키
    유일성으로 폐기했다. 2026-09-11 계약: 같은 날 판본은 마지막 관측으로 접는다(n_dedup_same_day 1) —
    모순 자체는 원장에 남고, 접힌 건수가 G1 metrics 에 기록된다(TECH_DEBT B-19)."""
    d = tmp_path / "raw2"
    d.mkdir()
    _write_dart(d / "dart.db", DISC_ROWS + [_disc(R_SAMSUNG, "20250312", page="3")], FIN_ROWS)
    s = snapshot.make_snapshot({"dart": d / "dart.db"}, tmp_path / "snapshots", snapshot_id="s2")
    r = _build("stg_rcept_dt_map", s, tmp_path)
    assert r.status is build.BuildStatus.OK, [(g.name, g.detail) for g in r.gates]
    assert _gate(r, "G3").metrics["key_uniqueness_violations"] == 0
    assert _gate(r, "G1").metrics["n_dedup_same_day"] == 1


# ── S2: stg_fin ────────────────────────────────────────────────────────────────
def test_fin_requires_the_rcept_dt_map_to_be_built_first(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError, match="stg_rcept_dt_map"):
        _build("stg_fin", snap, tmp_path)


def test_fin_derives_available_date_from_map_and_marks_misses_unknown(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    _build("stg_rcept_dt_map", snap, tmp_path)
    r = _build("stg_fin", snap, tmp_path)
    assert r.ok, [g for g in r.gates if g.status is gates.GateStatus.FAIL]
    con = _read(tmp_path, r)
    hit = con.execute(f"SELECT available_date, available_basis FROM t WHERE rcept_no='{R_SAMSUNG}'"
                      " AND account_id='ifrs-full_Revenue'").fetchone()
    assert hit is not None and str(hit[0]) == "2025-03-11" and hit[1] == "derived"
    miss = con.execute(f"SELECT available_date, available_basis FROM t WHERE rcept_no='{R_NO_MAP}'"
                       ).fetchone()
    assert miss == (None, "unknown")
    assert _gate(r, "G0").metrics["rcept_map_miss"] == 1


def test_fin_casts_amounts_to_decimal_38_4_and_records_missing_kinds(
    snap: snapshot.Snapshot, tmp_path: Path
) -> None:
    _build("stg_rcept_dt_map", snap, tmp_path)
    r = _build("stg_fin", snap, tmp_path)
    con = _read(tmp_path, r)
    rev = con.execute("SELECT thstrm_amount, frmtrm_amount FROM t WHERE"
                      f" account_id='ifrs-full_Revenue' AND rcept_no='{R_SAMSUNG}'").fetchone()
    assert rev is not None and str(rev[0]) == "300870903000000.0000"
    assert str(rev[1]) == "258935494000000.0000"
    blank = con.execute("SELECT thstrm_amount, miss_kind.thstrm_amount, _src_flag FROM t "
                        "WHERE account_id='ifrs-full_Assets'").fetchone()
    assert blank == (None, "ledger_blank", "ok")          # 원장 정상 결측 — 캐스팅 실패 아님
    comma = con.execute("SELECT thstrm_amount FROM t WHERE account_id='-표준계정코드 미사용-'"
                        ).fetchone()
    assert comma is not None and str(comma[0]) == "1234.0000"


def test_fin_categorize_flags_and_detail_path(snap: snapshot.Snapshot, tmp_path: Path) -> None:
    _build("stg_rcept_dt_map", snap, tmp_path)
    r = _build("stg_fin", snap, tmp_path)
    con = _read(tmp_path, r)
    sent = con.execute("SELECT account_std, account_detail, account_detail_path FROM t "
                       "WHERE account_id='-표준계정코드 미사용-'").fetchone()
    assert sent == (False, "자본 [member]|비지배지분 [member]",
                    ["자본 [member]", "비지배지분 [member]"])
    plain = con.execute("SELECT account_std, account_detail_path FROM t "
                        "WHERE account_id='ifrs-full_Revenue' LIMIT 1").fetchone()
    assert plain == (True, None)
    other = con.execute("SELECT bsns_year, bsns_year_resp, bsns_year_mismatch, currency, is_krw,"
                        f" year FROM t WHERE rcept_no='{R_OTHER}'").fetchone()
    assert other == ("2015", "2014", True, "USD", False, 2016)   # 키는 요청축, 파티션은 rcept 연도


def test_fin_dedups_identical_payload_recollection(snap: snapshot.Snapshot, tmp_path: Path) -> None:
    _build("stg_rcept_dt_map", snap, tmp_path)
    r = _build("stg_fin", snap, tmp_path)
    assert r.n_src == 6 and r.n_dedup == 1 and r.n_rows == 5
    con = _read(tmp_path, r)
    n = con.execute("SELECT observed_n, observed_date FROM t WHERE account_id='ifrs-full_Revenue'"
                    f" AND rcept_no='{R_SAMSUNG}'").fetchone()
    assert n is not None and n[0] == 2 and str(n[1]) == "2026-08-30"
    assert _gate(r, "G6").status is gates.GateStatus.PASS


def test_fin_rejects_out_of_range_receipt_year(tmp_path: Path) -> None:
    d = tmp_path / "raw3"
    d.mkdir()
    _write_dart(d / "dart.db", DISC_ROWS, FIN_ROWS + [FIN_FUTURE])
    s = snapshot.make_snapshot({"dart": d / "dart.db"}, tmp_path / "snapshots", snapshot_id="s3")
    _build("stg_rcept_dt_map", s, tmp_path)
    r = _build("stg_fin", s, tmp_path, gate_thresholds={"G7": 0.5})
    assert r.ok
    assert _gate(r, "G7").metrics["n_out_of_range"] == 1
    assert r.n_reject == 1 and r.n_rows == 5


def test_fin_same_key_differing_payload_on_the_same_observed_date_keeps_the_last_observation(
    tmp_path: Path
) -> None:
    """같은 8키, 다른 값, 같은 관측일 — 예전엔 G6 폐기(2026-09-11 첫 저녁 슬롯에서 stg_fin 86건으로 실제
    발생: 06:47 재스윕 + 18:05 저녁 스윕). 이제는 그날의 마지막 관측(23:00 KST, amt=1)이 판이다."""
    d = tmp_path / "raw4"
    d.mkdir()
    dup = _fin(R_SAMSUNG, amt="1", collected="2026-08-30T14:00:00")   # 23:00 KST 같은 날, 더 늦은 관측
    _write_dart(d / "dart.db", DISC_ROWS, FIN_ROWS + [dup])
    s = snapshot.make_snapshot({"dart": d / "dart.db"}, tmp_path / "snapshots", snapshot_id="s4")
    _build("stg_rcept_dt_map", s, tmp_path)
    r = _build("stg_fin", s, tmp_path)
    assert r.status is build.BuildStatus.OK, [(g.name, g.detail) for g in r.gates]
    assert _gate(r, "G6").metrics["n_dup"] == 0
    assert _gate(r, "G1").metrics["n_dedup_same_day"] == 1
