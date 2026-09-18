"""파티션 content_hash 재계산 — 서버 `stage/build.py:_content_hash` 규칙의 로컬 재현.

서버는 tmp 경로(`v=` 세그먼트 없음)에서 `read_parquet(..., hive_partitioning=true)` 로
`count(*), bit_xor(hash(CAST(row AS VARCHAR)))` 를 뜬다. 로컬 경로에는 `v=<build>` 가 섞여 hive 를
켜면 `v` 컬럼이 붙어 값이 달라지므로, hive 를 끄고 꼬리의 `key=value` 만 컬럼으로 덧붙인다.
하이브 값은 duckdb 규약대로 url-디코딩하고 `__HIVE_DEFAULT_PARTITION__` 은 NULL 이다
(`layout.Partition.hive_columns`). 2026-09-19 실측: 서버 29표 260 파티션 전부 일치(duckdb 1.5.5).

`plan`(재사용 직전 검사)과 `verify`(hash 층위)가 같은 함수를 쓴다.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .layout import Partition

PARQUET_SUFFIX = ".parquet"
EMPTY_HASH = "0:empty"


class _HashRow(Protocol):
    def fetchone(self) -> tuple[object, ...] | None: ...


class HashConnection(Protocol):
    """duckdb 연결 중 이 모듈이 쓰는 부분(`execute(...).fetchone()`)."""

    def execute(self, query: str) -> _HashRow: ...


class PartitionHashError(RuntimeError):
    """parquet 을 읽을 수 없어 해시를 낼 수 없다(손상·절단). 호출부가 finding 또는 재사용 거부로
    바꾼다."""


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def content_hash_sql(partition_dir: Path, partition: Partition) -> str:
    """서버 `_content_hash` 와 같은 값을 내는 SQL — 하이브 컬럼은 꼬리에서 직접 덧붙인다."""
    glob = _sql_str(str(partition_dir / f"*{PARQUET_SUFFIX}"))
    extra = "".join(
        f", {'NULL' if value is None else _sql_str(value)} AS {_sql_ident(key)}"
        for key, value in partition.hive_columns()
    )
    return ("SELECT count(*), bit_xor(hash(CAST(t AS VARCHAR))) FROM "
            f"(SELECT *{extra} FROM read_parquet({glob}, hive_partitioning=false)) t")


def format_hash(n: object, h: object) -> str:
    return f"{int(str(n))}:{'0' if h is None else format(int(str(h)), 'x')}"


def compute_partition_hash(connection: HashConnection, partition_dir: Path,
                           partition: Partition) -> str:
    """파티션 디렉토리의 content_hash. parquet 이 하나도 없으면 서버처럼 `0:empty`.

    Raises:
        FileNotFoundError: 디렉토리 자체가 없다("빈 파티션" 과 구분한다).
        PartitionHashError: duckdb 가 parquet 을 읽지 못했다.
    """
    if not partition_dir.is_dir():
        raise FileNotFoundError(f"partition directory missing — dir={partition_dir}")
    if not any(partition_dir.glob(f"*{PARQUET_SUFFIX}")):
        return EMPTY_HASH
    import duckdb

    try:
        row = connection.execute(content_hash_sql(partition_dir, partition)).fetchone()
    except duckdb.Error as error:
        raise PartitionHashError(
            f"parquet unreadable — dir={partition_dir} partition={partition.path} error={error!r}"
        ) from error
    if row is None:
        raise PartitionHashError(f"content hash query returned no row — dir={partition_dir}")
    return format_hash(row[0], row[1])


ReuseCheck = Callable[[Path, Partition], bool]
"""(로컬 파티션 디렉토리, 새 빌드 파티션) → 재사용해도 되는가."""


def duckdb_reuse_check() -> ReuseCheck:
    """재사용 직전에 로컬 파티션 해시를 다시 계산해 MANIFEST 의 content_hash 와 대조한다 — 크기는
    같은데 내용이 손상된 parquet 가 다음 빌드로 전파되는 것을 여기서 끊는다. duckdb 연결은 처음
    쓸 때 한 번 연다."""
    import duckdb

    connection: HashConnection | None = None

    def check(partition_dir: Path, partition: Partition) -> bool:
        nonlocal connection
        if connection is None:
            connection = duckdb.connect()
        try:
            return compute_partition_hash(connection, partition_dir, partition) \
                == partition.content_hash
        except (FileNotFoundError, PartitionHashError):
            return False

    return check


def trusting_reuse_check(_partition_dir: Path, _partition: Partition) -> bool:
    """MANIFEST 값만 믿고 재계산하지 않는다 — 테스트·`--no-reuse-check` 용."""
    return True
