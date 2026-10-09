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
mod driver;
mod event_queue;
mod execution;
mod feed;
mod persistent;
mod persistent_router;
mod portfolio;
mod quote;
mod records;
mod session;
mod tape;

use pyo3::prelude::*;
use pyo3::types::PyDict;

pyo3::create_exception!(
    backtest_core,
    RouteErrorException,
    pyo3::exceptions::PyValueError,
    "결정 라우팅 오류. args=(code, message)이며 Python 어댑터가 엔진 예외로 바꾼다."
);

#[pymodule]
fn backtest_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add(
        "RouteErrorException",
        m.py().get_type::<RouteErrorException>(),
    )?;
    callback::register(m)?;
    execution::register(m)?;
    persistent::register(m)?;
    portfolio::register(m)?;
    quote::register(m)?;
    buying_power::register(m)?;
    session::register(m)?;
    m.add(
        "RECORD_KIND_CODES",
        wire_constants(m.py(), &records::RECORD_KIND_NAMES)?,
    )?;
    m.add(
        "EVENT_PRIORITIES",
        wire_constants(m.py(), &driver::EVENT_PRIORITY_NAMES)?,
    )?;
    m.add(
        "ROW_INDEX_BYTES",
        wire_constants(m.py(), &feed::ROW_INDEX_BYTE_NAMES)?,
    )?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}

/// (name, value) 목록을 Python dict로 바꾼다. Rust 상수를 Python 정본과 대조하거나 Python이
/// 같은 규칙을 재현할 수 있게 모듈 상수로 노출하는 용도다 — 실행 경로는 이 dict를 읽지 않는다.
fn wire_constants<'py, T>(py: Python<'py>, names: &[(&str, T)]) -> PyResult<Bound<'py, PyDict>>
where
    T: Copy + IntoPyObject<'py>,
{
    let mapping = PyDict::new(py);
    for (name, value) in names {
        mapping.set_item(name, *value)?;
    }
    Ok(mapping)
}
