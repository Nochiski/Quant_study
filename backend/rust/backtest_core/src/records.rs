//! Persistent 실행의 append-only 레코드 스토어 (Rust-native payload).
//!
//! Python `EventStore`와 같은 순서(`seq`)로 모든 record를 담는다. payload는 원시 wire 값이며
//! 공개 Event 객체는 Python이 결과 조회 시점에 lazy materialize한다. Python 객체가 필요한
//! payload(DECISION의 결정 객체, CORPORATE_ACTION의 입력 사건)는 id/index만 담고 Python side
//! table이 실체를 보관한다.

use pyo3::exceptions::{PyIndexError, PyRuntimeError, PyValueError};
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

/// wire 상수 대조용 (name, code) 목록. name은 Python `RecordKind`의 value 문자열이다.
/// `lib.rs`가 모듈 상수로 노출하고 `tests/test_core_parity.py`가 Python 정본과 대조한다.
pub(crate) const RECORD_KIND_NAMES: [(&str, u8); 9] = [
    ("market", KIND_MARKET),
    ("decision", KIND_DECISION),
    ("order", KIND_ORDER),
    ("order_update", KIND_ORDER_UPDATE),
    ("fill", KIND_FILL),
    ("snapshot", KIND_SNAPSHOT),
    ("corporate_action", KIND_CORPORATE_ACTION),
    ("corporate_action_applied", KIND_CORPORATE_ACTION_APPLIED),
    ("cost", KIND_COST),
];

/// 오류 메시지용 kind wire 이름. 코드만으로는 어떤 레코드인지 읽히지 않는다.
fn kind_name(code: u8) -> &'static str {
    RECORD_KIND_NAMES
        .iter()
        .find(|(_, value)| *value == code)
        .map(|(name, _)| *name)
        .unwrap_or("unknown")
}

/// 이미 해제한 payload를 다시 읽으려 할 때의 오류. 어떤 조회가 어느 레코드에서 막혔는지 남긴다.
fn released_error(seq: u64, kind: u8, operation: &str) -> PyErr {
    PyRuntimeError::new_err(format!(
        "record payload was already released — operation={operation} seq={seq} kind={}({kind})",
        kind_name(kind)
    ))
}

/// `(seq, session_index, kind)` — Python `PersistentEventStore`가 소비하는 레코드 인덱스 행.
/// payload는 이 행과 따로 나른다. 정본 경로는 `drain_payloads(kind, limit)`로, 한 kind를 청크씩
/// 넘기면서 넘긴 자리를 바로 해제한다. 종료 전 partial trace만 해제하지 않는
/// `record_payloads(kind)`로 읽는다. 인덱스와 payload를 한 번에 다 나르면 Rust wire·Python
/// tuple·공개 객체가 동시에 살아 peak RSS가 셋의 합이 된다.
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

/// 주문 상태 변경 wire. Python `OrderUpdateEvent`의 원시 필드.
#[derive(Clone, Debug)]
pub(crate) struct OrderUpdateWire {
    pub(crate) order_id: String,
    pub(crate) status: String,
    pub(crate) detail: Option<String>,
}

impl OrderUpdateWire {
    pub(crate) fn to_py(&self, py: Python<'_>) -> PyResult<PyObject> {
        to_object(
            py,
            (
                self.order_id.as_str(),
                self.status.as_str(),
                self.detail.as_deref(),
            ),
        )
    }
}

/// 자본변동 적용 wire. `corporate_action`은 Python side table의 index다.
#[derive(Clone, Debug)]
pub(crate) struct CorporateActionAppliedWire {
    pub(crate) corporate_action: usize,
    pub(crate) old_quantity: i64,
    pub(crate) new_quantity: i64,
    pub(crate) old_average_price: f64,
    pub(crate) new_average_price: f64,
    pub(crate) cash_paid: f64,
}

impl CorporateActionAppliedWire {
    fn to_py(&self, py: Python<'_>) -> PyResult<PyObject> {
        to_object(
            py,
            (
                self.corporate_action,
                self.old_quantity,
                self.new_quantity,
                self.old_average_price,
                self.new_average_price,
                self.cash_paid,
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
                self.rows.as_slice(),
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
                self.kept.as_slice(),
                self.no_bar.as_slice(),
                self.reason.as_str(),
            ),
        )
    }
}

