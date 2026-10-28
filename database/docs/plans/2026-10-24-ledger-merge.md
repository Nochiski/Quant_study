# 원장 갈래 병합 — 서버 갈래 + WISE 갈래 + main (2026-10-24)

> 브랜치 `merge/main-into-ledger`(기준 `merge/ledger-sw` = 서버 배포본 `fix/kiwoom-ka10060-volume` fc6c91eb +
> `feat/equity-wise-fields` 80102a79, 그 위에 `origin/main` 4859372f). 사용자 10-24 지시 "꼬인 것 정리 — 작업 플랜, 검증 게이트,
> 병렬 디스패치 진행", 플랜 승인 10-24(트랙 L). 새 기능 없음 — 갈래 합치기와 그 때문에 깨지는 테스트·문구만 고친다.

| 작업 | 계획 | 구현 | 검토 | 배포 | 실전 확인 | 다음 관측 |
|---|---|---|---|---|---|---|
| L1 서버 갈래·WISE 갈래 원격 백업 | 10-24 | 10-24 | 보안 검사 0건 | — | — | — |
| L2 S+W 통합(`merge/ledger-sw` d8d7b3d1, stage 2.10.0 재번호) | 10-24 | 10-24 | database 3,887 통과·0 실패 | — | — | — |
| L3 main 병합(이 브랜치, equity e1.31.0) | 10-24 | 10-25 | database 3,929 통과·0 실패 · 변경 유실 0 | | | |
| L4 서버 임시 폴더 그림자 빌드(D=10-23) | 10-24 | 10-25 | 바뀐 표 = 예상 목록, 재현 해시 동일 | — | — | — |
| L5 PR(merge commit 머지만) | 10-24 | | | — | — | — |
| L6 배포(데이터베이스 세션) | 10-24 | — | — | 목표 10-29 15:10 | | 그날 장 마감·저녁·다음 아침 |

## 배경

