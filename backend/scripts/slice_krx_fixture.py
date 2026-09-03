"""KRX 원장 parquet(`quant-data` 빌드)에서 테스트용 소형 슬라이스를 잘라낸다.

원장 전체(674MB)는 레포에 넣을 수 없으므로 종목 몇 개의 전 기간 행만 추려
`tests/fixtures/krx_parquet/`에 같은 스키마·같은 파일명으로 저장한다.
어댑터는 이 디렉토리를 실제 빌드 디렉토리와 구분하지 않는다.

사용법:
    uv run python scripts/slice_krx_fixture.py <원장 디렉토리> [--out tests/fixtures/krx_parquet]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq

# 선정 이유를 함께 남긴다 — 왜 이 종목인지 모르면 픽스처 갱신 시 의미가 사라진다.
DEFAULT_TICKERS: dict[str, str] = {
    "005930": "삼성전자 — KOSPI 대형주, 2018-05 액면분할(50:1) 정지 구간 포함",
    "000660": "SK하이닉스 — KOSPI 대형주, 정상 케이스",
    "008080": "에스와이코퍼레이션 — 2013 감자·정지·+6,699,900% 등락률 이상치, 2013-09 상장폐지",
    "247540": "에코프로비엠 — KOSDAQ, 2019 상장 (기간 중간 시작 케이스)",
    "066970": "엘앤에프 — KOSDAQ, 정상 케이스",
}
TRADE_FILES = ("krx_stk_bydd_trd.parquet", "krx_ksq_bydd_trd.parquet")
# 종목마스터(일별 스냅샷)는 6자리 단축코드 컬럼이 isu_srt_cd다 (isu_cd는 ISIN).
MASTER_FILES = ("krx_stk_isu_base_info.parquet", "krx_ksq_isu_base_info.parquet")


def slice_file(
    source: Path,
    target: Path,
    tickers: list[str],
    symbol_column: str = "isu_cd",
    sort_by: str = "bas_dd",
) -> int:
    sliced = pq.read_table(source, filters=[(symbol_column, "in", tickers)]).sort_by(sort_by)
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(sliced, target, compression="zstd")
    return sliced.num_rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--out", type=Path, default=Path("tests/fixtures/krx_parquet"))
    parser.add_argument("--tickers", nargs="*", default=list(DEFAULT_TICKERS))
    args = parser.parse_args(argv)

    source_dir: Path = args.source_dir
    out_dir: Path = args.out
    tickers: list[str] = args.tickers
    if not source_dir.is_dir():
        print(f"source directory not found — path={source_dir}", file=sys.stderr)
        return 1

    manifest: dict[str, object] = {
        "sliced_at": datetime.now().isoformat(timespec="seconds"),
        "source_dir": source_dir.name,
        "tickers": {t: DEFAULT_TICKERS.get(t, "") for t in tickers},
        "files": {},
    }
    source_manifest = source_dir / "manifest.json"
    if source_manifest.exists():
        manifest["source_manifest"] = json.loads(source_manifest.read_text(encoding="utf-8"))

    files: dict[str, int] = {}
    for name in TRADE_FILES:
        source = source_dir / name
        if not source.exists():
            print(f"skip: file not found — path={source}", file=sys.stderr)
            continue
        rows = slice_file(source, out_dir / name, tickers)
        files[name] = rows
        print(f"{name}: {rows} rows -> {out_dir / name}")
    for name in MASTER_FILES:
        source = source_dir / name
        if not source.exists():
            print(f"skip: file not found — path={source}", file=sys.stderr)
            continue
        rows = slice_file(
            source, out_dir / name, tickers, symbol_column="isu_srt_cd", sort_by="bas_dd_req"
        )
        files[name] = rows
        print(f"{name}: {rows} rows -> {out_dir / name}")
    manifest["files"] = files
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
