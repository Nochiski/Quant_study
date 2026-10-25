"""두 tape 엔진을 같은 입력으로 돌려 비트 단위로 대조하는 하네스(columnar-tape spec 5절, T1-01).

`_corpus` 가 말뭉치를, `_engines` 가 엔진별 층(L0~L3) 결과를, `_compare` 가 차이를 낸다.
실원장 대조 진입점은 `backend/scripts/parity_tape.py` 다.
"""
