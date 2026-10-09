"""scripts/replay.sh — 격리 재생 실행기(컷오버 트랙 X-1).

HOME 을 임시 폴더로 바꿔 `~/quant-ledger`(운영 루트 대역)에 대역 `.venv/bin/python`·`scripts/
equity_order.txt`·`_engine/`·`data/{stage,equity,deliver}` 를 두고 저장소의 진짜 스크립트를 돌린다
(test_equity_rebuild_all_sh 와 같은 방식). 대역 python 은 `-m equity|factor_inputs|model` 호출을
calls.txt 에, 그때의 QL_HOME 을 envs.txt 에 적고 산출 MANIFEST·`_runs` 만 흉내 낸다 — 실제 빌드는
돌지 않는다. 보조 스크립트 `scripts/replay_tool.py`(stage 입력 고정·해시·대조)는 진짜 python 으로
넘긴다. rc 는 FAIL_EQ(그 표에서 rc 7)·RC_CATALOG·RC_CONTRACT·RC_FI·RC_MODEL 로 고른다.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

DB = Path(__file__).resolve().parents[1]
SCRIPT = DB / "scripts" / "replay.sh"
D = "20261008"
EQ_TABLES = ("trading_calendar", "price_daily", "adj_factor")

# 대역 python — 진짜 python 위에서 도는 스크립트라 인자를 쉽게 읽는다.
_FAKE_PY = '''#!{real}
import json
import os
import sys
from pathlib import Path

argv = sys.argv[1:]
if argv and argv[0].endswith("replay_tool.py"):
    with open(os.environ["QL_CALLS"], "a", encoding="utf-8") as f:
        f.write("tool " + " ".join(argv[1:2]) + "\\n")
    os.execv(sys.executable, [sys.executable, *argv])
with open(os.environ["QL_CALLS"], "a", encoding="utf-8") as f:
    f.write(" ".join(argv) + "\\n")
with open(os.environ["QL_ENVS"], "a", encoding="utf-8") as f:
    f.write(os.environ.get("QL_HOME", "") + "\\n")


def opt(name):
    return argv[argv.index(name) + 1]


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


mod = argv[1] if argv[:1] == ["-m"] else ""
if mod == "equity" and "build" in argv:
    t = argv[argv.index("build") + 1]
    if t == os.environ.get("FAIL_EQ"):
        sys.exit(7)
    bid = opt("--basis")[0] + "_fake"
    write(Path(opt("--root")) / t / "MANIFEST.json",
          {"table": t, "current_build": bid,
           "builds": [{"build_id": bid, "content_hash": "h-" + t, "n_rows": 3}]})
    sys.exit(0)
if mod == "equity" and "catalog" in argv:
    sys.exit(int(os.environ.get("RC_CATALOG", "0")))
if mod == "equity" and "contract" in argv:
    sys.exit(int(os.environ.get("RC_CONTRACT", "0")))
if mod == "factor_inputs":
    rc = int(os.environ.get("RC_FI", "0"))
    if rc == 0:
        root, d, b = Path(opt("--root")), opt("--date"), opt("--basis")
        run = {"status": "ok", "build_id": "m_fi", "date": d, "basis": b,
               "tables": {"fi_universe": {"content_hash": "h-fi", "n_rows": 5}}}
        write(root / "_runs" / (d + "_" + b + ".json"), run)
        write(root / ("latest_" + b + ".json"), run)
    sys.exit(rc)
if mod == "model":
    rc = int(os.environ.get("RC_MODEL", "0"))
    if rc == 0:
        root, d, b = Path(opt("--root")), opt("--date"), opt("--basis")
        write(root / "_runs" / (d + "_" + b + ".json"),
              {"status": "ok", "build_id": "m_model", "specs": {"scope@1.0": {}}})
        write(root / "scope@1.0" / "MANIFEST.json",
              {"table": "scope@1.0", "current_build": "m_model",
               "builds": [{"build_id": "m_model", "content_hash": "h-scope", "n_rows": 593}]})
    sys.exit(rc)
sys.exit(0)
'''


class Run(NamedTuple):
    rc: int
    out: str              # stdout + stderr
    calls: list[str]      # 대역 python 이 받은 호출(`-m …` 줄과 `tool <동사>` 줄)
    envs: list[str]       # `-m …` 호출 때의 QL_HOME

    @property
    def builds(self) -> list[str]:
        """빌드·판정 호출만(보조 스크립트 호출 제외)."""
        return [c for c in self.calls if c.startswith("-m ")]


def _write(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _stage_table(table_root: Path, build_ids: list[str]) -> None:
    """stage 표 대역 — 판마다 `v=<id>/year=2020/part0.parquet`(내용은 표·판 이름) + MANIFEST."""
    recs = []
    for bid in build_ids:
        part = table_root / f"v={bid}" / "year=2020"
        part.mkdir(parents=True)
        (part / "part0.parquet").write_bytes(f"{table_root.name}-{bid}".encode())
        (part / "_meta.json").write_text("{}", encoding="utf-8")
        recs.append({"build_id": bid, "snapshot_id": "snap", "rules_version": "s1",
                     "built_at_utc": "2026-10-09T00:00:00+00:00", "n_rows": 1,
                     "content_hash": f"c-{bid}",
                     "partitions": [{"path": f"v={bid}/year=2020", "n_rows": 1}]})
    _write(table_root / "MANIFEST.json", {"table": table_root.name,
                                          "current_build": build_ids[-1], "keep": 3,
                                          "builds": recs})


def _ops(home: Path) -> Path:
    """운영 루트 대역. 인계 이력 D 의 stage 판은 셋 — 운영 stage keep 안(stg_price_daily) ·
    keep 밖이라 equity `_pinned/` 에만 남은 판(stg_listing_daily) ·
    어디에도 없는 판(stg_fin_wise_q)."""
    ops = home / "quant-ledger"
    (ops / "src").mkdir(parents=True)
    (ops / "_engine").mkdir()
    (ops / "scripts").mkdir()
    (ops / "scripts" / "equity_order.txt").write_text(
        "# 주석 줄\n" + "\n".join(EQ_TABLES) + "\n\n", encoding="utf-8")
    py = ops / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(_FAKE_PY.replace("{real}", sys.executable), encoding="utf-8")
    py.chmod(0o755)
    data = ops / "data"
    _stage_table(data / "stage" / "stg_price_daily", ["m_1008", "e_1009", "m_1009"])
    _stage_table(data / "stage" / "stg_listing_daily", ["e_1009", "m_1009", "m_1010"])
    _stage_table(data / "equity" / "_pinned" / "stg_listing_daily", ["m_1008"])
    _stage_table(data / "stage" / "stg_fin_wise_q", ["e_1009", "m_1009", "m_1010"])
    _write(data / "deliver" / "history" / f"{D}_morning.json",
           {"date": D, "basis": "morning", "stage_snapshot_id": "snap",
            "stage_builds": {"stg_price_daily": "m_1008", "stg_listing_daily": "m_1008",
                             "stg_fin_wise_q": "m_1008"},
            "equity_builds": {"price_daily": "m_old"},
            "health": {"stage": "ok", "equity": "ok"}})
    eq = data / "equity"
    for name in ("baseline.json", "baseline_seed_s02.json", "_seed_s01.json",
                 "_catalog_meta.json", "_contract_meta.json"):
        _write(eq / name, {"name": name})
    (eq / "_asof" / "v_adj_close" / "snap1").mkdir(parents=True)
    (eq / "_asof" / "v_adj_close" / "snap1" / "sample.parquet").write_bytes(b"asof")
    _write(eq / "fixtures" / "price_daily.json", {"fixture": 1})
    # 운영 equity 표 — 재생 루트로 복사하면 안 된다
    _write(eq / "price_daily" / "MANIFEST.json",
           {"table": "price_daily", "current_build": "m_ops",
            "builds": [{"build_id": "m_ops", "content_hash": "h-ops", "n_rows": 9}]})
    return ops


def _snapshot(root: Path) -> dict[str, tuple[str, str]]:
    """경로 → (종류, 내용 해시 또는 링크 대상). 심볼릭 링크는 따라가지 않는다."""
    out: dict[str, tuple[str, str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            rel = str(p.relative_to(root))
            if p.is_symlink():
                out[rel] = ("link", os.readlink(p))
            elif p.is_dir():
                out[rel] = ("dir", "")
            else:
                out[rel] = ("file", hashlib.sha256(p.read_bytes()).hexdigest())
    return out


def _run(home: Path, *args: str, **env: object) -> Run:
    calls, envs = home / "calls.txt", home / "envs.txt"
    calls.touch()
    envs.touch()
    e = dict(os.environ, HOME=str(home), QL_CALLS=str(calls), QL_ENVS=str(envs),
             **{k: str(v) for k, v in env.items()})
    for k in ("QL_HOME", "PYTHONPATH"):
        e.pop(k, None)
    p = subprocess.run(["bash", str(SCRIPT), *args], env=e, capture_output=True, text=True,
                       timeout=60, check=False)
    return Run(p.returncode, p.stdout + p.stderr, calls.read_text(encoding="utf-8").splitlines(),
               envs.read_text(encoding="utf-8").splitlines())


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    h = tmp_path.resolve()
    _ops(h)
    return h


def _eq_build(out: Path, stage: Path, t: str, basis: str = "morning") -> str:
    return (f"-m equity --root {out}/data/equity --stage-root {stage} build {t} --basis {basis} "
            "--threads 3 --memory-limit 8GB --keep 3")


# ── 인자 ──────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("args", [
    [],                                                          # --out 없음
    ["--out", "{out}"],                                          # fi·model 이 있는데 --date 없음
    ["--out", "{out}", "--date"],                                # 값 없는 인자
    ["--out", "{out}", "--date", "2026-10-08"],                  # 날짜 형식
    ["--out", "{out}", "--date", D, "--basis", "noon"],
    ["--out", "{out}", "--date", D, "--steps", "equity,stage"],  # 모르는 단계
    ["--out", "{out}", "--date", D, "--steps", ","],             # 빈 단계
    ["--out", "{out}", "--date", D, "--bogus", "1"],
    ["--out", "{out}", "--date", D, "--engine", "{home}/no_engine"],   # contract 엔진 없음
    ["--out", "{out}", "--date", D, "--code", "{home}/no_code"],
    ["--out", "{out}", "--date", D, "--stage-at", "20260101"],   # 인계 이력 없음
    ["--out", "{out}", "--date", D, "--stage-at", "2026010"],
])
def test_인자_오류는_rc2_이고_아무것도_만들거나_돌리지_않는다(home: Path, args: list[str]) -> None:
    out = home / "replay" / "x"
    r = _run(home, *[a.format(out=out, home=home) for a in args])
    assert r.rc == 2, r.out
    assert r.calls == []
    assert not (home / "replay").exists()


@pytest.mark.parametrize("rel", ["quant-ledger/data/replay", "quant-ledger/r", "quant-ledger",
                                 ".", "link/r"])
def test_운영_홈_안이나_그_조상을_출력_루트로_주면_거부한다(home: Path, rel: str) -> None:
    """심볼릭 링크로 돌아 들어가도(link → 운영 data) 실제 경로로 판정한다."""
    (home / "link").symlink_to(home / "quant-ledger" / "data")
    before = _snapshot(home / "quant-ledger")
    r = _run(home, "--out", str(home / rel), "--date", D)
    assert r.rc == 2, r.out
    assert "운영" in r.out
    assert r.calls == []
    assert _snapshot(home / "quant-ledger") == before


# ── 단계 순서 ─────────────────────────────────────────────────────────────────
def test_기본은_다섯_단계를_운영_흐름_순서로_출력_루트에_짓는다(home: Path) -> None:
    out, ops = home / "replay" / "pre", home / "quant-ledger"
    r = _run(home, "--out", str(out), "--date", D)
    assert r.rc == 0, r.out
    stage = ops / "data" / "stage"
    assert r.builds == [
        *[_eq_build(out, stage, t) for t in EQ_TABLES],
        f"-m equity --root {out}/data/equity --stage-root {stage} catalog",
        f"-m equity --root {out}/data/equity --stage-root {stage} contract "
        f"--engine-src {ops}/_engine",
        f"-m factor_inputs build --date {D} --basis morning --root {out}/data/factor_inputs "
        f"--stage-root {stage} --equity-root {out}/data/equity",
        f"-m model build --date {D} --basis morning --fi-build latest --specs all "
        f"--root {out}/data/model --fi-root {out}/data/factor_inputs",
    ]
    # QL_HOME 도 출력 루트 — 기본 경로가 운영으로 새지 않는다
    assert set(r.envs) == {str(out)}


def test_steps_는_적은_순서와_무관하게_정해진_순서로_돈다(home: Path) -> None:
    out = home / "replay" / "x"
    r = _run(home, "--out", str(out), "--date", D, "--steps", "model,equity", "--basis",
             "evening")
    assert r.rc == 0, r.out
    stage = home / "quant-ledger" / "data" / "stage"
    assert r.builds == [
        *[_eq_build(out, stage, t, "evening") for t in EQ_TABLES],
        f"-m model build --date {D} --basis evening --fi-build latest --specs all "
        f"--root {out}/data/model --fi-root {out}/data/factor_inputs",
    ]


def test_code_engine_을_주면_그_코드와_엔진으로_돈다(home: Path) -> None:
    code, engine = home / "post", home / "eng"
    (code / "src").mkdir(parents=True)
    (code / "scripts").mkdir()
    (code / "scripts" / "equity_order.txt").write_text("corp\n", encoding="utf-8")
    engine.mkdir()
    out = home / "replay" / "post"
    r = _run(home, "--out", str(out), "--date", D, "--code", str(code), "--engine", str(engine),
             "--steps", "equity,contract")
    assert r.rc == 0, r.out
    assert [c.split()[7] for c in r.builds if " build " in c] == ["corp"]
    assert r.builds[-1].endswith(f"contract --engine-src {engine}")
    assert os.readlink(out / "scripts") == str(code / "scripts")


# ── 실패 전파 ─────────────────────────────────────────────────────────────────
def _tsv(out: Path, n: int = 1) -> list[list[str]]:
    lines = (out / "logs" / f"pass{n}" / "summary.tsv").read_text(encoding="utf-8").splitlines()
    return [ln.split("\t") for ln in lines]


def test_equity_표가_실패하면_남은_표와_뒤_단계를_돌지_않는다(home: Path) -> None:
    """혼합 판 위에 짓지 않는다 — 두 번째 패스부터는 실패한 표 자리에 앞 패스 판이 남아 있다."""
    out = home / "replay" / "x"
    r = _run(home, "--out", str(out), "--date", D, FAIL_EQ="price_daily")
    assert r.rc == 1, r.out
    assert [c.split()[7] for c in r.builds] == ["trading_calendar", "price_daily"]
    rows = {(x[0], x[1]): x[2] for x in _tsv(out)[1:]}
    assert rows[("equity", "price_daily")] == "7"
    assert rows[("equity", "adj_factor")] == "skip"
    for step in ("catalog", "contract", "fi", "model"):
        assert rows[(step, "-")] == "skip"


def test_catalog_실패는_contract_만_막고_fi_model_은_돈다(home: Path) -> None:
    r = _run(home, "--out", str(home / "replay" / "x"), "--date", D, RC_CATALOG=1)
    assert r.rc == 1, r.out
    verbs = [c.split()[2] if c.startswith("-m factor_inputs") or c.startswith("-m model")
             else c.split()[6] for c in r.builds]
    assert verbs[3:] == ["catalog", "build", "build"]


def test_contract_실패는_기록만_하고_fi_model_은_돈다(home: Path) -> None:
    out = home / "replay" / "x"
    r = _run(home, "--out", str(out), "--date", D, RC_CONTRACT=5)
    assert r.rc == 1, r.out
    assert [c.split()[1] for c in r.builds[-2:]] == ["factor_inputs", "model"]
    assert "contract(rc=5)" in (out / "logs" / "pass1" / "summary.txt").read_text(
        encoding="utf-8")


def test_fi_실패는_model_을_막는다(home: Path) -> None:
    """model 은 `--fi-build latest` 라 fi 가 실패하면 앞 패스의 같은 D fi 판을 조용히 읽는다."""
    r = _run(home, "--out", str(home / "replay" / "x"), "--date", D, RC_FI=2)
    assert r.rc == 1, r.out
    assert r.builds[-1].startswith("-m factor_inputs")


# ── 운영 루트 무쓰기 · 기준 파일 ─────────────────────────────────────────────
def test_운영_루트에는_쓰지_않는다(home: Path) -> None:
    ops = home / "quant-ledger"
    before = _snapshot(ops)
    out = home / "replay" / "x"
    assert _run(home, "--out", str(out), "--date", D).rc == 0
    r = _run(home, "--out", str(out), "--date", D, "--stage-at", D)
    assert r.rc == 0, r.out
    assert _snapshot(ops) == before
    for call in r.builds:
        a = call.split()
        for flag in ("--root", "--fi-root", "--equity-root"):
            if flag in a:
                assert a[a.index(flag) + 1].startswith(f"{out}/"), call


def test_기준_파일은_첫_패스에만_운영에서_복사하고_표_폴더는_복사하지_않는다(home: Path) -> None:
    ops_eq = home / "quant-ledger" / "data" / "equity"
    out = home / "replay" / "x"
    eq = out / "data" / "equity"
    assert _run(home, "--out", str(out), "--date", D, "--steps", "catalog").rc == 0
    for name in ("baseline.json", "baseline_seed_s02.json", "_seed_s01.json",
                 "_catalog_meta.json", "_contract_meta.json"):
        assert (eq / name).read_bytes() == (ops_eq / name).read_bytes()
    assert (eq / "_asof" / "v_adj_close" / "snap1" / "sample.parquet").read_bytes() == b"asof"
    assert (eq / "fixtures" / "price_daily.json").exists()
    assert not (eq / "price_daily").exists()      # 운영 표 판은 복사하지 않는다
    assert not (eq / "_pinned").exists()
    # 둘째 패스는 운영 기준 파일이 바뀌어도 다시 복사하지 않는다(게이트 기준 = 첫 패스 사본)
    _write(ops_eq / "baseline.json", {"changed": True})
    assert _run(home, "--out", str(out), "--date", D, "--steps", "catalog").rc == 0
    assert json.loads((eq / "baseline.json").read_text(encoding="utf-8")) == {
        "name": "baseline.json"}
    assert sorted(p.name for p in (out / "logs").iterdir()) == ["pass1", "pass2"]


# ── --stage-at ───────────────────────────────────────────────────────────────
def test_stage_at_은_인계_이력의_판을_가리키는_임시_stage_루트를_만든다(home: Path) -> None:
    ops = home / "quant-ledger" / "data"
    out = home / "replay" / "at"
    r = _run(home, "--out", str(out), "--date", D, "--stage-at", D, "--steps", "equity,fi")
    assert r.rc == 0, r.out
    tmp = out / "stage_at" / "pass1"
    assert {c.split()[c.split().index("--stage-root") + 1] for c in r.builds} == {str(tmp)}

    def manifest(t: str) -> dict:
        return json.loads((tmp / t / "MANIFEST.json").read_text(encoding="utf-8"))

    # 운영 stage keep 안의 판 → 운영 stage 판 디렉터리를 가리킨다, BuildRecord 는 그 판 1개
    m = manifest("stg_price_daily")
    assert m["current_build"] == "m_1008"
    assert [b["build_id"] for b in m["builds"]] == ["m_1008"]
    assert m["builds"][0]["partitions"] == [{"path": "v=m_1008/year=2020", "n_rows": 1}]
    link = tmp / "stg_price_daily" / "v=m_1008"
    assert link.is_symlink()
    assert Path(os.readlink(link)) == ops / "stage" / "stg_price_daily" / "v=m_1008"
    assert (link / "year=2020" / "part0.parquet").read_bytes() == b"stg_price_daily-m_1008"
    # keep 밖이라 운영 stage 에서 지워진 판 → equity _pinned 사본
    link = tmp / "stg_listing_daily" / "v=m_1008"
    assert Path(os.readlink(link)) == ops / "equity" / "_pinned" / "stg_listing_daily" / "v=m_1008"
    assert manifest("stg_listing_daily")["current_build"] == "m_1008"
    # 어디에도 없는 판 → MANIFEST 만(판 디렉터리 없음), 기록과 출력에 남긴다
    assert manifest("stg_fin_wise_q")["current_build"] == "m_1008"
    assert not (tmp / "stg_fin_wise_q" / "v=m_1008").exists()
    srcs = {ln.split("\t")[0]: ln.split("\t")[2] for ln in
            (out / "logs" / "pass1" / "stage_at.tsv").read_text(encoding="utf-8").splitlines()}
    assert srcs == {"stg_fin_wise_q": "missing", "stg_listing_daily": "pinned",
                    "stg_price_daily": "stage"}
    assert "stg_fin_wise_q" in r.out
    # 다음 패스는 새 경로에 다시 세운다
    assert _run(home, "--out", str(out), "--date", D, "--stage-at", D, "--steps", "fi").rc == 0
    assert (out / "stage_at" / "pass2" / "stg_price_daily" / "v=m_1008").is_symlink()


# ── 요약 ──────────────────────────────────────────────────────────────────────
def test_요약은_단계별_rc_와_표별_해시를_남긴다(home: Path) -> None:
    out = home / "replay" / "x"
    r = _run(home, "--out", str(out), "--date", D)
    assert r.rc == 0, r.out
    rows = _tsv(out)
    assert rows[0] == ["step", "table", "rc", "sec", "build_id", "content_hash", "n_rows"]
    by = {(x[0], x[1]): x for x in rows[1:]}
    for t in EQ_TABLES:
        assert by[("equity", t)][2] == "0"
        assert by[("equity", t)][4:] == ["m_fake", f"h-{t}", "3"]
    for step in ("catalog", "contract", "fi", "model"):
        assert by[(step, "-")][2] == "0"
    assert by[("fi", "fi_universe")][4:] == ["m_fi", "h-fi", "5"]
    assert by[("model", "scope@1.0")][4:] == ["m_model", "h-scope", "593"]
    line = (out / "logs" / "pass1" / "summary.txt").read_text(encoding="utf-8").strip()
    assert "\n" not in line
    assert line in r.out
    for word in ("pass1", f"D={D}", "stage=current", "equity 3/3", "실패 없음"):
        assert word in line


# ── --compare ────────────────────────────────────────────────────────────────
def _root(base: Path, eq: dict[str, tuple[str, str]], fi_hash: str, model_hash: str) -> Path:
    """대조용 루트 — equity 표별 (current_build, hash) · fi·model 의 D 판."""
    data = base / "data"
    for t, (bid, h) in eq.items():
        _write(data / "equity" / t / "MANIFEST.json",
               {"table": t, "current_build": bid,
                "builds": [{"build_id": "m_old", "content_hash": "h-old", "n_rows": 1},
                           {"build_id": bid, "content_hash": h, "n_rows": 2}]})
    _write(data / "factor_inputs" / "_runs" / f"{D}_morning.json",
           {"status": "ok", "build_id": "m_fi",
            "tables": {"fi_universe": {"content_hash": fi_hash, "n_rows": 5}}})
    _write(data / "model" / "_runs" / f"{D}_morning.json",
           {"status": "ok", "build_id": "m_mo", "specs": {"scope@1.0": {}}})
    _write(data / "model" / "scope@1.0" / "MANIFEST.json",
           {"table": "scope@1.0", "current_build": "m_mo",
            "builds": [{"build_id": "m_mo", "content_hash": model_hash, "n_rows": 593}]})
    return base


def test_compare_는_표별_해시를_대조하고_운영쪽_equity_는_인계_이력의_판을_쓴다(home: Path) -> None:
    """운영 루트는 그 뒤 저녁·다음 날 판이 current 라,
    D 의 equity 판은 인계 이력이 가리키는 판이다."""
    a = _root(home / "a", {"corp": ("m_fake", "h-old")}, "h-fi", "h-scope")
    b = _root(home / "b", {"corp": ("e_new", "h-new")}, "h-fi", "h-scope")
    _write(b / "data" / "deliver" / "history" / f"{D}_morning.json",
           {"equity_builds": {"corp": "m_old"}})
    before = _snapshot(home)
    r = _run(home, "--out", str(a), "--compare", str(b), "--date", D)
    assert r.rc == 0, r.out
    assert r.builds == []
    assert "equity\tcorp\tm_fake\th-old\tm_old\th-old\t같음" in r.out
    assert "fi\tfi_universe\tm_fi\th-fi\tm_fi\th-fi\t같음" in r.out
    assert "model\tscope@1.0\tm_mo\th-scope\tm_mo\th-scope\t같음" in r.out
    assert "다름 0" in r.out
    assert {k: v for k, v in _snapshot(home).items() if not k.endswith(".txt")} == {
        k: v for k, v in before.items() if not k.endswith(".txt")}       # 읽기 전용


def test_compare_는_다르거나_한쪽에_없으면_rc1(home: Path) -> None:
    a = _root(home / "a", {"corp": ("m_x", "h-1"), "security": ("m_x", "h-s")}, "h-fi", "h-m")
    b = _root(home / "b", {"corp": ("m_y", "h-2")}, "h-fi", "h-m")
    r = _run(home, "--out", str(a), "--compare", str(b), "--date", D)
    assert r.rc == 1, r.out
    assert "equity\tcorp\tm_x\th-1\tm_y\th-2\t다름" in r.out
    assert "equity\tsecurity\tm_x\th-s\t-\t-\t없음" in r.out


# ── 임시 stage 루트 ↔ 실제 읽기 계약(equity.inputs · factor_inputs) ──────────────
def test_임시_stage_루트는_실제_equity_pin_과_fi_해석으로_읽힌다(tmp_path: Path,
                                                         make_stage_tree) -> None:
    """대역 python 이 아니라 진짜 `inputs.pin`·fi `_resolve` 로 읽는다. keep 밖이라 운영 stage 에서
    지워진 판은 운영 `_pinned/` 파일에, keep 안의 판은 운영 stage 파일에 하드링크된다(같은 inode —
    내용 사본 없음). 어디에도 없는 판은 fi 의 선택 원천이라도 'absent'(빈 표)로 바뀌지 않고 읽을 때
    실패한다."""
    import datetime as dt

    import duckdb
    from equity import inputs

    # 패키지가 build 함수를 내보내 모듈 이름을 가린다 — 이름을 직접 가져온다
    from factor_inputs.build import _resolve as fi_resolve

    ops = tmp_path.resolve() / "ops" / "data"
    rows = [{"date": dt.date(2020, 1, 2), "ticker": "005930", "val": 1},
            {"date": dt.date(2021, 1, 4), "ticker": "000660", "val": 2}]
    make_stage_tree(ops, "stg_price_daily", rows, partition_class="date_axis", build_id="m_old")
    inputs.pin(ops / "stage", ops / "equity", "stg_price_daily")      # 그날 운영 equity 가 고정
    for bid in ("m_a", "m_b", "m_c"):            # keep 3 → m_old 는 stage 에서 GC
        make_stage_tree(ops, "stg_price_daily", rows, partition_class="date_axis", build_id=bid)
    assert not (ops / "stage" / "stg_price_daily" / "v=m_old").exists()
    make_stage_tree(ops, "stg_listing_daily", rows[:1], build_id="m_cur")
    _write(ops / "deliver" / "history" / f"{D}_morning.json",
           {"stage_builds": {"stg_price_daily": "m_old", "stg_listing_daily": "m_cur",
                             "stg_fin_wise_q": "m_gone"}})
    before = _snapshot(ops)
    dest = tmp_path.resolve() / "out" / "stage_at" / "pass1"
    p = subprocess.run([sys.executable, str(DB / "scripts" / "replay_tool.py"), "stage-at",
                        "--ops-data", str(ops), "--date", D, "--basis", "morning",
                        "--dest", str(dest)], capture_output=True, text=True, check=False)
    assert p.returncode == 0, p.stderr
    out_eq = tmp_path.resolve() / "out" / "data" / "equity"

    pb = inputs.pin(dest, out_eq, "stg_price_daily")
    assert pb.build_id == "m_old"
    con = duckdb.connect()
    inputs.create_views(con, {"stg_price_daily": pb}, {})
    assert con.execute('SELECT ticker, val FROM "stg_price_daily" ORDER BY val').fetchall() == [
        ("005930", 1), ("000660", 2)]
    for f in (out_eq / "_pinned" / "stg_price_daily" / "v=m_old").rglob("*.parquet"):
        src = ops / "equity" / "_pinned" / "stg_price_daily" / f.relative_to(
            out_eq / "_pinned" / "stg_price_daily")
        assert f.stat().st_ino == src.stat().st_ino
    pb = inputs.pin(dest, out_eq, "stg_listing_daily")
    f = next((out_eq / "_pinned" / "stg_listing_daily" / "v=m_cur").glob("*.parquet"))
    src = ops / "stage" / "stg_listing_daily" / "v=m_cur" / f.name
    assert f.stat().st_ino == src.stat().st_ino

    with pytest.raises(FileNotFoundError):
        inputs.pin(dest, out_eq, "stg_fin_wise_q")
    ids, exprs = fi_resolve(dest, ("stg_fin_wise_q",), stage=True)
    assert ids == {"stg_fin_wise_q": "m_gone"}          # 'absent' 로 조용히 대체되지 않는다
    with pytest.raises(duckdb.Error):
        con.execute(f"SELECT * FROM {exprs['stg_fin_wise_q']}")
    assert _snapshot(ops) == before                      # 운영 쪽은 내용·구성 그대로
