# Sangmok 백테스트 학습 작업물

이 폴더는 백테스트 엔진을 직접 만들기 위한 학습 작업물이다. Zipline, PyKRX,
FastAPI, Astro는 최종 목표가 아니라 엔진 설계에 필요한 입력/출력, 이벤트 흐름,
성과 지표를 관찰하기 위한 보조 도구다.

```text
implementation/  실제 실행 코드, FastAPI, Astro, Zipline/PyKRX 스크립트
result/          학습용 HTML 아티팩트와 Astro 빌드 결과
```

정본 아티팩트:

- [백테스트 엔진 설계 노트](result/html/index.html)

Astro 빌드 결과는 `result/html/app/index.html`에 생성된다. 개발 서버로 실험할 때는
`implementation` 디렉터리에서 FastAPI와 Astro를 각각 실행한다.

```bash
# 터미널 1
uv run python scripts/run_api.py

# 터미널 2
npm run dev
```

개발 앱은 `http://127.0.0.1:4321/app/`, FastAPI 문서는
`http://127.0.0.1:8000/docs`에서 연다. `result/html`을 정적 서버로 열 때는
`http://127.0.0.1:4173/index.html`에서 설계 아티팩트와 빌드 앱을 함께 볼 수 있다.
