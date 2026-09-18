//! Persistent 실행의 append-only 레코드 스토어 (Rust-native payload).
//!
//! Python `EventStore`와 같은 순서(`seq`)로 모든 record를 담는다. payload는 원시 wire 값이며
//! 공개 Event 객체는 Python이 결과 조회 시점에 lazy materialize한다. Python 객체가 필요한
//! payload(DECISION의 결정 객체, CORPORATE_ACTION의 입력 사건)는 id/index만 담고 Python side
//! table이 실체를 보관한다.

use pyo3::exceptions::PyIndexError;
use pyo3::prelude::*;
use pyo3::{BoundObject, IntoPyObject};

/// record kind 코드는 Python `RecordKind` enum 순서와 같다.
pub(crate) const KIND_MARKET: u8 = 0;
pub(crate) const KIND_DECISION: u8 = 1;
pub(crate) const KIND_ORDER: u8 = 2;
pub(crate) const KIND_ORDER_UPDATE: u8 = 3;
pub(crate) const KIND_FILL: u8 = 4;
pub(crate) const KIND_SNAPSHOT: u8 = 5;
pub(crate) const KIND_CORPORATE_ACTION: u8 = 6;
pub(crate) const KIND_CORPORATE_ACTION_APPLIED: u8 = 7;
pub(crate) const KIND_COST: u8 = 8;

/// `(seq, session_index, kind)` — Python `PersistentEventStore`가 소비하는 레코드 인덱스 행.
/// payload는 `record_payload(seq)`로 필요할 때만 변환한다 — 배치 전체를 tuple로 복제하면 Rust
/// wire·Python tuple·공개 객체가 동시에 살아 peak RSS가 커진다.
pub(crate) type RecordIndexWire = (u64, usize, u8);

pub(crate) fn to_object<'py, T>(py: Python<'py>, value: T) -> PyResult<PyObject>
where
    T: IntoPyObject<'py>,
    T::Error: Into<PyErr>,
{
    Ok(value
        .into_pyobject(py)
        .map_err(Into::into)?
        .into_bound()
        .into_any()
        .unbind())
}

/// 주문 wire. Python `OrderEvent`의 원시 필드 + 결정 추적용 (decision_id, action_index, leg_index).
#[derive(Clone, Debug)]
pub(crate) struct OrderWire {
    pub(crate) order_id: String,
    pub(crate) decision_id: String,
    pub(crate) instrument_id: u32,
    pub(crate) quantity: i64,
    pub(crate) side: String,
    pub(crate) order_type: String,
    pub(crate) limit_text: Option<String>,
    pub(crate) stop_text: Option<String>,
    pub(crate) tif: String,
    pub(crate) group_id: Option<String>,
    pub(crate) action_index: usize,
    pub(crate) leg_index: Option<usize>,
}

impl OrderWire {
    pub(crate) fn to_py(&self, py: Python<'_>) -> PyResult<PyObject> {
        to_object(
            py,
            (
                self.order_id.as_str(),
                self.decision_id.as_str(),
                self.instrument_id,
                self.quantity,
                self.side.as_str(),
                self.order_type.as_str(),
                self.limit_text.as_deref(),
                self.stop_text.as_deref(),
                self.tif.as_str(),
                self.group_id.as_deref(),
                self.action_index,
                self.leg_index,
            ),
        )
    }
}

/// 체결 wire. Python `FillEvent`의 원시 필드.
#[derive(Clone, Debug)]
pub(crate) struct FillWire {
    pub(crate) fill_id: String,
    pub(crate) order_id: String,
    pub(crate) instrument_id: u32,
    pub(crate) quantity: i64,
    pub(crate) side: String,
    pub(crate) price: f64,
    pub(crate) fee: f64,
    pub(crate) slippage_per_share: f64,
}

impl FillWire {
    pub(crate) fn to_py(&self, py: Python<'_>) -> PyResult<PyObject> {
        to_object(
            py,
            (
                self.fill_id.as_str(),
                self.order_id.as_str(),
                self.instrument_id,
                self.quantity,
                self.side.as_str(),
                self.price,
                self.fee,
                self.slippage_per_share,
            ),
        )
    }
}

