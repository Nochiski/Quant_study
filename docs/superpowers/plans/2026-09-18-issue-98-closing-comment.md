# 이슈 #98 종료 댓글 초안

> 게시 담당은 팀 리드다. 이 파일은 초안이며 아래 `---` 사이가 댓글 본문이다.
> PR 11(`docs/rust-loop-final-gates`)이 머지되면 PR 번호를 채워 넣는다.

---

## 리뷰 후속 스택 완료 — 최종 게이트 판정

2026-09-18 리뷰(Rust·Python·성능 실측 3인)에서 나온 결함·SoT 중복·죽은 코드·성능 여지를 PR 11개로
해소했다. 계획 문서는
[`docs/superpowers/plans/2026-09-18-rust-loop-review-followup.md`](../../../docs/superpowers/plans/2026-09-18-rust-loop-review-followup.md)이고
수치의 정본은 스펙
[`2026-09-01-persistent-rust-engine-implementation.md`](../../../docs/superpowers/specs/2026-09-01-persistent-rust-engine-implementation.md)의
"2026-09-18 최종 판정 (리뷰 후속 PR 1~11)" 절이다.

### PR 스택

앞 PR이 뒤 PR의 base다.

| PR | 브랜치 | 내용 |
|---|---|---|
| #123 | `fix/bench-honest-boundary` | 벤치 측정 경계 교정(DEFECT-301) + 워크벤치 구간 측정(Phase 3-2) |
| #124 | `fix/driver-failed-lifecycle` | 세션 도메인 오류 후 `Failed` 고정, 구조화 라우팅 예외, parity 공백 3건 |
| #127 | `refactor/drop-dead-persistent-api` | `PersistentPortfolio`·죽은 pymethod 25개·규칙 위반 제거 |
| #128 | `refactor/rust-owns-wire-constants` | Rust가 wire 상수·큐 세션을 소유, no_bar 포맷 정본 단일화 |
| #129 | `refactor/python-sot-context-tape-marker` | `RecordKind` 명시 코드(DEFECT-302), 컨텍스트 base, tape 명시 옵트인(DEFECT-303) |
| #134 | `perf/materialize-by-kind` | 결과 조회를 kind 청크 FFI로, 넘긴 payload 즉시 해제 |
| #136 | `perf/workbench-result-columnar` | 워크벤치가 엔진 columnar 결과 테이블을 직접 읽는다 |
| #137 | `perf/rust-hot-loop` | 라우터·원장 O(1) 인덱스, 심볼 표 1회 구축, clone 제거 |
| #138 | `perf/record-and-queue-memory` | 레코드·큐 payload `Box`화, 큐 arena free-list, 행 조회표 밀도 선택 |
| #141 | `perf/feed-columnar` | `DataFeed.from_columns` — 어댑터가 `Bar` 객체를 만들지 않는다 |
| (PR 11) | `docs/rust-loop-final-gates` | 최종 재측정·문서·이 댓글 |

### 최종 게이트 판정

측정: `--warmup 1 --repeat 5`, Rust `--release`, 실행 직전 CPU 부하 15~39%, 산출물
`backend/benchmarks/baseline/rust-loop-*.json` 16개.

| 게이트 | 최소 | 목표 | PR 1 기준선 | 최종 | 판정 |
|---|---|---|---|---|---|
| 100종목 TargetTape 경로 | 3배 | 5배 | 2.04배 | 엔진 2.41배 / 워크벤치 e2e 3.23배 | **미달** (엔진 경계) |
| 100종목 Python 전략 경로 | 2배 | 3배 | 2.16배 | 2.48배 | **통과** (목표 미달) |
| 300종목 | 2배 | 3배 | callback 1.93배 · tape 1.82배 | callback 2.35배 · tape 2.43배 | **통과** (목표 미달) |
| 실제 4종목 fixture | 회귀 10% 이내 | 현행 이상 | callback 2.14배 · tape 2.77배 | callback 2.41배 · tape 2.59배 | **통과** |
| 세션당 FFI | 0회 | — | 0회 | 0회 | **통과** |
| Peak RSS | 1.25배 이하 | — | callback 1.23배 · tape 1.28배 | callback 1.15배 · tape 1.19배 | **통과** |
| 결과 패리티 | 100% | — | 통과 | 통과 | **통과** |

PR 1 기준선은 개선 전 값이 아니라 **정직한 측정 경계로 처음 잰 값**이다. 그 이전 표(2026-09-17의
4.22~4.73배)는 lazy한 Rust 결과의 조회 비용을 타이머 밖에 둬서 무효다.

### 엔진 실측 (`scripts/bench_universe.py`, 중앙값)

