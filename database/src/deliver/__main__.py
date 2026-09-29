"""CLI — 모델 엑셀 만들기와 발송.

  python -m deliver model-daily  --date YYYYMMDD --basis morning [--send] [--dry-run]
  python -m deliver model-weekly --week YYYY-Www                 [--send] [--dry-run]

공통: [--model-root data/model] [--fi-root data/factor_inputs] [--out-root data/deliver]
      [--env-file PATH] [--chat-key CHAT_ID_AIPLAYGROUND]
기본 루트는 `QL_HOME`(없으면 저장소 `database/`) 아래 `data/…` — factor_inputs CLI 와 같다.
엑셀은 늘 만든다. `--send` 면 텔레그램으로 보내고, `--dry-run` 이면 네트워크 없이 비밀 키 존재만
확인한다(`--send` 와 함께 줘도 보내지 않는다).

rc: 0 성공 · 1 발송 실패(dry-run 은 비밀 키 없음) · 2 입력·인자 오류(판 없음 등)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from .excel_daily import build_daily
from .excel_weekly import build_weekly, parse_week
from .reader import DeliverError
from .telegram import DEFAULT_CHAT_KEY, caption_daily, caption_weekly, send_document


def _parser() -> argparse.ArgumentParser:
    base = Path(os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])
    p = argparse.ArgumentParser(prog="python -m deliver")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--model-root", type=Path, default=base / "data" / "model")
        sp.add_argument("--fi-root", type=Path, default=base / "data" / "factor_inputs")
        sp.add_argument("--out-root", type=Path, default=base / "data" / "deliver")
        sp.add_argument("--config-dir", type=Path, default=None,
                        help="모델 레지스트리(기본 database/config/models)")
        sp.add_argument("--send", action="store_true", help="텔레그램으로 보낸다")
        sp.add_argument("--dry-run", action="store_true", help="보내지 않고 비밀 키 존재만 확인")
        sp.add_argument("--env-file", type=Path, default=None,
                        help="비밀 env(기본 QL_ENV → ~/kael-system-v3/.env)")
        sp.add_argument("--chat-key", default=DEFAULT_CHAT_KEY)

    d = sub.add_parser("model-daily", help="매일 엑셀")
    d.add_argument("--date", required=True, help="YYYYMMDD")
    d.add_argument("--basis", required=True, choices=("morning", "evening"))
    common(d)
    w = sub.add_parser("model-weekly", help="주간 엑셀(아침 확정판)")
    w.add_argument("--week", required=True, help="YYYY-Www (ISO 주)")
    common(w)
    return p


def _deliver(args: argparse.Namespace, path: Path, caption: str) -> int:
    print(f"caption:\n{caption}")
    if not (args.send or args.dry_run):
        return 0
    res = send_document(path, caption, env_file=args.env_file, chat_key=args.chat_key,
                        dry_run=args.dry_run)
    print(f"telegram: {'ok' if res['ok'] else 'FAIL'} — {res['description']}")
    return 0 if res["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parser().parse_args(argv)
    roots = dict(model_root=args.model_root, fi_root=args.fi_root, out_root=args.out_root,
                 config_dir=args.config_dir)
    try:
        if args.cmd == "model-daily":
            try:
                day = datetime.strptime(args.date, "%Y%m%d").date()
            except ValueError as e:
                raise DeliverError(f"--date 는 YYYYMMDD: {args.date!r}") from e
            r = build_daily(day, args.basis, **roots)
            print(f"daily: {r.path} · {r.spec_id} · 모집단 {r.n_rows} · 순위 {r.n_ranked} · "
                  f"시트 {', '.join(r.sheets)}")
            return _deliver(args, r.path, caption_daily(r.date, r.basis, r.spec_id, r.top,
                                                        r.n_ranked, r.n_rows))
        parse_week(args.week)
        wr = build_weekly(args.week, **roots)
        print(f"weekly: {wr.path} · 기준일 {wr.base_date} · 후보 {wr.n_candidates} · "
              f"거래일 판 {wr.n_days}/5 · 시트 {', '.join(wr.sheets)}")
        return _deliver(args, wr.path, caption_weekly(wr.week, wr.base_date, wr.spec_id, wr.top,
                                                      wr.n_candidates, wr.n_new, wr.n_out,
                                                      wr.n_days))
    except DeliverError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
