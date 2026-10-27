# 기술적 분석 모듈 · 종목 조회 도구

종목·ETF·지수를 넣으면 기술적 상태를 한 장으로 보여 준다. 예측 점수가 아니라 "지금 어디쯤인가"를
보는 도구다. 지표 계산 모듈(`src/technical_analysis/`)은 같은 정의로 과거 성적 연구에도 쓴다.

## 준비

연구 DB(equity 층)가 로컬에 있어야 한다(`database/docs/LEDGER_SYNC.md` 로 받는다). 위치는 환경 변수
`TA_EQUITY_ROOT`, 없으면 `~/quant-ledger/data/equity`. 표마다 MANIFEST 의 `current_build` 판만 읽는다.

```sh
cd workspace/dongmin/technical_analysis
uv sync --group dev          # numba 가 들어간다(arm64 맥은 파이썬 3.12, .python-version)
uv run pytest -q
```

## 쓰는 법

```sh
uv run ta 삼성전자            # 웹 페이지를 만들고 브라우저로 연다
uv run ta 코스피 --png         # PNG 와 텔레그램용 짧은 글(.txt)도 만든다(크롬 필요)
uv run ta 005935 --no-open     # 코드로도 찾는다. 이름이 여럿에 걸리면 후보를 보여 준다

# 내가 고른 세 점으로 피보나치 채널(저점·저점·고점 또는 고점·고점·저점)
uv run ta channel set 코스피 2019-08-06:low 2022-09-30:low 2024-07-11:high
uv run ta channel list
uv run ta channel rm 코스피
```

결과는 `~/quant-data/technical-analysis/out/<코드>_<날짜>.html|png|txt`, 지정 채널은
`~/quant-data/technical-analysis/channels.json`(환경 변수 `TA_CHANNELS`)에 저장한다.

## 화면

- 캔들 · 거래량 · EMA 20·50 · 로그 눈금 전환 · 선 묶음 켜고 끄기
- **피봇**: 직전 극값에서 ATR(14일 평균 진폭) 3배 이상 되돌린 점. 앞뒤 2개월 이상 극값인 주요 피봇에
  "앞 N개월 · 뒤 M개월" 표시
- **피보나치 채널**: 첫 두 점이 기준선(0), 셋째 점이 폭 1. 주 세트는 폭의 **0.5배 등간격** 선(TradingView
  설정 2, 2.5, …, 13.5 와 같은 선), 보조 세트는 피보나치 비율(0.236 … 4.236). 기울기는 봉(거래일) 간격 기준
  - 지정 모드: 위 `ta channel set` 으로 저장, 열 때마다 최신 봉까지 다시 그린다
  - 자동 모드: 앞뒤 2개월 이상 극값인 피봇으로 후보를 만들고 "세 번째 점 뒤 등간격 선에서 꺾인 수 − 같은
    채널을 무작위 위치로 옮겼을 때 평균 꺾인 수"로 상위 3개. 지정 채널도 같은 기준 점수를 나란히 보인다.
    자동 채널은 지난 꺾임에 맞춰 고른 것이라 그 자체로 예측력의 근거가 아니다
- 추세선(마지막 확정 저점 2개·고점 2개)과 이탈 표시, 고전 피봇 포인트(다음 거래일·이번 주·이번 달)
- 매물대(일봉 근사: 하루 거래량을 그날 고가~저가에 고르게 나눠 쌓음, 60·120·250일)
- 오른쪽 요약: 오늘 점검표(D1~D10 양호·주의 + 과거 실측), 오늘 켜진 신호(A1~A15), 채널 안 위치,
  가까운 저항·지지 3개씩(거리 %·ATR 배수), 변동성, 수급(외국인·기관)·공매도·신용

가격은 수정주가를 마지막 봉 = 실제 종가가 되게 맞춘 값이다(원장 수정주가는 전방 조정이라 그대로 쓰면
옛 가격 기준이 된다). 거래정지일처럼 가격이 빈 날은 봉에서 뺀다.

## 점검표 실측 칸

연구 결과 성적표(`TA_SCORECARD`, 기본 `~/quant-data/research/2026-10-27-technical-analysis/results/scorecard.json`)가
있으면 지표마다 유효·무의미·반대를 붙이고, 요약을 "근거 있는 지표"와 "습관 지표"로 나눠 센다. 없으면 '미측정'.