| 워크로드 | python total | rust run | rust 조회 | rust total | 배수 | `run()`만 배수 |
|---|---|---|---|---|---|---|
| 100종목 synthetic · callback | 1.696초 | 0.268초 | 0.407초 | 0.685초 | 2.48배 | 6.32배 |
| 100종목 synthetic · tape | 1.650초 | 0.241초 | 0.430초 | 0.685초 | 2.41배 | 6.83배 |
| 300종목 synthetic · callback | 4.723초 | 0.837초 | 1.190초 | 2.012초 | 2.35배 | 5.64배 |
| 300종목 synthetic · tape | 4.471초 | 0.721초 | 1.106초 | 1.841초 | 2.43배 | 6.20배 |
| 실제 4종목 fixture · callback | 0.115초 | 0.030초 | 0.016초 | 0.048초 | 2.41배 | 3.84배 |
| 실제 4종목 fixture · tape | 0.122초 | 0.030초 | 0.017초 | 0.047초 | 2.59배 | 4.09배 |

세 코어의 signature는 워크로드마다 동일하다 (100종목 orders 22,243 / fills 22,155 / equity
2,321,985,874, 300종목 52,344 / 52,121 / 2,402,131,747, 4종목 982 / 978 / 2,739,581,588).

`run()`만 보면 5.6~6.8배로 이슈가 예상한 "5배 이상"에 들어간다. total 배수의 상한은 결과
조회이고, 그 대부분은 FFI가 아니라 Python 공개 객체 생성이다 — 100종목 스냅샷 1,225개가
종목마다 `Position`을 만들어 123,625개가 된다. 이 계약은 `BacktestResult` 불변 계약 안에 있다.

### 워크벤치 e2e (`scripts/bench_workbench_adapter.py --instruments 100 --repeat 5`)

| 구간 | python | rust |
|---|---|---|
| `dataset_to_engine_inputs` | 0.0002초 | 0.0003초 |
| `strategy_and_feed_build` | 0.1437초 | 0.1373초 |
| `engine.run` | 2.3242초 | 0.2238초 |
| `result_tables` | 0.1974초 | 0.0298초 |
| `artifacts` | 0.5549초 | 0.4522초 |
| `analysis_points` | 0.0013초 | 0.0015초 |
| `compute_analytics` | 0.0163초 | 0.0182초 |
| `manifest` | 0.0645초 | 0.0325초 |
| **e2e total** | **3.332초** | **0.911초** |

코어 격리 실행 기준 **3.66배**, 한 프로세스에서 번갈아 돌린 `--core all` 실행은 3.223초 /
0.998초로 **3.23배**다 (PR 1 기준선 1.21배). 구간별 수치의 정본은 격리 실행이다 — `--core all`에서는
자동 순환 GC가 rust `compute_analytics`에 붙어 0.018초가 0.168초로 찍힌다. 어댑터 격리 실행
peak RSS는 python 318.6MiB / rust 329.4MiB로 1.03배다.

### Peak RSS와 그 지표의 한계

| 코어 격리 Peak RSS (100종목, `--core <one>` 단독) | python | rust | 배수 |
|---|---|---|---|
| callback | 169.8MiB | 194.8MiB | 1.15배 (PR 1 기준선 1.23배) |
| tape | 170.2MiB | 202.1MiB | 1.19배 (PR 1 기준선 1.28배) |

`scripts/bench_universe.py::peak_rss_bytes()`는 Windows `PeakWorkingSetSize`라 run 도중 잠깐
커밋됐다 풀리는 버퍼를 못 잡는다. PR #138의 큐 arena가 그 예다 — 300종목 상주가 30.4MiB에서 약
20KiB로 줄었는데 working set peak은 움직이지 않았고 `PeakPagefileUsage`로만 −5.16MiB가 보였다.
이 실행의 working set peak이 큐가 가장 큰 순간이 아니라 결과 조회 구간에서 정해지기 때문이다.
**메모리 항목을 이 지표 하나로 판정하면 안 된다.**

### 희소 유니버스 (`--density`, PR 11에서 추가)

기존 벤치는 100·300종목 모두 밀도 100%라 PR #138이 넣은 `RowIndex::Sparse` 경로가 한 번도 돌지
않았다. `--density`가 종목마다 길이 `round(sessions × density)`의 연속 상장 구간만 남겨 격자를
비운다.

| 워크로드 (300종목 tape) | 밀도 | 행 조회표 | bars | python total | rust run | rust total | 배수 |
|---|---|---|---|---|---|---|---|
| `--density 0.15` | 15.03% | Sparse | 55,500 | 1.049초 | 0.200초 | 0.433초 | 2.42배 |
| `--density 0.2` | 19.98% | Sparse | 73,800 | 1.368초 | 0.245초 | 0.581초 | 2.36배 |
| `--density 0.2005` | 20.06% | Dense | 74,100 | 1.409초 | 0.227초 | 0.532초 | 2.65배 |

뒤 두 줄이 `row_at` 해시 비용의 A/B다. 상장 구간이 246 / 247 세션 차이라 bar 수가 0.4%만 다른데
표현만 Sparse / Dense로 갈린다. **Sparse가 rust `run()`에서 8.1% 느리다** (bar 수로 정규화하면
8.5%). 조회표 크기는 이 경계에서 양쪽 모두 약 1.4MiB로 같고, Sparse가 값을 하는 구간은 Dense 표가
유니버스 × 기간에 비례해 커지는 누적 유니버스다 (3,000종목 × 5,000세션이면 Dense는 60MiB 고정).
세 코어 결과 signature는 희소 실행에서도 동일하다.