/// 스냅샷 wire: `(cash, [(instrument_id, qty, avg, mark, market_value, unrealized)], equity, gross)`.
#[derive(Clone, Debug)]
pub(crate) struct SnapshotWire {
    pub(crate) cash: f64,
    pub(crate) rows: Vec<(u32, i64, f64, f64, f64, f64)>,
    pub(crate) equity: f64,
    pub(crate) gross_exposure: f64,
}

impl SnapshotWire {
    pub(crate) fn to_py(&self, py: Python<'_>) -> PyResult<PyObject> {
        to_object(
            py,
            (
                self.cash,
                self.rows.clone(),
                self.equity,
                self.gross_exposure,
            ),
        )
    }
}

/// Rust가 스스로 만든 결정(선언형 tape)의 재구성 정보. Python 결정 객체가 없을 때만 채운다.
#[derive(Clone, Debug)]
pub(crate) struct NativeDecision {
    /// 결정을 만든 tape 프레임의 세션 index. None이면 idle(NoAction).
    pub(crate) frame_session: Option<usize>,
    /// 유지된 목표: `(instrument_id, is_weight, weight, quantity)`. 결정당 종목 수만큼 쌓이므로
    /// 문자열 대신 bool로 둔다.
    pub(crate) kept: Vec<(u32, bool, f64, i64)>,
    pub(crate) no_bar: Vec<String>,
    pub(crate) reason: String,
}

impl NativeDecision {
    fn to_py(&self, py: Python<'_>) -> PyResult<PyObject> {
        to_object(
            py,
            (
                self.frame_session,
                self.kept.clone(),
                self.no_bar.clone(),
                self.reason.as_str(),
            ),
        )
    }
}

#[derive(Clone, Debug)]
pub(crate) enum RecordPayload {
    Market,
    Decision {
        decision_id: String,
        native: Option<NativeDecision>,
    },
    Order(OrderWire),
    OrderUpdate {
        order_id: String,
        status: String,
        detail: Option<String>,
    },
    Fill(FillWire),
    Snapshot(SnapshotWire),
    CorporateAction(usize),
    CorporateActionApplied {
        corporate_action: usize,
        old_quantity: i64,
        new_quantity: i64,
        old_average_price: f64,
        new_average_price: f64,
        cash_paid: f64,
    },
    Cost {
        kind: String,
        instrument_id: Option<u32>,
        amount: f64,
    },
}

impl RecordPayload {
    pub(crate) fn kind(&self) -> u8 {
        match self {
            RecordPayload::Market => KIND_MARKET,
            RecordPayload::Decision { .. } => KIND_DECISION,
            RecordPayload::Order(_) => KIND_ORDER,
            RecordPayload::OrderUpdate { .. } => KIND_ORDER_UPDATE,
            RecordPayload::Fill(_) => KIND_FILL,
            RecordPayload::Snapshot(_) => KIND_SNAPSHOT,
            RecordPayload::CorporateAction(_) => KIND_CORPORATE_ACTION,
            RecordPayload::CorporateActionApplied { .. } => KIND_CORPORATE_ACTION_APPLIED,
            RecordPayload::Cost { .. } => KIND_COST,
        }
    }

    pub(crate) fn to_py(&self, py: Python<'_>) -> PyResult<PyObject> {
        match self {
            RecordPayload::Market => Ok(py.None()),
            RecordPayload::Decision {
                decision_id,
                native,
            } => {
                let native = match native {
                    Some(native) => native.to_py(py)?,
                    None => py.None(),
                };
                to_object(py, (decision_id.as_str(), native))
            }
            RecordPayload::Order(order) => order.to_py(py),
            RecordPayload::OrderUpdate {
                order_id,
                status,
                detail,
            } => to_object(py, (order_id.as_str(), status.as_str(), detail.as_deref())),
            RecordPayload::Fill(fill) => fill.to_py(py),
            RecordPayload::Snapshot(snapshot) => snapshot.to_py(py),
            RecordPayload::CorporateAction(index) => to_object(py, *index),
            RecordPayload::CorporateActionApplied {
                corporate_action,
                old_quantity,
                new_quantity,
                old_average_price,
                new_average_price,
                cash_paid,
            } => to_object(
                py,
                (
                    *corporate_action,
                    *old_quantity,
                    *new_quantity,
                    *old_average_price,
                    *new_average_price,
                    *cash_paid,
                ),
            ),
            RecordPayload::Cost {
                kind,
                instrument_id,
                amount,
            } => to_object(py, (kind.as_str(), *instrument_id, *amount)),
        }
    }
}