/// 레코드 payload.
///
/// enum은 가장 큰 variant 크기로 고정되므로, 큰 wire는 모두 `Box`로 간접 참조해 둔다.
/// 그러지 않으면 `OrderWire`(String 8개, 약 232B)가 payload 종류와 무관하게 모든 레코드
/// 자리를 차지해 `Vec<NativeRecord>`가 레코드 수 × 248B로 상주한다. `Box`로 빼면 레코드
/// 배열은 종류와 무관한 작은 크기가 되고, 개별 payload 힙은 `drain_payloads`가 그 자리를
/// `Released`로 바꾸는 순간 바로 반환된다 (`size_of` 단언은 아래 테스트가 고정한다).
#[derive(Clone, Debug)]
pub(crate) enum RecordPayload {
    Market,
    Decision {
        decision_id: String,
        native: Option<Box<NativeDecision>>,
    },
    Order(Box<OrderWire>),
    OrderUpdate(Box<OrderUpdateWire>),
    Fill(Box<FillWire>),
    Snapshot(Box<SnapshotWire>),
    CorporateAction(usize),
    CorporateActionApplied(Box<CorporateActionAppliedWire>),
    Cost {
        kind: String,
        instrument_id: Option<u32>,
        amount: f64,
    },
    /// Python이 이미 공개 객체로 바꾼 뒤 힙을 돌려준 자리. 원래 kind를 그대로 들고 있어
    /// `index()`가 해제 전후로 같은 `(seq, session_index, kind)`를 답한다.
    Released {
        kind: u8,
    },
}

impl RecordPayload {
    pub(crate) fn kind(&self) -> u8 {
        match self {
            RecordPayload::Market => KIND_MARKET,
            RecordPayload::Decision { .. } => KIND_DECISION,
            RecordPayload::Order(_) => KIND_ORDER,
            RecordPayload::OrderUpdate(_) => KIND_ORDER_UPDATE,
            RecordPayload::Fill(_) => KIND_FILL,
            RecordPayload::Snapshot(_) => KIND_SNAPSHOT,
            RecordPayload::CorporateAction(_) => KIND_CORPORATE_ACTION,
            RecordPayload::CorporateActionApplied(_) => KIND_CORPORATE_ACTION_APPLIED,
            RecordPayload::Cost { .. } => KIND_COST,
            RecordPayload::Released { kind } => *kind,
        }
    }

    /// `seq`는 해제된 payload를 만났을 때 어느 레코드인지 알리는 데만 쓴다.
    pub(crate) fn to_py(&self, py: Python<'_>, seq: u64) -> PyResult<PyObject> {
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
            RecordPayload::OrderUpdate(update) => update.to_py(py),
            RecordPayload::Fill(fill) => fill.to_py(py),
            RecordPayload::Snapshot(snapshot) => snapshot.to_py(py),
            RecordPayload::CorporateAction(index) => to_object(py, *index),
            RecordPayload::CorporateActionApplied(applied) => applied.to_py(py),
            RecordPayload::Cost {
                kind,
                instrument_id,
                amount,
            } => to_object(py, (kind.as_str(), *instrument_id, *amount)),
            RecordPayload::Released { kind } => Err(released_error(seq, *kind, "record_payloads")),
        }
    }
}

