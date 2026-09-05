"""빌드 후 문서 검사 — 테이블을 가로지르는 기록형 게이트 D6·D8·D12 (DOC_DESIGN v1.1 §4).

stage 게이트는 테이블 하나만 보므로 여기서 parquet 를 읽어 보고서를 만든다. 세 항목 모두 기록형이다:
정정 문서 접두([기재정정])는 report_nm 에만 있어 여기서 판정할 수 없고, 폐기형 판정(E-G6a)은
equity 가 stg_disclosure 를 붙여 한다(§8.1). 그래서 status 는 질의가 돌아가면 항상 ok.
CLI: PYTHONPATH=src python -m stage.doc_checks [--stage-root data/stage] [--out report.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb

from . import manifest


@dataclass(frozen=True)
class D6Report:
    n_main_parsed: int              # main 멤버 · parse_mode ok|lenient
    n_with_page: int                # 그중 CORRECTION 첫 장 있음
    n_corr_rows: int                # stg_doc_correction 행 수 (접수번호당 1)
    n_filed_parsed: int             # filed_date 해석 성공


@dataclass(frozen=True)
class D8YearReport:
    n_main_annual: int              # main 멤버 · 사업보고서(11011)
    n_xbrl_ge4: int                 # 그중 XBRL 그룹 4개 이상 (BS·IS·CF·SA 기대)


@dataclass(frozen=True)
class D12Report:
    n_members: int                  # parse_log 행(멤버) · ok|lenient
    unknown_tag_members: int
    lenient_members: int


@dataclass(frozen=True)
class DocCheckReport:
    d6: D6Report
    d8_by_year: dict[str, D8YearReport]
    d12: D12Report
    status: str                     # ok

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _glob(stage_root: Path, table: str) -> str:
    m = manifest.load(stage_root / table / "MANIFEST.json")
    if m.current_build is None:
        raise FileNotFoundError(f"no committed build for {table} under {stage_root}")
    return str(stage_root / table / f"v={m.current_build}" / "**" / "*.parquet")


def run(stage_root: Path) -> DocCheckReport:
    con = duckdb.connect()
    for t in ("stg_doc_meta", "stg_doc_correction", "stg_doc_parse_log"):
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{_glob(stage_root, t)}', "
                    f"hive_partitioning=true)")
    d6 = con.execute("""
        SELECT count(*) AS n_main_parsed,
               count(*) FILTER (WHERE m.has_correction_page) AS n_with_page,
               count(*) FILTER (WHERE c.rcept_no IS NOT NULL) AS n_corr_rows,
               count(*) FILTER (WHERE c.filed_date_status = 'parsed') AS n_filed_parsed
        FROM stg_doc_meta m LEFT JOIN stg_doc_correction c ON c.rcept_no = m.rcept_no
        WHERE m.member_role = 'main' AND m.parse_mode IN ('ok', 'lenient')""").fetchone()
    d8_rows = con.execute("""
        SELECT year, count(*) AS n_main_annual,
               count(*) FILTER (WHERE n_xbrl_groups >= 4) AS n_xbrl_ge4
        FROM stg_doc_meta WHERE member_role = 'main' AND doc_acode = '11011'
        GROUP BY 1 ORDER BY 1""").fetchall()
    d12 = con.execute("""
        SELECT count(*) AS n_members,
               count(*) FILTER (WHERE unknown_tags <> '[]') AS unknown_tag_members,
               count(*) FILTER (WHERE parse_mode = 'lenient') AS lenient_members
        FROM stg_doc_parse_log WHERE parse_mode IN ('ok', 'lenient')""").fetchone()
    con.close()
    if d6 is None or d12 is None:
        raise RuntimeError(f"doc_checks query returned no row: stage_root={stage_root}")
    return DocCheckReport(
        d6=D6Report(int(d6[0]), int(d6[1]), int(d6[2]), int(d6[3])),
        d8_by_year={str(y): D8YearReport(int(n), int(x)) for y, n, x in d8_rows},
        d12=D12Report(int(d12[0]), int(d12[1]), int(d12[2])),
        status="ok",
    )


def main(argv: list[str] | None = None) -> int:
    base = Path(os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage-root", type=Path, default=base / "data" / "stage")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    rep = run(a.stage_root)
    text = json.dumps(rep.as_dict(), ensure_ascii=False, indent=1)
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if rep.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
