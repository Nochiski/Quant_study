//! backtest_engine의 결정론적 코어 (로드맵 6a).
//!
//! Python 구현(`backend/src/backtest_engine/engine/{broker,portfolio}.py`, `sizing.py`)이 진실 원천이다.
//! 여기 함수는 그 구현과 **같은 부동소수 연산 순서**로 같은 결과를 내야 하며, 동일성은
//! `backend/tests/test_core_parity.py`가 두 코어를 나란히 돌려 고정한다.
//!
//! 경계는 원시 타입만 쓴다: 종목은 `"venue:symbol"` 키 문자열, 수량은 정수 주식 수(i64),
//! 가격·현금은 f64. Decimal 비율이 필요한 자본변동 산술은 Python 어댑터가 하고 여기는
//! 결과만 적용한다.

mod buying_power;
mod callback;
mod compact_store;
mod event_queue;
mod execution;
mod feed;
mod persistent;
mod persistent_router;
mod portfolio;
mod quote;
mod session;

use pyo3::prelude::*;

#[pymodule]
fn backtest_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    callback::register(m)?;
    execution::register(m)?;
    persistent::register(m)?;
    portfolio::register(m)?;
    quote::register(m)?;
    buying_power::register(m)?;
    session::register(m)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
