"""연속 판 접기 (STAGE_DESIGN §1 예외 (f), 묶음 7-1) — 빌드 두 길(회사별·한 번에)과 아침 재사용 판정(7-3)이
이 모듈 한 곳의 함수를 쓴다.

원장 `ws_raw` 는 수집일마다 같은 원문을 새 판으로 저장한다(PK 에 fetched_date). `BlobSource.fold_consecutive`
표는 같은 (cmp_cd, ep, pkey) 단위에서 fetched_date 가 **바로 앞**인 원장 blob 과 sha256 이 같은 blob 을
파싱하지 않는다. 원장에는 모든 판이 남는다 — 접기는 stage 산출만 줄인다.

- 단위의 첫 blob 은 늘 남는다(첫 판·중간에 들어온 종목).
- 비교 상대는 바로 앞 blob 하나다 — A→B→A 의 셋째 A 는 남는다(DART `backfill_dart.py:481-484` 는 버리지만
  따르지 않는다. cF4002 는 하루 최대 19건이 되돌아온다, 10-09 서버 실측).
- 수집 공백은 무시한다 — A(d1)·없음(d2)·A(d3) 이면 d1 만 남는다(D7-1).
- 접기 판정의 정렬은 파이썬에서 한다(SQL ORDER BY 에 기대지 않는다). 단위가 회사 안에 있어 묶음 크기와
  무관하다. 지문(`input_fingerprint`)만 원장 PK 순서 ORDER BY 로 스트리밍한다.
- 같은 원문 판정은 원장 sha256 열(압축 전 원문 해시, `backfill_wise.py:412`)이다. 남긴 blob 은 압축을 풀어
  sha256 을 다시 계산해 대조한다(`n_sha_mismatch` — G8 폐기형, 열 뜻 오류를 첫 빌드에서 잡는다, D7-3).
"""
from __future__ import annotations

import hashlib
import sqlite3
import zlib
from collections.abc import Iterable
from dataclasses import dataclass, field

from .model import RULES_VERSION, BlobSource
from .parsers import RawBlob

Unit = tuple[str, str, str]          # (cmp_cd, ep, pkey)


def _label(ep: str, pkey: str) -> str:
    return f"{ep}:{pkey}"


@dataclass(frozen=True)
class Folded:
    kept: list[RawBlob]              # 단위·fetched_date 순
    n_folded: dict[str, int]         # 'ep:pkey' → 버린 blob 수 (입력에 있던 ep:pkey 는 0 이어도 싣는다)
    last_date: dict[Unit, str]       # 단위 → 원장 마지막 fetched_date (n_keys_stale 입력)


def fold_consecutive(blobs: Iterable[RawBlob]) -> Folded:
    """바로 앞 blob 과 sha256 이 같은 blob 을 버린다. 단위 (cmp_cd, ep, pkey) — ep·pkey 마다 따로."""
    kept: list[RawBlob] = []
    folded: dict[str, int] = {}
    last: dict[Unit, str] = {}
    prev_unit: Unit | None = None
    prev_sha = ""
    for b in sorted(blobs, key=lambda x: (x.cmp_cd, x.ep, x.pkey, x.fetched_date)):
        unit = (b.cmp_cd, b.ep, b.pkey)
        label = _label(b.ep, b.pkey)
        folded.setdefault(label, 0)
        if unit == prev_unit and b.sha256 == prev_sha:
            folded[label] += 1
        else:
            kept.append(b)
        prev_unit, prev_sha = unit, b.sha256
        last[unit] = b.fetched_date
    return Folded(kept, folded, last)


def sha_mismatch(b: RawBlob) -> bool:
    """원장 sha256 이 원문의 sha256 과 다른가. 원문은 파서와 같은 규칙으로 얻는다 — 본문이 zlib 머리(`78 9C`)로
    시작할 때만 압축을 풀고 아니면 본문 그대로(`parsers._decode_json`). 압축 해제 실패만 False — 파서가
    parse_failed 로 센다(sha 불일치로 세지 않는다)."""
    raw = bytes(b.body)
    if raw[:2] == b"\x78\x9c":
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            return False
    return hashlib.sha256(raw).hexdigest() != b.sha256


def keys_stale(last_date: dict[Unit, str]) -> dict[str, int]:
    """'ep:pkey' → 마지막 원장 blob 날짜가 그 ep·pkey 의 최신 수집일보다 이른 단위 수(기록형 — 커버 끊김·폐지).

    접은 뒤에는 stage 의 fetched_date 가 '처음 본 날'이라 '마지막 확인일'이 stage 에서 사라진다. 그중 커버가
    끊긴 단위만 여기서 센다(범위 밖 TECH_DEBT 8번의 일부 대신).
    """
    newest: dict[str, str] = {}
    for (_, ep, pkey), d in last_date.items():
        label = _label(ep, pkey)
        newest[label] = max(newest.get(label, d), d)
    out = dict.fromkeys(sorted(newest), 0)
    for (_, ep, pkey), d in last_date.items():
        label = _label(ep, pkey)
        if d < newest[label]:
            out[label] += 1
    return out


@dataclass
class Tally:
    """빌드 한 번의 접기 계상. 회사 묶음마다 `fold` 를 부르고 끝에 `metrics()` 를 싣는다."""

    n_folded: dict[str, int] = field(default_factory=dict)
    n_sha_mismatch: int = 0
    last_date: dict[Unit, str] = field(default_factory=dict)

    def fold(self, blobs: Iterable[RawBlob]) -> list[RawBlob]:
        f = fold_consecutive(blobs)
        for k, n in f.n_folded.items():
            self.n_folded[k] = self.n_folded.get(k, 0) + n
        self.last_date.update(f.last_date)
        self.n_sha_mismatch += sum(1 for b in f.kept if sha_mismatch(b))
        return f.kept

    def metrics(self) -> dict[str, object]:
        return {"n_folded": dict(sorted(self.n_folded.items())),
                "n_sha_mismatch": self.n_sha_mismatch,
                "n_keys_stale": keys_stale(self.last_date)}


def input_fingerprint(lite: sqlite3.Connection, bs: BlobSource) -> str:
    """원장 내용 지문 — 아침 재사용(7-3) 판정 ⑤ 의 원장 축. 본문 없이 키·sha256·fetched_at 만 읽는다.

    sha256(머리줄 'RULES_VERSION·파서·eps' + (cmp_cd, ep, pkey, fetched_date, sha256, fetched_at) 전부를 원장
    PK 순서로). 행은 `ORDER BY` PK 로 받아 한 행씩 해시에 넣는다 — 전 행을 메모리에 모으지 않고, PK 가 유일해
    순서가 결정적이다. 같은 날 같은 키를 다른 원문으로 덮거나(sha256) 원문이 같아도 다시 받으면(fetched_at —
    행의 observed_date 원천) 지문이 바뀐다. 저녁 빌드가 G8 지표로 싣고, 아침에 같은 함수로 다시 계산해 비교한다.
    """
    eps = list(bs.eps)
    h = hashlib.sha256(f"{RULES_VERSION}\t{bs.parser}\t{','.join(bs.eps)}\n".encode())
    for r in lite.execute(
            f'SELECT cmp_cd, ep, pkey, fetched_date, sha256, fetched_at FROM "{bs.table}" '
            f"WHERE ep IN ({', '.join('?' * len(eps))}) ORDER BY cmp_cd, ep, pkey, fetched_date", eps):
        h.update(("\t".join(str(v) for v in r) + "\n").encode())
    return h.hexdigest()