밀도 15%에서는 주문 23,315건 중 체결이 1,991건뿐이다. `REPLACE` 목표가 상폐된 보유 종목을
매도하려는데 그 세션에 bar가 없어 day 주문이 만료되기 때문이다. 밀도 100% 워크로드와 체결 비중이
다르므로 두 줄을 서로 빼서 읽으면 안 된다 — Sparse / Dense 비교는 위 A/B 짝 안에서만 유효하다.

### 남은 결정 — Phase 3-4 Python 코어 삭제 범위

이슈 원문 "결정 필요" 2번이 아직 미결이다. 기본안은 **"테스트 전용 reference로 유지"**다.

- **근거**: `core="python"`은 parity oracle이다. `tests/test_core_parity.py`가 python 코어의
  trace(`seq, ts, kind, payload`)·ID·float 연산 순서·오류 메시지를 정답으로 놓고 rust 코어를
  byte 단위로 대조한다. 삭제하면 이 정답이 사라지고, rust 코어의 회귀는 golden 파일이 스스로를
  검증하는 형태로만 남는다.
- **비용**: 이번 스택의 PR #136에서 python 코어 e2e가 약 10% 느려졌다(결과 테이블 생성이 python
  코어에는 순수 추가 패스). 프로덕션 경로가 아니므로 참조 코어가 치르는 비용으로 받아들였는데,
  "테스트 전용"이 명시되면 이런 판단이 매번 자명해진다.
- **제안**: `core="python"`을 공개 API에서 "참조 구현, 성능 보장 없음"으로 문서화하고 삭제하지
  않는다. `core="rust_legacy"`는 별개 항목이며 이미 `DeprecationWarning`을 낸다.

### 후속 항목

1. **TargetTape 엔진 경계 최소 3배** — 현재 2.41배. 남은 거리는 결과 조회의 Python 공개 객체
   생성이고, 줄이려면 `BacktestResult` 계약을 건드려야 한다. 워크벤치 경계에서는 이미 3.23~3.66배다.
2. **tape 경량 프레임** (PR #137에서 구현 후 되돌림) — `make_native_frame`은 fill·order_update
   알림을 선언한 tape 전략에서만 값을 한다. 현재 벤치는 `MARKET`만 선언해 NOTIFY 분기가 돌지
   않아 측정으로 유지를 정당화할 수 없었다. 알림 선언 tape 워크로드를 재는 벤치 옵션이 생기면
   다시 올린다.
3. **코어 간 instrument key 충돌 거부 통일** — 같은 key가 다른 symbol로 두 번 등록되면 persistent
   경로는 적재에서 거부하는데 python 코어는 완주한다. 두 코어의 거부 시점·메시지를 맞춰야 한다.
4. **#135** — 수량 문자열이 `7E+2`처럼 지수 표기로 나가는 건.
5. **라우터 `instrument_not_snapshot` 메시지 순서** — `available` 목록이 Rust에서는 `HashMap`
   순서(비결정)이고 python(`types/market.py::MarketSnapshot.bar`)은 feed 순서 + `ts=` 접두를
   담는다. byte 동일이 아니고 이를 고정하는 테스트도 없다. PR #138 이전부터 그랬다.
6. **`BuyingPower` key clone** — `#[pyclass]`라 수명을 못 갖고 `HashMap<String, _>` 둘을 key마다
   채운다. 줄이려면 두 맵 병합과 `checkpoint`/`restore` 모양 변경이 필요하다.
7. **`engine/store.py` → `engine/tape.py` import 방향** — `no_bar_reason`의 정본을 한 곳으로 모은
   결과지만 store가 tape를 import하는 방향이 생겼다. 사유 포맷을 `types/` 쪽으로 내리는 안을 검토.
8. **희소 유니버스 Sparse 경로** — 위 A/B에서 `run()` +8.1%를 확인했다. 현재 선택 규칙(밀도 20%)이
   맞는 경계인지는 실제 누적 유니버스 원장으로 다시 봐야 한다 (기존 백로그 "서로 다른 실제 종목
   100/300개를 포함한 외부 원장으로 성능 게이트 재검증"과 같은 항목).

### 종료 제안

게이트 7개 중 6개가 통과했고 미달은 **TargetTape 엔진 경계 최소 3배** 하나다. 이 이슈의 목표
("실행 루프를 Rust가 끝까지 소유, 세션당 FFI 0회, TargetTape 콜백 0회")는 전부 달성했고, 남은
한 칸은 실행 루프가 아니라 결과 조회 계약의 문제라 이 이슈의 범위 밖이다.

**제안**: 이 이슈는 종료하고, 위 후속 항목 1(TargetTape 엔진 경계)과 2~8을 별도 이슈로 연다.
Phase 3-4(Python 코어 삭제 범위)는 이슈 원문의 결정 항목이므로 그 결론을 이 댓글에 대한 회신으로
확정한 뒤 종료한다.

---
