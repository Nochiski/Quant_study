"""연속 평가 저장소는 자체 application 포트와 공용 SQLite 수명 규칙에 의존한다."""

DEPENDS_ON: tuple[str, ...] = (
    "application.continuous_account",
    "adapters.outbound.sqlite_store",
    "domain.canonical",
)
