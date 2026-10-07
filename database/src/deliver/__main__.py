"""CLI — 모델 엑셀 만들기와 발송.

  python -m deliver model-daily  --date YYYYMMDD --basis morning [--send [--resend]] [--dry-run]
  python -m deliver model-weekly --week YYYY-Www                 [--send] [--dry-run]

공통: [--model-root data/model] [--fi-root data/factor_inputs] [--out-root data/deliver]
      [--env-file PATH] [--chat-key CHAT_ID_AIPLAYGROUND]
기본 루트는 `QL_HOME`(없으면 저장소 `database/`) 아래 `data/…` — factor_inputs CLI 와 같다.
엑셀은 늘 만든다(아래 '이미 보냄' 건너뜀만 예외). `--send` 면 텔레그램으로 보내고, `--dry-run` 이면
네트워크 없이 비밀 키 존재만 확인한다(`--send` 와 함께 줘도 보내지 않는다).

발송 장부(N-25 Q9, 로드맵 K0-1 '거래일마다 정확히 1건'): model-daily 를 실제로 보내면
**성공한 뒤에만** `<out-root>/sent_model_daily.jsonl` 에 한 줄
(date·basis·build_id·sha256·sent_utc·correction)을 덧붙인다. 같은 D·basis 가 장부에 있으면
`--send` 는 엑셀도 다시 만들지 않고 건너뛴다(rc 0, 표준출력 한 줄 — 보낸 파일이 장부 sha256 과
같게 남는다). 같은 날 다시 보내려면 `--resend` 를 명시하고, 그때 캡션에 '정정 n · 판 id · 생성
시각' 이 붙는다. 장부 줄을 읽지 못하면 보냈는지 모르므로 보내지 않는다(rc 2).

rc: 0 성공(이미 보내 건너뜀 포함) · 1 발송 실패(dry-run 은 비밀 키 없음) · 2 입력·인자 오류(판 없음·
    발송 장부 손상·읽기 실패 등) · 3 그 밖 예상 밖 예외(엑셀 생성 실패 등 — E-13, 1 '발송 실패'와
    섞지 않는다. 발송 뒤 장부 기록 실패면 메시지에 '발송은 됐다')
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from .excel_daily import build_daily
from .excel_weekly import build_weekly, parse_week
from .reader import DeliverError
from .telegram import DEFAULT_CHAT_KEY, caption_daily, caption_weekly, send_document

# 발송 장부 — out-root(기본 data/deliver) 바로 아래. out-root 를 따르므로 임시 루트 리허설
# (`--out-root $(mktemp -d)`)이 운영 장부를 읽거나 쓰지 않는다. history/(확정판 인계 이력 —
# GC·가드가 *.json 을 읽는다)·ledger_evening.json(저녁 수집 상태)과는 다른 파일이다.
LEDGER_NAME = "sent_model_daily.jsonl"


def _sent(ledger: Path, day: str, basis: str) -> list[dict[str, object]]:
    """장부에서 그 D(YYYY-MM-DD)·basis 의 발송 기록(보낸 순서). 장부가 없으면 빈 목록."""
    if not ledger.is_file():
        return []
    # 보냈는지 알 수 없으면 보내지 않는다(P1) — 중복 발송보다 미발송 경보 쪽. 읽기 실패는 rc 2
    unknown = f"D={day} basis={basis} 를 보냈는지 알 수 없어 보내지 않는다"
    try:
        text = ledger.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise DeliverError(f"발송 장부 {ledger} 를 UTF-8 텍스트로 읽지 못했다 — {unknown}: "
                           f"{type(e).__name__}: {e}") from e
    out: list[dict[str, object]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError as e:
            raise DeliverError(f"발송 장부 {ledger}:{i} 를 읽지 못했다 — {unknown}: {e}") from e
        if not isinstance(entry, dict):
            raise DeliverError(f"발송 장부 {ledger}:{i} 가 JSON 객체가 아니다"
                               f"({type(entry).__name__}) — {unknown}")
        if entry.get("date") == day and entry.get("basis") == basis:
            out.append(entry)
    return out


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
    d.add_argument("--resend", action="store_true",
                   help="이미 보낸 D·basis 를 정정으로 다시 보낸다(--send 와 함께, "
                        "캡션에 '정정 n')")
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
    parser = _parser()
    args = parser.parse_args(argv)
    if args.cmd == "model-daily" and args.resend and not args.send:
        parser.error("--resend 는 --send 와 함께 준다(다시 보내는 옵션이다)")
    roots = dict(model_root=args.model_root, fi_root=args.fi_root, out_root=args.out_root,
                 config_dir=args.config_dir)
    sent = False                        # 발송이 성공했는가 — 그 뒤 예외(장부 append 실패)를 가른다
    try:
        if args.cmd == "model-daily":
            try:
                day = datetime.strptime(args.date, "%Y%m%d").date()
            except ValueError as e:
                raise DeliverError(f"--date 는 YYYYMMDD: {args.date!r}") from e
            sending = args.send and not args.dry_run
            ledger = Path(args.out_root) / LEDGER_NAME
            prior = _sent(ledger, day.isoformat(), args.basis) if sending else []
            if prior and not args.resend:
                last = prior[-1]
                print(f"발송 건너뜀 — D={day} basis={args.basis} 는 이미 보냈다(정정 "
                      f"{last.get('correction')} · 판 {last.get('build_id')} · "
                      f"{last.get('sent_utc')}, 장부 {ledger} {len(prior)}건). 엑셀도 다시 만들지 "
                      f"않았다 — 다시 보내려면 --resend")
                return 0
            r = build_daily(day, args.basis, **roots)
            print(f"daily: {r.path} · {r.spec_id} · 모집단 {r.n_rows} · 순위 {r.n_ranked} · "
                  f"시트 {', '.join(r.sheets)}")
            sha256 = hashlib.sha256(r.path.read_bytes()).hexdigest()
            rc = _deliver(args, r.path, caption_daily(
                r.date, r.basis, r.spec_id, r.top, r.n_ranked, r.n_rows,
                correction=len(prior), build_id=r.build_id, generated_at=r.generated_at))
            if rc == 0 and sending:
                sent = True
                entry = {"date": r.date, "basis": r.basis, "build_id": r.build_id,
                         "sha256": sha256,
                         "sent_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "correction": len(prior)}
                ledger.parent.mkdir(parents=True, exist_ok=True)
                # 손 편집으로 끝 줄바꿈이 빠졌으면 먼저 보충한다 — 줄이 붙으면 다음 실행부터
                # 장부 손상 rc 2 로 멈춘다
                old = ledger.read_bytes() if ledger.is_file() else b""
                lead = "\n" if old and not old.endswith(b"\n") else ""
                with ledger.open("a", encoding="utf-8") as f:
                    f.write(lead + json.dumps(entry, ensure_ascii=False) + "\n")
                print(f"발송 장부 {ledger} — {json.dumps(entry, ensure_ascii=False)}")
            return rc
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
    except Exception as e:  # noqa: BLE001  # reason: E-13 — 예상 밖 예외를 rc 1(발송 실패)과 가른다
        target = (f"--date {args.date} --basis {args.basis}" if args.cmd == "model-daily"
                  else f"--week {args.week}")
        # 발송 뒤 예외(장부 append 실패, B-58)면 장부에 없어 다음 자동 실행이 다시 보낼 수 있다
        after = (" · 발송은 됐다 — 장부 기록 실패(장부에 없어 다음 실행이 다시 보낼 수 있다)"
                 if sent else "")
        traceback.print_exc()
        print(f"예상 밖 예외 rc 3 — {args.cmd} {target}{after}: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
