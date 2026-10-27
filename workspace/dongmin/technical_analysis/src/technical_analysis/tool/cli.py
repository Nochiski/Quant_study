"""`ta` 명령.

    ta 삼성전자                 # 웹 페이지를 만들고 브라우저로 연다
    ta 코스피 --png             # PNG·텔레그램용 글도 만든다
    ta channel set 코스피 2019-08-06:low 2022-09-30:low 2024-07-11:high
    ta channel list
    ta channel rm 코스피
    ta panic                    # 오늘 시장 투매 비율, 투매일이면 전진 관찰 기록에 한 줄
"""

import argparse
import subprocess
import sys
from pathlib import Path

from .. import data
from .. import market as mk
from . import channels as chs
from . import render, report


def _find(root: Path, query: str) -> data.Match | None:
    r = data.lookup(root, query)
    if r.ok and r.match is not None:
        return r.match
    if r.status is data.LookupStatus.AMBIGUOUS:
        print(f"'{query}' 에 맞는 것이 여럿이다. 이름이나 코드를 정확히 적어 주세요:")
        for m in r.candidates:
            print(f"  {m.code:>8}  {m.name}  ({'지수' if m.kind == 'index' else m.sec_type})")
    else:
        print(f"'{query}' 를 찾지 못했다(종목명·코드·지수명).")
    return None


def _channel(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ta channel", description="사용자 지정 피보나치 채널 저장")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set", help="세 점 저장: 날짜:low|high 세 개")
    s.add_argument("query")
    s.add_argument("points", nargs=3)
    sub.add_parser("list", help="저장한 채널 보기")
    r = sub.add_parser("rm", help="저장한 채널 지우기")
    r.add_argument("query")
    args = ap.parse_args(argv)
    path = chs.store_path()
    root = data.equity_root()
    if args.cmd == "list":
        for key, v in chs.load_all(path).items():
            pts = " ".join(f"{p.date}:{p.side}" for p in v.points)
            print(f"{key}  {v.name}  {pts}  (저장 {v.saved_at})")
        return 0
    m = _find(root, args.query)
    if m is None:
        return 1
    if args.cmd == "rm":
        print("지웠다" if chs.remove(path, m.code) else "저장된 채널이 없다")
        return 0
    try:
        specs = [chs.parse_point(p) for p in args.points]
    except ValueError as e:
        print(e)
        return 1
    s_ = data.load(root, m)
    res = chs.resolve(specs, s_.dates, s_.bars.high, s_.bars.low)
    if not res.ok or res.channel is None:
        print(f"저장하지 않았다: {res.detail}")
        return 1
    chs.save(path, m.code, m.name, specs)
    c = res.channel
    print(f"{m.name} 지정 채널 저장: ① {specs[0].date} {c.ya:,.2f} · ② {specs[1].date} {c.yb:,.2f} · ③ {specs[2].date} {c.yc:,.2f}")
    print(f"  기울기 {c.slope:,.4f}/봉 · 폭 {c.width:,.2f} · 0.5 간격 {abs(c.width) / 2:,.2f}")
    return 0


def _panic() -> int:
    snap = mk.snapshot(data.equity_root())
    tickers = snap.panic_tickers()
    print(f"{snap.date} 유니버스 {int(snap.in_universe.sum())}종목 중 RSI 30 아래 {len(tickers)}종목 = {snap.panic * 100:.1f}%"
          f" (투매일 기준 {mk.PANIC_FRACTION * 100:.0f}%)")
    if snap.panic >= mk.PANIC_FRACTION:
        log = Path("~/quant-data/technical-analysis/forward_p2.csv").expanduser()
        new = mk.log_panic(log, snap.date, snap.panic, tickers)
        print(f"투매일 — 전진 관찰 기록 {'추가' if new else '이미 있음'}: {log}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["channel"]:
        return _channel(argv[1:])
    if argv[:1] == ["panic"]:
        return _panic()
    ap = argparse.ArgumentParser(prog="ta", description="종목·지수 기술적 상태 한 장")
    ap.add_argument("query", help="종목명·코드·지수명")
    ap.add_argument("--png", action="store_true", help="PNG 와 텔레그램용 글도 만든다")
    ap.add_argument("--no-open", action="store_true", help="브라우저로 열지 않는다")
    ap.add_argument("--out", type=Path, default=None, help="저장 폴더(기본 ~/quant-data/technical-analysis/out)")
    args = ap.parse_args(argv)
    root = data.equity_root()
    m = _find(root, args.query)
    if m is None:
        return 1
    stored = chs.load_all(chs.store_path()).get(m.code)
    payload = report.build(root, m, stored, report.load_scorecard(report.scorecard_path()))
    out = (args.out or render.out_dir()).expanduser()
    stem = render.file_stem(payload["meta"])  # pyright: ignore[reportArgumentType]  # reason: build 가 meta 를 사전으로 만든다
    html = render.write_html(payload, out / f"{stem}.html")
    print(f"웹 페이지: {html}")
    if args.png:
        png = render.write_png(html, out / f"{stem}.png")
        txt = out / f"{stem}.txt"
        txt.write_text(render.caption(payload))
        print(f"PNG: {png}\n글: {txt}")
    if not args.no_open:
        subprocess.run(["open", str(html)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
