use crate::driver::NotifyPayload;
use crate::records::{to_object, OrderWire, SnapshotWire};
use pyo3::prelude::*;

/// Python 전략으로 제어권을 넘길 때의 불변 프레임.
///
/// 이벤트·포트폴리오·대기 주문은 콜백 시점 값으로 고정된 Rust wire이며, Python getter가 읽을
/// 때만 tuple로 변환한다. Rust runtime은 token lifecycle의 단일 진실 원천이다.
#[pyclass(frozen)]
#[derive(Clone, Debug)]
pub(crate) struct CallbackFrame {
    #[pyo3(get)]
    pub(crate) token: u64,
    #[pyo3(get)]
    pub(crate) event_kind: String,
    #[pyo3(get)]
    pub(crate) session_index: usize,
    #[pyo3(get)]
    pub(crate) ts: String,
    /// `None`이면 market 콜백. 그 외에는 requirements().events에 선언된 알림 payload.
    pub(crate) event: Option<NotifyPayload>,
    pub(crate) snapshot: SnapshotWire,
    pub(crate) open_orders: Vec<(OrderWire, i64)>,
}

#[pymethods]
impl CallbackFrame {
    /// 알림 이벤트 payload: FillWire | (order_id, status, detail) | corporate_action index | None.
    #[getter]
    fn event(&self, py: Python<'_>) -> PyResult<PyObject> {
        match &self.event {
            Some(payload) => payload.to_py(py),
            None => Ok(py.None()),
        }
    }

    /// 콜백 시점 포트폴리오: `(cash, [(instrument_id, qty, avg, mark, mv, upnl)], equity, gross)`.
    #[getter]
    fn snapshot(&self, py: Python<'_>) -> PyResult<PyObject> {
        self.snapshot.to_py(py)
    }

    /// 콜백 시점 대기 주문: `[(OrderWire, remaining), ...]`.
    #[getter]
    fn open_orders(&self, py: Python<'_>) -> PyResult<PyObject> {
        let rows = self
            .open_orders
            .iter()
            .map(|(order, remaining)| Ok((order.to_py(py)?, *remaining)))
            .collect::<PyResult<Vec<_>>>()?;
        to_object(py, rows)
    }
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<CallbackFrame>()?;
    Ok(())
}
