use pyo3::prelude::*;

/// Python 전략으로 제어권을 넘길 때의 불변 프레임 식별자.
///
/// 큰 portfolio/history payload는 Python `RustStrategyContext`가 token과 고정 시각을 기준으로
/// lazy 조회한다. Rust runtime은 token lifecycle의 단일 진실 원천이다.
#[pyclass(frozen)]
#[derive(Clone)]
pub(crate) struct CallbackFrame {
    #[pyo3(get)]
    pub(crate) token: u64,
    #[pyo3(get)]
    pub(crate) event_kind: String,
    #[pyo3(get)]
    pub(crate) session_index: usize,
    #[pyo3(get)]
    pub(crate) ts: String,
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<CallbackFrame>()?;
    Ok(())
}
