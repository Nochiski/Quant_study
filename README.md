# Quant_study

퀀트 스터디 저장소. 데이터 수집·가공부터 백테스팅까지 각자 실습하고, 쓸 만한 코드는 공용으로 올려 함께 쓴다.

## 디렉토리 구조

```
workspace/
  dongmin/     # 개인 작업 공간
  sangmok/     # 개인 작업 공간
.claude/rules/ # 코딩 규칙 (공용)
```

`shared/` 는 아직 없다. 두 사람이 같은 코드를 각자 짜고 있다는 게 확인되면 그때 만들어서 옮긴다. 미리 만들어 두지 않는다.

## 작업 규칙

1. **남의 `workspace/` 폴더는 건드리지 않는다.** 개인 공간 안에서는 구조도 스타일도 자유. 이것만 지키면 충돌이 날 일이 없다.
2. **공용 영역 변경은 상의하거나 PR 로.** `.gitignore`, `.claude/rules/`, `README.md`, 앞으로 생길 `shared/` 와 `pyproject.toml` 이 해당된다.
3. **데이터 파일은 커밋하지 않는다.** 시세 CSV·parquet 등은 `.gitignore` 에서 막아 두었다. 저장소에는 **데이터를 만들어 내는 스크립트**를 넣고, 데이터는 각자 로컬에서 재현한다.

데이터를 둘 곳이 필요하면 `workspace/<이름>/data/` 를 쓰면 된다 — `**/data/` 규칙으로 이미 git 에서 제외된다. 손으로 계산할 수 있는 소형 테스트 픽스처만 `tests/fixtures/` 아래 CSV 로 예외 허용.

## 코딩 규칙

`.claude/rules/` 에 정리되어 있다.

| 파일 | 범위 | 내용 |
|---|---|---|
| `code-style.md` | `**/*.py` | 기존 헬퍼 재사용, 기능/정리 커밋 분리, ruff·pyright 게이트, 네이밍 |
| `python.md` | `**/*.py` | 성공/실패는 튜플 대신 Result 값 타입으로 |
| `error-messages.md` | `**/*.py` | 예외·로그에 재현 가능한 컨텍스트 포함 |
| `testing.md` | `tests/`, `scripts/` | 산출물 파일 존재/내용을 단언하는 테스트 금지 |
| `pr-review.md` | 전체 | PR 본문 양식, 결함 보고 4요소 |