/// 결과 집계용 columnar 테이블의 Rust 쪽 표현.
///
/// 행 원소의 의미는 Python `backtest_engine/types/result_tables.py`가 정본이다. Python 변환은
/// GIL이 필요하므로 여기서 떼어 둔다 — 순서·결합·해제 가드는 GIL 없이 검사할 수 있다.
/// 문자열은 레코드에서 빌려 쓴다 (테이블은 한 번 만들어 바로 Python으로 넘긴다).
#[allow(clippy::type_complexity)]
#[derive(Debug)]
pub(crate) struct ResultTableRows<'a> {
    pub(crate) snapshots: Vec<(usize, f64, f64, f64, f64)>,
    pub(crate) positions: Vec<(usize, u32, i64, f64, f64, f64, f64)>,
    pub(crate) orders: Vec<(&'a str, &'a str, usize, u32, &'a str, i64, &'a str, &'a str)>,
    pub(crate) fills: Vec<(&'a str, &'a str, usize, u32, &'a str, i64, f64, f64, f64)>,
    pub(crate) costs: Vec<(usize, &'a str, Option<u32>, f64)>,
    /// `(traded_notional, total_fees, total_slippage_cost)` — FILL 레코드 순서 누산.
    pub(crate) fill_totals: (f64, f64, f64),
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
    /// kind별로 어디까지 넘겼는지. 청크마다 레코드를 처음부터 다시 훑지 않기 위한 커서다.
    drain_cursors: [usize; RECORD_KIND_NAMES.len()],
    /// 이미 넘겨서 해제한 레코드 수.
    released: usize,
    /// 모든 레코드를 넘겨 인덱스 Vec까지 돌려준 상태. 이후 조회는 빈 답이 아니라 오류다.
    drained: bool,
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

    /// kind 하나에 속한 레코드를 seq 순서로 모은다. 해제된 payload를 만나면 오류다.
    /// payload 변환과 분리해 GIL 없이도 순서·필터·해제 가드를 검사할 수 있게 둔다.
    fn live_records_of(&self, kind: u8) -> PyResult<Vec<(u64, &NativeRecord)>> {
        self.ensure_not_drained("record_payloads")?;
        let mut rows: Vec<(u64, &NativeRecord)> = Vec::new();
        for (seq, record) in self.records.iter().enumerate() {
            if record.payload.kind() != kind {
                continue;
            }
            if matches!(record.payload, RecordPayload::Released { .. }) {
                return Err(released_error(seq as u64, kind, "record_payloads"));
            }
            rows.push((seq as u64, record));
        }
        Ok(rows)
    }

    /// kind 하나의 `(seq, session_index, payload)`를 seq 순서로 한 번에 돌려준다.
    pub(crate) fn payloads_of(
        &self,
        py: Python<'_>,
        kind: u8,
    ) -> PyResult<Vec<(u64, usize, PyObject)>> {
        self.live_records_of(kind)?
            .into_iter()
            .map(|(seq, record)| Ok((seq, record.session_index, record.payload.to_py(py, seq)?)))
            .collect()
    }

    /// kind 하나의 payload를 앞에서부터 `limit`개까지 Python으로 넘기면서 그 자리를 해제한다.
    /// 빈 Vec이면 그 kind는 다 넘긴 것이다.
    ///
    /// 한 kind를 통째로 넘기면 wire tuple 전부와 Python 객체 전부와 Rust payload가 한순간에
    /// 같이 살아 peak RSS가 셋의 합이 된다. 청크로 넘기면서 바로 해제하면 그 구간이 청크
    /// 크기로 묶인다.
    pub(crate) fn drain(
        &mut self,
        py: Python<'_>,
        kind: u8,
        limit: usize,
    ) -> PyResult<Vec<(u64, usize, PyObject)>> {
        let seqs = self.next_drain_seqs(kind, limit)?;
        let mut rows: Vec<(u64, usize, PyObject)> = Vec::with_capacity(seqs.len());
        for &seq in &seqs {
            let record = &self.records[seq];
            let payload = record.payload.to_py(py, seq as u64)?;
            rows.push((seq as u64, record.session_index, payload));
        }
        self.release_seqs(kind, &seqs);
        Ok(rows)
    }

    /// 다음으로 넘길 레코드 자리를 고르고 kind 커서를 전진시킨다.
    /// payload 변환과 분리해 GIL 없이도 사전 조건과 청크 경계를 검사할 수 있게 둔다.
    fn next_drain_seqs(&mut self, kind: u8, limit: usize) -> PyResult<Vec<usize>> {
        if !self.finished {
            return Err(PyRuntimeError::new_err(format!(
                "cannot drain record payloads before finish — kind={}({kind}) records={}",
                kind_name(kind),
                self.records.len()
            )));
        }
        if limit == 0 {
            return Err(PyValueError::new_err(format!(
                "drain limit must be > 0 — kind={}({kind}) limit={limit}",
                kind_name(kind)
            )));
        }
        let slot = usize::from(kind);
        if slot >= self.drain_cursors.len() {
            return Err(PyValueError::new_err(format!(
                "unknown record kind — kind={kind} expected=0..{}",
                self.drain_cursors.len()
            )));
        }
        // 전부 넘긴 뒤에는 어느 kind를 물어도 "남은 것 없음"이 맞는 답이다 — 조회(읽기)와
        // 달리 drain은 남은 것을 가져가는 호출이라 빈 답이 손실을 감추지 않는다.
        let total = self.records.len();
        let mut seqs: Vec<usize> = Vec::new();
        let mut cursor = self.drain_cursors[slot];
        while cursor < total && seqs.len() < limit {
            let seq = cursor;
            cursor += 1;
            let payload = &self.records[seq].payload;
            if payload.kind() != kind || matches!(payload, RecordPayload::Released { .. }) {
                continue;
            }
            seqs.push(seq);
        }
        self.drain_cursors[slot] = cursor;
        Ok(seqs)
    }

    /// 넘긴 자리를 해제한다. 전부 넘겼으면 인덱스 Vec까지 돌려준다 — Python이 `finish()`에서
    /// 이미 인덱스를 받아 갖고 있으므로 Rust가 더 답할 것이 없다.
    fn release_seqs(&mut self, kind: u8, seqs: &[usize]) {
        for &seq in seqs {
            self.records[seq].payload = RecordPayload::Released { kind };
        }
        self.released += seqs.len();
        if !self.records.is_empty() && self.released == self.records.len() {
            self.records = Vec::new();
            self.drained = true;
        }
    }

    /// 전부 넘긴 뒤의 조회는 빈 답이 아니라 오류여야 한다 — 빈 trace는 조용한 손실이다.
    fn ensure_not_drained(&self, operation: &str) -> PyResult<()> {
        if self.drained {
            return Err(PyRuntimeError::new_err(format!(
                "record store was fully drained — operation={operation} released={}",
                self.released
            )));
        }
        Ok(())
    }

    /// 종료 전 partial trace 조회용 인덱스.
    pub(crate) fn index_for_trace(&self) -> PyResult<Vec<RecordIndexWire>> {
        self.ensure_not_drained("record_batch")?;
        Ok(self.index())
    }

    /// SNAPSHOT record 순서의 equity — metrics 입력.
    /// Python은 `finish()` 직후 SNAPSHOT을 해제하기 전에 부른다.
    pub(crate) fn equity_series(&self) -> PyResult<Vec<f64>> {
        self.ensure_not_drained("equity_series")?;
        let mut series: Vec<f64> = Vec::new();
        for (seq, record) in self.records.iter().enumerate() {
            match &record.payload {
                RecordPayload::Snapshot(snapshot) => series.push(snapshot.equity),
                RecordPayload::Released { kind } if *kind == KIND_SNAPSHOT => {
                    return Err(released_error(seq as u64, KIND_SNAPSHOT, "equity_series"));
                }
                _ => {}
            }
        }
        Ok(series)
    }

    /// 결과 집계용 테이블을 레코드 한 번 순회로 만든다.
    ///
    /// 비파괴 조회다 — 공개 Event 객체를 만들지 않으므로 payload를 해제하지 않고, 이후
    /// `drain_payloads`로 같은 레코드를 공개 객체로 다시 읽을 수 있다. 반대로 테이블이 읽는
    /// kind(SNAPSHOT/ORDER/FILL/COST)를 이미 넘겨 해제했다면 조용히 빠뜨리지 않고 오류다.
    pub(crate) fn result_table_rows(&self) -> PyResult<ResultTableRows<'_>> {
        self.ensure_not_drained("result_tables")?;
        let mut snapshots = Vec::new();
        let mut positions = Vec::new();
        let mut orders = Vec::new();
        let mut fills = Vec::new();
        let mut costs = Vec::new();
        let mut traded_notional = 0.0_f64;
        let mut total_fees = 0.0_f64;
        let mut total_slippage_cost = 0.0_f64;
        for (seq, record) in self.records.iter().enumerate() {
            let session = record.session_index;
            match &record.payload {
                RecordPayload::Snapshot(snapshot) => {
                    // positions_value는 행 순서대로 왼쪽부터 더한다 — Python `sum()`과 같은
                    // 결합 순서여야 두 코어의 net exposure가 bit 동일하다.
                    let mut positions_value = 0.0_f64;
                    for row in &snapshot.rows {
                        positions_value += row.4;
                        positions.push((session, row.0, row.1, row.2, row.3, row.4, row.5));
                    }
                    snapshots.push((
                        session,
                        snapshot.cash,
                        snapshot.equity,
                        snapshot.gross_exposure,
                        positions_value,
                    ));
                }
                RecordPayload::Order(order) => orders.push((
                    order.order_id.as_str(),
                    order.decision_id.as_str(),
                    session,
                    order.instrument_id,
                    order.side.as_str(),
                    order.quantity,
                    order.order_type.as_str(),
                    order.tif.as_str(),
                )),
                RecordPayload::Fill(fill) => {
                    fills.push((
                        fill.fill_id.as_str(),
                        fill.order_id.as_str(),
                        session,
                        fill.instrument_id,
                        fill.side.as_str(),
                        fill.quantity,
                        fill.price,
                        fill.fee,
                        fill.slippage_per_share,
                    ));
                    traded_notional += fill.quantity as f64 * fill.price;
                    total_fees += fill.fee;
                    total_slippage_cost += fill.quantity as f64 * fill.slippage_per_share.abs();
                }
                RecordPayload::Cost {
                    kind,
                    instrument_id,
                    amount,
                } => costs.push((session, kind.as_str(), *instrument_id, *amount)),
                RecordPayload::Released { kind }
                    if matches!(*kind, KIND_SNAPSHOT | KIND_ORDER | KIND_FILL | KIND_COST) =>
                {
                    return Err(released_error(seq as u64, *kind, "result_tables"));
                }
                _ => {}
            }
        }
        Ok(ResultTableRows {
            snapshots,
            positions,
            orders,
            fills,
            costs,
            fill_totals: (traded_notional, total_fees, total_slippage_cost),
        })
    }

    /// `result_table_rows`를 Python 튜플 묶음으로 넘긴다.
    pub(crate) fn result_tables(&self, py: Python<'_>) -> PyResult<PyObject> {
        let rows = self.result_table_rows()?;
        to_object(
            py,
            (
                rows.snapshots,
                rows.positions,
                rows.orders,
                rows.fills,
                rows.costs,
                rows.fill_totals,
            ),
        )
    }

    /// FILL record 순서로 `quantity × price`를 누산한다 (Python `traded_notional`과 같은 결합 순서).
    /// Python은 `finish()` 직후 FILL을 해제하기 전에 부른다.
    pub(crate) fn traded_notional(&self) -> PyResult<f64> {
        self.ensure_not_drained("traded_notional")?;
        let mut total = 0.0_f64;
        for (seq, record) in self.records.iter().enumerate() {
            match &record.payload {
                RecordPayload::Fill(fill) => total += fill.quantity as f64 * fill.price,
                RecordPayload::Released { kind } if *kind == KIND_FILL => {
                    return Err(released_error(seq as u64, KIND_FILL, "traded_notional"));
                }
                _ => {}
            }
        }
        Ok(total)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fill(quantity: i64, price: f64) -> RecordPayload {
        RecordPayload::Fill(Box::new(FillWire {
            fill_id: "F-000001".into(),
            order_id: "O-000001".into(),
            instrument_id: 0,
            quantity,
            side: "buy".into(),
            price,
            fee: 0.0,
            slippage_per_share: 0.0,
        }))
    }

    fn snapshot(equity: f64) -> RecordPayload {
        RecordPayload::Snapshot(Box::new(SnapshotWire {
            cash: equity,
            rows: Vec::new(),
            equity,
            gross_exposure: 0.0,
        }))
    }

    /// 레코드 배열은 payload 종류와 무관하게 이 크기로 상주한다 — 가장 큰 wire를 `Box`로
    /// 빼 둔 구조가 무너지면(어느 variant를 인라인으로 되돌리면) 여기서 먼저 깨진다.
    /// 값은 64-bit 대상에서 잰 실측치다.
    #[test]
    fn record_payload_stays_small_enough_for_a_dense_record_array() {
        assert_eq!(std::mem::size_of::<RecordPayload>(), 40);
        assert_eq!(std::mem::size_of::<NativeRecord>(), 48);
        // 인라인으로 두면 레코드 한 자리가 이만큼으로 부푸는 wire들.
        assert!(std::mem::size_of::<OrderWire>() >= 200);
        assert!(std::mem::size_of::<FillWire>() >= 96);
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
        assert_eq!(store.equity_series().unwrap(), vec![1.0, 2.0]);
        assert_eq!(store.traded_notional().unwrap(), 3.0 * 10.0 + 2.0 * 0.1);
    }

    fn filled_store() -> RecordStore {
        let mut store = RecordStore::default();
        store.append(0, RecordPayload::Market).unwrap();
        store.append(0, fill(3, 10.0)).unwrap();
        store.append(1, snapshot(2.0)).unwrap();
        store.append(1, fill(2, 0.1)).unwrap();
        store
    }

    #[test]
    fn live_records_of_keeps_seq_order_within_one_kind() {
        let store = filled_store();
        let seqs: Vec<u64> = store
            .live_records_of(KIND_FILL)
            .unwrap()
            .into_iter()
            .map(|(seq, _)| seq)
            .collect();
        assert_eq!(seqs, vec![1, 3]);
        let sessions: Vec<usize> = store
            .live_records_of(KIND_FILL)
            .unwrap()
            .into_iter()
            .map(|(_, record)| record.session_index)
            .collect();
        assert_eq!(sessions, vec![0, 1]);
        assert!(store.live_records_of(KIND_ORDER).unwrap().is_empty());
    }

    /// payload 변환(GIL 필요)만 빼고 `drain`이 하는 일을 그대로 한다.
    fn drain_kind(store: &mut RecordStore, kind: u8, limit: usize) -> Vec<usize> {
        let seqs = store.next_drain_seqs(kind, limit).unwrap();
        store.release_seqs(kind, &seqs);
        seqs
    }

    #[test]
    fn drain_rejects_unfinished_store_zero_limit_and_unknown_kind() {
        let mut store = filled_store();
        let unfinished = store.next_drain_seqs(KIND_FILL, 8).unwrap_err().to_string();
        assert!(unfinished.contains("before finish"), "{unfinished}");
        assert!(unfinished.contains("kind=fill(4)"), "{unfinished}");
        store.finish().unwrap();
        let zero = store.next_drain_seqs(KIND_FILL, 0).unwrap_err().to_string();
        assert!(zero.contains("limit must be > 0"), "{zero}");
        let unknown = store.next_drain_seqs(99, 8).unwrap_err().to_string();
        assert!(
            unknown.contains("unknown record kind — kind=99"),
            "{unknown}"
        );
    }

    #[test]
    fn drain_hands_out_one_kind_in_seq_order_chunk_by_chunk() {
        let mut store = filled_store();
        store.finish().unwrap();
        assert_eq!(drain_kind(&mut store, KIND_FILL, 1), vec![1]);
        assert_eq!(drain_kind(&mut store, KIND_FILL, 1), vec![3]);
        assert!(drain_kind(&mut store, KIND_FILL, 1).is_empty());
        // 커서는 kind마다 따로다 — FILL을 다 넘겨도 SNAPSHOT은 처음부터 읽는다.
        assert_eq!(drain_kind(&mut store, KIND_SNAPSHOT, 8), vec![2]);
    }

    #[test]
    fn released_kind_is_refused_by_every_reader() {
        let mut store = filled_store();
        store.finish().unwrap();
        assert_eq!(drain_kind(&mut store, KIND_FILL, 8).len(), 2);
        // 인덱스 행은 그대로 남는다 — 해제는 payload 힙만 돌려준다.
        assert_eq!(
            store.index(),
            vec![
                (0, 0, KIND_MARKET),
                (1, 0, KIND_FILL),
                (2, 1, KIND_SNAPSHOT),
                (3, 1, KIND_FILL),
            ]
        );
        let batch = store.live_records_of(KIND_FILL).unwrap_err().to_string();
        assert!(batch.contains("operation=record_payloads"), "{batch}");
        assert!(batch.contains("seq=1"), "{batch}");
        assert!(batch.contains("kind=fill(4)"), "{batch}");
        let notional = store.traded_notional().unwrap_err().to_string();
        assert!(notional.contains("operation=traded_notional"), "{notional}");
        // 다른 kind는 그대로 읽힌다.
        assert_eq!(store.equity_series().unwrap(), vec![2.0]);
        assert_eq!(store.live_records_of(KIND_SNAPSHOT).unwrap().len(), 1);

        drain_kind(&mut store, KIND_SNAPSHOT, 8);
        let equity = store.equity_series().unwrap_err().to_string();
        assert!(equity.contains("operation=equity_series"), "{equity}");
        assert!(equity.contains("seq=2"), "{equity}");
    }

    #[test]
    fn fully_drained_store_answers_errors_not_empty_results() {
        let mut store = filled_store();
        store.finish().unwrap();
        for kind in [KIND_MARKET, KIND_FILL, KIND_SNAPSHOT] {
            drain_kind(&mut store, kind, 8);
        }
        assert!(store.drained);
        // 아직 한 번도 안 넘긴 kind를 물어도 남은 것이 없다 — drain은 빈 답이 맞다.
        assert!(drain_kind(&mut store, KIND_ORDER, 8).is_empty());
        // 읽기 조회는 빈 답 대신 오류여야 한다 — 빈 trace는 조용한 손실이다.
        for message in [
            store.index_for_trace().unwrap_err().to_string(),
            store.live_records_of(KIND_FILL).unwrap_err().to_string(),
            store.equity_series().unwrap_err().to_string(),
            store.traded_notional().unwrap_err().to_string(),
        ] {
            assert!(message.contains("fully drained"), "{message}");
            assert!(message.contains("released=4"), "{message}");
        }
    }

    fn order(order_id: &str, quantity: i64) -> RecordPayload {
        RecordPayload::Order(Box::new(OrderWire {
            order_id: order_id.into(),
            decision_id: "D-000001".into(),
            instrument_id: 0,
            quantity,
            side: "buy".into(),
            order_type: "market".into(),
            limit_text: None,
            stop_text: None,
            tif: "day".into(),
            group_id: None,
            action_index: 0,
            leg_index: None,
        }))
    }

    #[test]
    fn result_tables_keep_record_order_and_flatten_positions_per_snapshot() {
        let mut store = RecordStore::default();
        store.append(0, RecordPayload::Market).unwrap();
        store.append(0, order("O-000001", 7)).unwrap();
        store
            .append(
                0,
                RecordPayload::Snapshot(Box::new(SnapshotWire {
                    cash: 5.0,
                    rows: vec![
                        (0, 7, 10.0, 11.0, 77.0, 7.0),
                        (1, 3, 20.0, 19.0, 57.0, -3.0),
                    ],
                    equity: 139.0,
                    gross_exposure: 0.9,
                })),
            )
            .unwrap();
        store.append(1, fill(3, 10.0)).unwrap();
        store
            .append(
                1,
                RecordPayload::Cost {
                    kind: "margin_interest".into(),
                    instrument_id: None,
                    amount: 1.5,
                },
            )
            .unwrap();

        let tables = store.result_table_rows().unwrap();
        // positions_value는 행 순서 좌→우 결합이다.
        assert_eq!(tables.snapshots, vec![(0, 5.0, 139.0, 0.9, 77.0 + 57.0)]);
        assert_eq!(
            tables.positions,
            vec![
                (0, 0, 7, 10.0, 11.0, 77.0, 7.0),
                (0, 1, 3, 20.0, 19.0, 57.0, -3.0),
            ]
        );
        assert_eq!(
            tables.orders,
            vec![("O-000001", "D-000001", 0, 0, "buy", 7, "market", "day")]
        );
        assert_eq!(
            tables.fills,
            vec![("F-000001", "O-000001", 1, 0, "buy", 3, 10.0, 0.0, 0.0)]
        );
        assert_eq!(tables.costs, vec![(1, "margin_interest", None, 1.5)]);
        assert_eq!(tables.fill_totals, (30.0, 0.0, 0.0));
    }

    #[test]
    fn result_tables_read_kinds_that_are_still_live_and_refuse_released_ones() {
        let mut store = filled_store();
        store.finish().unwrap();
        // MARKET을 넘겨도 테이블이 읽는 kind가 아니므로 그대로 답한다.
        drain_kind(&mut store, KIND_MARKET, 8);
        let tables = store.result_table_rows().unwrap();
        assert_eq!(tables.fills.len(), 2);
        assert_eq!(tables.snapshots.len(), 1);

        drain_kind(&mut store, KIND_FILL, 8);
        let message = store.result_table_rows().unwrap_err().to_string();
        assert!(message.contains("operation=result_tables"), "{message}");
        assert!(message.contains("kind=fill(4)"), "{message}");
    }

    #[test]
    fn released_payload_conversion_reports_the_record() {
        let released = RecordPayload::Released { kind: KIND_ORDER };
        assert_eq!(released.kind(), KIND_ORDER);
        // `to_py`는 GIL이 필요하지만 해제 가드는 변환 전에 걸린다 — 오류 문구만 고정한다.
        let message = released_error(7, KIND_ORDER, "record_payloads").to_string();
        assert!(message.contains("seq=7"), "{message}");
        assert!(message.contains("kind=order(2)"), "{message}");
    }
}
