"""어댑터(adapter): 포트(`backtest_engine.ports`)를 구체 데이터 소스에 연결한다.

파일 형식·벤더 SDK·DB 드라이버 의존성은 이 패키지 바깥으로 새지 않는다.
새 채널(외부 DB 등)을 붙일 때는 여기에 모듈을 추가하고 `BarSource`를 구현한다.
"""
