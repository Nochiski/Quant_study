"""CLI — `PYTHONPATH=src python -m ledger_sync <verb>` (래퍼 `database/scripts/ledger_sync.*`).

  plan      원격 MANIFEST 와 로컬 state 대조 — 무엇을 받을지만 보여 준다(로컬 변경 없음)
  pull      계획 실행: 새 빌드 수신·재사용·원자 교체·로컬 GC. 수신 중 서버 판본이 바뀌면 3
  verify    manifest · files · hash 세 층위 대조. 하나라도 어긋나면 4 (`--offline` 은 hash 만)
  gc        로컬 구판본 정리(current 보호, `--keep`)
  catalog   `python -m equity catalog` 위임 — equity.duckdb 매크로를 로컬 절대경로로 재생성
  sync      pull → catalog → verify(manifest·files) 를 한 번에. 일일 작업이 부르는 동사.
            결과는 `_sync/last_run.json`, 로그는 `_sync/logs/`
  status    마지막 실행 결과 + 테이블별 로컬 빌드. `--remote` 면 서버 current_build 와 대조해 뒤처진
            테이블을 보여 준다

종료 코드: 0 성공 · 2 전송 실패·설정 오류 · 3 수신 중 서버 판본 변경(다시 pull) · 4 검증 불일치.

접속 정보는 인자 또는 환경변수(`QL_SYNC_HOST`·`QL_SYNC_USER`·`QL_SYNC_KEY`·`QL_SYNC_ROOT`).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from .hashing import trusting_reuse_check
from .layout import SYNC_DIR
from .plan import SyncPlan, TableAction, list_remote_tables, make_plan, read_remote_manifest
from .pull import (
    KEEP_DEFAULT,
    InsufficientDiskSpace,
    Log,
    PullReport,
    TableOutcome,
    execute_plan,
    gc_table,
)
from .remote import RemoteConnectError, RemoteFS, RemoteTransferError, SftpEndpoint, open_sftp
from .state import SyncState, load_state
from .verify import (
    Level,
    VerifyReport,
    require_coverage,
    verify_files,
    verify_hash,
    verify_manifest,
)

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_DRIFTED = 3
EXIT_MISMATCH = 4

DEFAULT_HOST = "210.217.23.47"
DEFAULT_USER = "quantshare"
DEFAULT_KEY = "~/.ssh/kael_quant"
DEFAULT_ROOT = "~/quant-ledger/data"
LAYERS = ("equity", "stage")
LAST_RUN_NAME = "last_run.json"
# 서버 빌드 체인이 도는 창(UTC). 이 안에서 시작하면 drifted 가 나기 쉽다 — 경고만 한다.
BUILD_WINDOWS_UTC: tuple[tuple[tuple[int, int], tuple[int, int]], ...] = (
    ((13, 15), (13, 50)),
    ((0, 0), (0, 35)),
)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def _endpoint(args: argparse.Namespace) -> SftpEndpoint:
    return SftpEndpoint(host=args.host, user=args.user, key_path=Path(args.key).expanduser(),
                        port=args.port)


def _layer_root(args: argparse.Namespace) -> Path:
    return Path(args.root).expanduser() / args.layer


def _remote_root(args: argparse.Namespace) -> str:
    base = args.remote_root.rstrip("/")
    return f"{base}/{args.layer}"


def _in_build_window(now: datetime) -> bool:
    minutes = now.hour * 60 + now.minute
    return any(start[0] * 60 + start[1] <= minutes <= end[0] * 60 + end[1]
               for start, end in BUILD_WINDOWS_UTC)


def _fmt_mb(n: int) -> str:
    return f"{n / 1e6:,.1f}MB"


def _print_plan(plan: SyncPlan) -> None:
    for t in plan.tables:
        remote_build = t.remote_build.build_id if t.remote_build else "-"
        print(f"  {t.table:22s} {t.action.value:10s} remote={remote_build} "
              f"local={t.local_build or '-'}"
              f" down={_fmt_mb(t.download_bytes)} reuse={_fmt_mb(t.reused_bytes)}"
              + (f"  {t.detail}" if t.detail else "")
              + ("  manifest_refresh" if t.manifest_refresh else ""))
    for w in plan.warnings:
        print(f"  warning: {w}")
    print(f"== {plan.layer}: new_build={len(plan.by_action(TableAction.NEW_BUILD))} "
          f"up_to_date={len(plan.by_action(TableAction.UP_TO_DATE))} "
          f"error={len(plan.by_action(TableAction.ERROR))} "
          f"download={_fmt_mb(plan.download_bytes)} reuse={_fmt_mb(plan.reused_bytes)}")


def _plan_json(plan: SyncPlan) -> dict[str, object]:
    return {
        "layer": plan.layer,
        "download_bytes": plan.download_bytes,
        "reused_bytes": plan.reused_bytes,
        "warnings": list(plan.warnings),
        "tables": [
            {
                "table": t.table, "action": t.action.value,
                "remote_build": t.remote_build.build_id if t.remote_build else None,
                "local_build": t.local_build, "download_bytes": t.download_bytes,
                "reused_bytes": t.reused_bytes, "manifest_refresh": t.manifest_refresh,
                "detail": t.detail,
            }
            for t in plan.tables
        ],
    }


def _report_json(report: PullReport) -> dict[str, object]:
    return {
        "layer": report.layer,
        "ok": report.ok,
        "elapsed_s": report.elapsed_s,
        "bytes_downloaded": report.bytes_downloaded,
        "bytes_reused": report.bytes_reused,
        "drifted": report.drifted,
        "meta_files": report.meta_files,
        "gc_removed": report.gc_removed,
        "results": [
            {"table": r.table, "outcome": r.outcome.value, "build_id": r.build_id,
             "bytes_downloaded": r.bytes_downloaded, "bytes_reused": r.bytes_reused,
             "files_downloaded": r.files_downloaded, "detail": r.detail}
            for r in report.results
        ],
    }


def _verify_json(report: VerifyReport) -> dict[str, object]:
    return {"ok": report.ok, "checked": report.checked, "skipped": report.skipped,
            "findings": [asdict(f) | {"level": f.level.value} for f in report.findings]}


def _log_writer(layer_root: Path, verb: str) -> tuple[Log, TextIO, Path]:
    """콘솔과 `_sync/logs/<verb>_<UTC>.log` 에 같이 쓰는 log 함수, 파일 핸들, 로그 경로."""
    log_dir = layer_root / SYNC_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = log_dir / f"{verb}_{stamp}.log"
    handle = path.open("a", encoding="utf-8")

    def log(line: str) -> None:
        print(line)
        handle.write(line + "\n")
        handle.flush()

    return log, handle, path


def _pull_exit(report: PullReport) -> int:
    if report.failed:
        return EXIT_ERROR
    if report.drifted:
        return EXIT_DRIFTED
    return EXIT_OK


def _run_pull(remote: RemoteFS, args: argparse.Namespace, layer_root: Path, state: SyncState,
              log: Log) -> tuple[PullReport, SyncPlan]:
    now = datetime.now(UTC)
    if _in_build_window(now):
        log(f"warning: inside server build window (utc={now:%H:%M}) — expect drifted tables")
    plan = make_plan(remote, _remote_root(args), layer_root, state, layer=args.layer,
                     tables=args.tables, reuse=not args.no_reuse,
                     reuse_check=trusting_reuse_check if args.no_reuse_check else None)
    if not args.json:
        _print_plan(plan)
    report = execute_plan(remote, plan, layer_root, state, keep=args.keep,
                          check_space=not args.no_space_check, log=log)
    return report, plan


def cmd_plan(args: argparse.Namespace) -> int:
    layer_root = _layer_root(args)
    state = load_state(layer_root, args.layer)
    with open_sftp(_endpoint(args), accept_new_host_key=args.accept_new) as remote:
        plan = make_plan(remote, _remote_root(args), layer_root, state, layer=args.layer,
                         tables=args.tables, reuse=not args.no_reuse,
                         reuse_check=trusting_reuse_check if args.no_reuse_check else None)
    if args.json:
        print(json.dumps(_plan_json(plan), ensure_ascii=False, indent=1))
    else:
        _print_plan(plan)
    return EXIT_ERROR if plan.by_action(TableAction.ERROR) else EXIT_OK


def cmd_pull(args: argparse.Namespace) -> int:
    layer_root = _layer_root(args)
    state = load_state(layer_root, args.layer)
    log, handle, _ = _log_writer(layer_root, "pull")
    try:
        with open_sftp(_endpoint(args), accept_new_host_key=args.accept_new) as remote:
            report, _ = _run_pull(remote, args, layer_root, state, log)
    finally:
        handle.close()
    if args.json:
        print(json.dumps(_report_json(report), ensure_ascii=False, indent=1))
    else:
        _print_pull_summary(report)
    return _pull_exit(report)


def _print_pull_summary(report: PullReport) -> None:
    done = sum(1 for r in report.results if r.outcome is TableOutcome.DONE)
    print(f"== pull {report.layer}: done={done} unchanged="
          f"{sum(1 for r in report.results if r.outcome is TableOutcome.UNCHANGED)} "
          f"failed={len(report.failed)} downloaded={_fmt_mb(report.bytes_downloaded)} "
          f"reused={_fmt_mb(report.bytes_reused)} elapsed={report.elapsed_s}s")
    if report.gc_removed:
        print(f"   gc removed {len(report.gc_removed)}: {report.gc_removed[:6]}"
              + (" …" if len(report.gc_removed) > 6 else ""))
    if report.drifted:
        print(f"   drifted (server committed a new build while pulling — run pull again): "
              f"{report.drifted}")
    for r in report.failed:
        print(f"   {r.outcome.value} {r.table}: {r.detail}")


def _levels(args: argparse.Namespace) -> list[Level]:
    if args.offline:
        return [Level.HASH]
    if not args.level:
        return [Level.MANIFEST, Level.FILES, Level.HASH]
    return [Level(name) for name in args.level]


def _reject_conflicting_verify_options(parser: argparse.ArgumentParser,
                                       args: argparse.Namespace) -> None:
    """`--offline` 은 원격이 필요한 층위를 조용히 빼면 안 된다 — `--level` 과 같이 오면 거부한다."""
    if args.verb == "verify" and args.offline and args.level \
            and set(args.level) != {Level.HASH.value}:
        parser.error("--offline runs only the hash level; drop --level or drop --offline")


def _run_verify(remote: RemoteFS | None, args: argparse.Namespace, layer_root: Path,
                state: SyncState, levels: Iterable[Level]) -> VerifyReport:
    report = VerifyReport()
    if not require_coverage(layer_root, state, report, tables=args.tables):
        return report
    for level in levels:
        if level is Level.HASH:
            verify_hash(layer_root, state, report, tables=args.tables)
        elif remote is None:
            raise RemoteConnectError(f"level {level.value} needs the remote — drop --offline")
        elif level is Level.MANIFEST:
            verify_manifest(remote, _remote_root(args), layer_root, state, report,
                            tables=args.tables)
        elif level is Level.FILES:
            verify_files(remote, _remote_root(args), layer_root, state, report,
                         tables=args.tables)
    return report


def _print_verify(report: VerifyReport) -> None:
    for f in report.findings:
        print(f"  {f.level.value:8s} {f.table:22s} {f.detail}")
    print(f"== verify: {'ok' if report.ok else 'MISMATCH'} checked={report.checked} "
          f"skipped={report.skipped} findings={len(report.findings)}")


def cmd_verify(args: argparse.Namespace) -> int:
    layer_root = _layer_root(args)
    state = load_state(layer_root, args.layer)
    levels = _levels(args)
    if Level.MANIFEST in levels or Level.FILES in levels:
        with open_sftp(_endpoint(args), accept_new_host_key=args.accept_new) as remote:
            report = _run_verify(remote, args, layer_root, state, levels)
    else:
        report = _run_verify(None, args, layer_root, state, levels)
    if args.json:
        print(json.dumps(_verify_json(report), ensure_ascii=False, indent=1))
    else:
        _print_verify(report)
    return EXIT_OK if report.ok else EXIT_MISMATCH


def cmd_gc(args: argparse.Namespace) -> int:
    layer_root = _layer_root(args)
    state = load_state(layer_root, args.layer)
    removed: list[str] = []
    for table in sorted(state.tables):
        removed.extend(gc_table(layer_root, table, state, args.keep))
    print(f"== gc {args.layer}: removed={len(removed)} keep={args.keep}")
    for item in removed:
        print(f"  {item}")
    return EXIT_OK


CATALOG_TIMEOUT_S = 3600  # 실측 8분(2026-09-19); 게이트가 걸리면 더 걸릴 수 있어 넉넉히


def catalog_command(equity_root: Path) -> list[str]:
    return [sys.executable, "-m", "equity", "--root", str(equity_root),
            "--stage-root", str(equity_root), "catalog"]


def run_catalog(equity_root: Path, log: Log) -> int:
    """`python -m equity catalog` 를 같은 인터프리터·`database/src` PYTHONPATH 로 돈다."""
    src_dir = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(src_dir), env.get("PYTHONPATH", "")) if p)
    command = catalog_command(equity_root)
    log(f"== catalog: {' '.join(command)}")
    try:
        completed = subprocess.run(command, env=env, check=False, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace",
                                   timeout=CATALOG_TIMEOUT_S)
    except subprocess.TimeoutExpired as error:
        log(f"   catalog timed out after {CATALOG_TIMEOUT_S}s — {error!r}")
        return EXIT_ERROR
    for stream in (completed.stdout, completed.stderr):
        for line in stream.splitlines():
            log(f"   {line}")
    if completed.returncode != 0:
        log(f"   catalog failed rc={completed.returncode}")
        return EXIT_ERROR
    return EXIT_OK


def cmd_catalog(args: argparse.Namespace) -> int:
    if args.layer != "equity":
        print(f"catalog is an equity-layer step — layer={args.layer}", file=sys.stderr)
        return EXIT_ERROR
    return run_catalog(_layer_root(args), print)


LOG_KEEP = 60


def _rotate_logs(layer_root: Path) -> None:
    """`_sync/logs/` 가 무한히 쌓이지 않게 최근 LOG_KEEP 개만 남긴다."""
    log_dir = layer_root / SYNC_DIR / "logs"
    if not log_dir.is_dir():
        return
    logs = sorted(log_dir.glob("*.log"))
    for stale in logs[: max(0, len(logs) - LOG_KEEP)]:
        try:
            stale.unlink()
        except OSError:
            pass  # 열려 있는 로그는 다음 실행이 정리한다


def _write_last_run(layer_root: Path, payload: dict[str, object]) -> Path:
    path = layer_root / SYNC_DIR / LAST_RUN_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path


def cmd_sync(args: argparse.Namespace) -> int:
    layer_root = _layer_root(args)
    state = load_state(layer_root, args.layer)
    log, handle, log_path = _log_writer(layer_root, "sync")
    started = datetime.now(UTC)
    # 예상 밖 예외로 죽어도 last_run.json 에 "성공" 이 남지 않도록 실패로 시작해 성공 경로에서만
    # 내린다
    exit_code = EXIT_ERROR
    payload: dict[str, object] = {"started_at_utc": started.isoformat(timespec="seconds"),
                                  "layer": args.layer, "log": str(log_path)}
    try:
        with open_sftp(_endpoint(args), accept_new_host_key=args.accept_new) as remote:
            report, _ = _run_pull(remote, args, layer_root, state, log)
            _print_pull_summary(report)
            payload["pull"] = _report_json(report)
            exit_code = _pull_exit(report)
            # 카탈로그는 verify 앞에서 돈다 — verify(manifest) 의 카탈로그 snapshot 검사가
            # "재생성하라" 는 finding 을 내는데, 그 재생성이 바로 이 단계다. pull 이 전송 실패 없이
            # 끝났으면 (drifted 여도 로컬 빌드 집합 기준으로) 다시 만든다.
            if args.layer == "equity" and not args.skip_catalog and not report.failed:
                catalog_rc = run_catalog(layer_root, log)
                payload["catalog_rc"] = catalog_rc
                if catalog_rc != EXIT_OK:
                    exit_code = EXIT_ERROR
            state = load_state(layer_root, args.layer)
            verify_report = _run_verify(remote, args, layer_root, state,
                                        [Level.MANIFEST, Level.FILES])
            # 이번에 받은(재사용 포함) 테이블은 내용(hash)까지 본다 — 하루치는 파티션 몇 개라 싸다.
            changed = [r.table for r in report.results if r.outcome is TableOutcome.DONE]
            if changed and verify_report.ok:
                verify_hash(layer_root, state, verify_report, tables=changed)
        _print_verify(verify_report)
        payload["verify"] = _verify_json(verify_report)
        if not verify_report.ok and exit_code == EXIT_OK:
            exit_code = EXIT_MISMATCH
        for warning in report.warnings:
            log(f"warning: {warning}")
    except (RemoteConnectError, RemoteTransferError, InsufficientDiskSpace) as error:
        log(f"sync aborted — {error}")
        payload["error"] = str(error)
        exit_code = EXIT_ERROR
    except BaseException as error:
        payload["error"] = f"unexpected {type(error).__name__}: {error}"
        exit_code = EXIT_ERROR
        raise
    finally:
        payload["finished_at_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
        payload["exit_code"] = exit_code
        _write_last_run(layer_root, payload)
        handle.close()
        _rotate_logs(layer_root)
    print(f"== sync {args.layer}: exit={exit_code} log={log_path}")
    return exit_code


def cmd_status(args: argparse.Namespace) -> int:
    layer_root = _layer_root(args)
    state = load_state(layer_root, args.layer)
    last_path = layer_root / SYNC_DIR / LAST_RUN_NAME
    last: dict[str, object] | None = None
    if last_path.exists():
        try:
            loaded = json.loads(last_path.read_text(encoding="utf-8"))
            last = loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError):
            last = None
    remote_builds: dict[str, str | None] = {}
    remote_errors: dict[str, str] = {}
    if args.remote:
        with open_sftp(_endpoint(args), accept_new_host_key=args.accept_new) as remote:
            remote_root = _remote_root(args)
            for table in list_remote_tables(remote, remote_root):
                view = read_remote_manifest(remote, remote_root, table)
                if view is None:
                    continue
                if not view.ok:
                    remote_errors[table] = view.detail or view.status.value
                    continue
                remote_builds[table] = view.current_build
    behind = sorted(t for t, b in remote_builds.items()
                    if t not in state.tables or state.tables[t].build_id != b)
    if args.json:
        print(json.dumps({
            "layer": args.layer, "root": str(layer_root), "last_run": last,
            "tables": {t: ts.build_id for t, ts in sorted(state.tables.items())},
            "remote": remote_builds or None, "behind": behind if args.remote else None,
            "remote_errors": remote_errors or None,
        }, ensure_ascii=False, indent=1))
        return EXIT_OK
    print(f"== status {args.layer} root={layer_root} tables={len(state.tables)} "
          f"state_updated={state.updated_at_utc or '-'}")
    if last is not None:
        print(f"   last sync: exit={last.get('exit_code')} started={last.get('started_at_utc')} "
              f"finished={last.get('finished_at_utc')}"
              + (f" error={last.get('error')}" if last.get("error") else ""))
    else:
        print("   last sync: never")
    for table, ts in sorted(state.tables.items()):
        line = f"   {table:22s} {ts.build_id}"
        if args.remote:
            rb = remote_builds.get(table)
            line += f"  remote={rb or '-'}" + ("  BEHIND" if table in behind else "")
        print(line)
    if args.remote:
        for table in sorted(set(remote_builds) - set(state.tables)):
            print(f"   {table:22s} -  remote={remote_builds[table]}  BEHIND")
        for table, detail in sorted(remote_errors.items()):
            print(f"   {table:22s} REMOTE_MANIFEST_ERROR {detail}")
        print(f"   behind={len(behind)} remote_errors={len(remote_errors)}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ledger_sync", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=_env("QL_SYNC_ROOT", DEFAULT_ROOT),
                        help=f"로컬 데이터 루트(기본 $QL_SYNC_ROOT 또는 {DEFAULT_ROOT})")
    parser.add_argument("--layer", choices=LAYERS, default="equity")
    parser.add_argument("--remote-root", default="/", help="서버 SFTP 루트(기본 /)")
    parser.add_argument("--host", default=_env("QL_SYNC_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(_env("QL_SYNC_PORT", "22")))
    parser.add_argument("--user", default=_env("QL_SYNC_USER", DEFAULT_USER))
    parser.add_argument("--key", default=_env("QL_SYNC_KEY", DEFAULT_KEY))
    parser.add_argument("--accept-new", action="store_true",
                        help="known_hosts 에 없는 호스트키를 받아들인다(첫 접속만)")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="verb", required=True)

    def add_pull_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--tables", nargs="+", default=None, help="이 테이블만")
        p.add_argument("--no-reuse", action="store_true",
                       help="content_hash 가 같아도 로컬 재사용 없이 전부 받는다(바이트 동일 사본)")
        p.add_argument("--no-reuse-check", action="store_true",
                       help="재사용 직전 로컬 파티션 해시 재계산을 건너뛴다(MANIFEST 값만 믿음)")
        p.add_argument("--keep", type=int, default=KEEP_DEFAULT, help="로컬에 남길 판본 수")
        p.add_argument("--no-space-check", action="store_true")

    p_plan = sub.add_parser("plan", help="무엇을 받을지 보기")
    add_pull_options(p_plan)
    p_plan.set_defaults(fn=cmd_plan)

    p_pull = sub.add_parser("pull", help="새 빌드 수신")
    add_pull_options(p_pull)
    p_pull.set_defaults(fn=cmd_pull)

    p_verify = sub.add_parser("verify", help="서버와 대조")
    p_verify.add_argument("--level", nargs="+", choices=[level.value for level in Level],
                          default=None)
    p_verify.add_argument("--offline", action="store_true", help="hash 만(원격 접속 없음)")
    p_verify.add_argument("--tables", nargs="+", default=None)
    p_verify.set_defaults(fn=cmd_verify)

    p_gc = sub.add_parser("gc", help="로컬 구판본 정리")
    p_gc.add_argument("--keep", type=int, default=KEEP_DEFAULT)
    p_gc.set_defaults(fn=cmd_gc)

    p_catalog = sub.add_parser("catalog", help="equity.duckdb 재생성")
    p_catalog.set_defaults(fn=cmd_catalog)

    p_sync = sub.add_parser("sync", help="pull → verify → catalog")
    add_pull_options(p_sync)
    p_sync.add_argument("--skip-catalog", action="store_true")
    p_sync.set_defaults(fn=cmd_sync)

    p_status = sub.add_parser("status", help="마지막 실행·로컬 빌드")
    p_status.add_argument("--remote", action="store_true", help="서버 current_build 와 대조")
    p_status.set_defaults(fn=cmd_status)
    return parser


def _force_utf8_stdio() -> None:
    """Windows 콘솔 기본 cp949 로는 한글·`—` 출력이 UnicodeEncodeError 로 죽는다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    _reject_conflicting_verify_options(parser, args)
    try:
        return int(args.fn(args))
    except (RemoteConnectError, RemoteTransferError, InsufficientDiskSpace) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR
    except ModuleNotFoundError as error:
        if error.name in ("paramiko", "duckdb"):
            print(f"error: dependency missing — {error.name} (run through "
                  f"database/scripts/ledger_sync.ps1|.sh, which adds it)", file=sys.stderr)
            return EXIT_ERROR
        raise


if __name__ == "__main__":
    raise SystemExit(main())