#[derive(Clone, Debug)]
pub(crate) struct NativeRecord {
    pub(crate) session_index: usize,
    pub(crate) payload: RecordPayload,
}

#[derive(Default)]
pub(crate) struct RecordStore {
    records: Vec<NativeRecord>,
    finished: bool,
}

impl RecordStore {
    pub(crate) fn append(&mut self, session_index: usize, payload: RecordPayload) -> PyResult<()> {
        if self.finished {
            return Err(PyIndexError::new_err(
                "cannot append to finished persistent record store",
            ));
        }
        self.records.push(NativeRecord {
            session_index,
            payload,
        });
        Ok(())
    }

    pub(crate) fn finish(&mut self) -> PyResult<()> {
        if self.finished {
            return Err(PyIndexError::new_err(
                "persistent record store finish called more than once",
            ));
        }
        self.finished = true;
        Ok(())
    }

    #[cfg(test)]
    pub(crate) fn records(&self) -> &[NativeRecord] {
        &self.records
    }

    pub(crate) fn index(&self) -> Vec<RecordIndexWire> {
        self.records
            .iter()
            .enumerate()
            .map(|(seq, record)| (seq as u64, record.session_index, record.payload.kind()))
            .collect()
    }

    pub(crate) fn payload(&self, py: Python<'_>, seq: usize) -> PyResult<PyObject> {
        let record = self.records.get(seq).ok_or_else(|| {
            PyIndexError::new_err(format!(
                "record seq out of range — seq={seq} records={}",
                self.records.len()
            ))
        })?;
        record.payload.to_py(py)
    }

    /// SNAPSHOT record 순서의 equity — metrics 입력.
    pub(crate) fn equity_series(&self) -> Vec<f64> {
        self.records
            .iter()
            .filter_map(|record| match &record.payload {
                RecordPayload::Snapshot(snapshot) => Some(snapshot.equity),
                _ => None,
            })
            .collect()
    }

    /// FILL record 순서로 `quantity × price`를 누산한다 (Python `traded_notional`과 같은 결합 순서).
    pub(crate) fn traded_notional(&self) -> f64 {
        let mut total = 0.0_f64;
        for record in &self.records {
            if let RecordPayload::Fill(fill) = &record.payload {
                total += fill.quantity as f64 * fill.price;
            }
        }
        total
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fill(quantity: i64, price: f64) -> RecordPayload {
        RecordPayload::Fill(FillWire {
            fill_id: "F-000001".into(),
            order_id: "O-000001".into(),
            instrument_id: 0,
            quantity,
            side: "buy".into(),
            price,
            fee: 0.0,
            slippage_per_share: 0.0,
        })
    }

    fn snapshot(equity: f64) -> RecordPayload {
        RecordPayload::Snapshot(SnapshotWire {
            cash: equity,
            rows: Vec::new(),
            equity,
            gross_exposure: 0.0,
        })
    }

    #[test]
    fn append_keeps_order_and_rejects_after_finish() {
        let mut store = RecordStore::default();
        store.append(0, RecordPayload::Market).unwrap();
        store.append(0, snapshot(10.0)).unwrap();
        store.finish().unwrap();
        assert!(store.append(1, RecordPayload::Market).is_err());
        assert!(store.finish().is_err());
        let kinds: Vec<u8> = store.records().iter().map(|r| r.payload.kind()).collect();
        assert_eq!(kinds, vec![KIND_MARKET, KIND_SNAPSHOT]);
    }

    #[test]
    fn equity_series_and_traded_notional_follow_record_order() {
        let mut store = RecordStore::default();
        store.append(0, snapshot(1.0)).unwrap();
        store.append(0, fill(3, 10.0)).unwrap();
        store.append(1, fill(2, 0.1)).unwrap();
        store.append(1, snapshot(2.0)).unwrap();
        assert_eq!(store.equity_series(), vec![1.0, 2.0]);
        assert_eq!(store.traded_notional(), 3.0 * 10.0 + 2.0 * 0.1);
    }
}