- `database/` 가 10-02(e8400fba, Q-4 역병합 지점)부터 두 갈래였다. 서버 배포본(S)은 main 에 없는 커밋 770개를 가졌고,
  main(M)은 서버에 없는 상목 equity 수정(#583·#589·#590·#592·#597, 753c9b83 연구 입력 사본)을 가졌다.
- WISE 필드 갈래(W, #608)는 S 와 55ca58d7(10-22)에서 갈라진 형제다. rsync 덮어쓰기 배포라 한쪽만 배포하면
  다른 쪽 수정이 빠진다.
- 판본 번호가 겹쳤다: equity e1.20.0·e1.21.0(M ≠ S), stage 2.9.0(S ≠ W). 빌드 기록의 `rules_version` 만으로는
  어느 코드인지 모른다.

## 판본

| 층 | M | S(서버) | W | 병합 뒤 | 근거 |
|---|---|---|---|---|---|
| equity | e1.21.0 | e1.29.0 | e1.30.0 | **e1.31.0** | 두 갈래 최대값보다 크다. e1.30.0 은 W 임시 빌드·문서가 쓰므로 W 몫으로 둔다 |
| stage | 2.3.0 | 2.9.0(ka10060) | 2.9.0(fetched_at_utc) | **2.10.0**(W 몫) | S 2.9.0 이 이미 운영 빌드 기록에 있다 |
| fi / model / parser | — | fi1.10.0 / mb1.7.0 / p1.5 | 같음 | 그대로 | 규칙 불변 |

- 이력 주석은 지우지 않는다. M 의 e1.20.0·e1.21.0 주석은 문구 그대로 두고 이름만 `main e1.20.0`·`main e1.21.0` 으로
  붙여 `model.py` 의 "main 계보" 절 밑(2차)에 둔다.
- 산출·화면 문자열에 박힌 "(e1.20.0)"·"(e1.21.0)"(M 계보 뜻)은 "e1.31.0(main e1.20.0, #589)" 꼴로 고친다. S 계보의
  e1.20.0 언급(fin_std 회귀 수정 판)은 그대로 둔다.
- 버전 문자열은 `==`·`!=` 로만 비교된다(크기 비교 코드 0) — "2.10.0" 이 글자 순서로 "2.9.0" 보다 앞서도 문제없다.

## 충돌 해결 방침 — 양쪽 변경을 모두 살린다

| 파일 | 방침 |
|---|---|
| `src/equity/model.py` | 값 `e1.31.0`, 이력 주석 두 계보 보존 + e1.31.0 줄 |
| `src/equity/rules_s05.py` | `CORP_EVENT.inputs` 합집합: security_span 4열(M #590) + price_daily·security(S C-04) |
| `src/equity/sql/corp_event.sql` | `dart_leg` FROM 절 네 줄: `CROSS JOIN cal_bounds b`·`CROSS JOIN _const k`(S) + `leg_ticker IS NULL` legs 조건(M) + `LEFT JOIN end_base eb`(S). `leg_ticker` 행은 자사주라 `event_type <> 'bonus'` 분기를 타 eb 를 읽지 않는다 |
| `tests/test_equity_s05_event.py` | `run_event_sql` 인자·설명 합집합 |
| `README.md`·`docs/EQUITY_HANDOFF.md`·`docs/START_HERE.md` | 판본 안내를 e1.31.0 · 두 계보의 합으로 |

M 에만 있는 상목 수정(#589·#590·#592 등)은 S 가 같은 결함을 따로 고친 적이 없어 M 쪽을 그대로 택한다.

## 자동 병합됐지만 고친 것(의미 충돌)

1. backend `tests/equity_fixture.py` `factor_table` 에 `price_only_factor` 열(1.0) — M 이 픽스처를 원장 S23 SQL 그대로
   굽는데 S e1.26.0 ⑤ 의 SQL 이 이 열을 읽는다.
2. backend #438 테스트가 main 에서 없어진 `FactorResearchService.preview` 를 불렀다 — main(a444f252)은 팩터 미리보기를
   전략 추적의 파이프라인 평가로 합쳤으므로 파이프라인 경로 단언만 남긴다(PR 에서 상목 확인).
3. 원장 대기 필드(`LEDGER_PENDING_FIELDS`) 비우기, S21 `ALL_FIELDS` 에 WISE 13필드 — W 계획 '열린 것' 3.
4. S19 row_filter 기대 집합에 WISE 13필드.
5. 산출 문구의 main 판본 번호.

## 배포 때 서버 반응(코드 확인)

- equity EG5a: 첫 판은 `rules_changed` skip(허용표에 있음). 재현성은 그림자 빌드에서 같은 입력으로 두 번 지어 본다.
- stage health C4: 배포 뒤 첫 판은 전 표 `rules_changed`. 아침 재사용 7-3 은 판본·code_rev 가 달라 거절 → WISE blob 표 전량
  재빌드.
  - (2026-10-28 정정, 전수 조사 하12) 거절은 배포 때문에 새로 생기는 것이 아니다. 10-14 저녁 체인을 끈 뒤로 아침 재사용은
    매일 그 앞 단계에서 `reuse_declined reason=current_not_evening` 으로 거절돼(`stage/reuse.py`), WISE 7표는 이미 매일
    다시 지어진다(10-23·10-24 아침 6.4~6.6분). 배포 뒤 첫 판(10-28)의 7표 합은 8.1분이었다.
- `deploy.sh` ⑦: 서버 DEPLOYED rev(fc6c91eb)가 HEAD 의 조상이어야 한다 → **merge commit 으로만 머지**(스쿼시·리베이스 금지).
- EG-C 계약은 서버 `_engine` 으로 병합판 backend facade 를 import 한다 — main 의 columnar 작업이 numpy 를 끌어들인다.
  원장 venv 의 numpy 와 계약 단계 인터프리터를 그림자 빌드에서 확인한다.

## 게이트

트랙 L G1~G7(저장소 밖 Qmain 플랜). 이 문서에는 결과만 '기록' 절에 적는다.

## 기록

- 10-24 L1: 서버 갈래·WISE 갈래를 원격에 올렸다(푸시 전 보안 검사 0건). L2: `merge/ledger-sw`(d8d7b3d1) database 3,887 통과.
- 10-25 L3: 이 브랜치. database 3,929 통과·0 실패, backend 실패 집합 = main 기준선(Rust 코어 없는 맥의 환경 실패, CI 가 최종 판정).
  세 갈래 끝 커밋이 모두 조상이고, 두 갈래가 더한 줄 중 사라진 것은 판본 번호·안내 문장·합집합으로 닫힌 줄·대기 목록·옮긴 테스트뿐이다.
- 10-25 L4: 서버 임시 폴더에서 D=10-23 확정판을 같은 입력으로 다시 지었다(운영 루트 쓰기 0).
  - 서버 배포본 코드 판은 운영 10-23 확정판을 equity 30/30 · fi 8/8 · model 12/12 해시까지 재현했다.
  - 병합 코드 판(WISE blob 7표만 같은 원장 스냅숏에서 2.10.0 으로 다시 지은 stage 위)과 견줘 바뀐 equity 표는
    `corp_event`(treasury_buy 행만)·`dataset_profile`·`factor_readiness`(E05·E06·I03 만), 새 표는 S26 3표, 나머지 27표
    (`adj_factor`·`price_adj_daily` 포함)는 같다. WISE 7표는 새 열을 빼면 행까지 같다.
  - 병합 코드 판 두 번 = 해시 같음. 계약(EG-C)은 병합 워크벤치 facade 로 통과. fi·모델 해시 같음 — scope@2.0 상위 30 같은 순서.
