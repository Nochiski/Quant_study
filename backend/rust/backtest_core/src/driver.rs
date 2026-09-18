//! Persistent runtime의 세션 루프 드라이버.
//!
//! Python `engine/loop.py`의 `_execute`/`_on_market`/`_on_session_close`/`_dispatch`가 하던
//! MARKET → FILL → NOTIFY → SESSION_CLOSE → ORDER 드레인을 Rust 안에서 진행한다. Python은
//! 전략 콜백이 필요할 때만 `CallbackFrame`을 받고 `submit_decision`으로 돌려준다. 레코드
//! 순서·ID·오류 시점은 Python reference와 같아야 하며 `tests/test_core_parity.py`가 고정한다.

use crate::callback::CallbackFrame;
use crate::persistent::{Lifecycle, PersistentEngine, StoredGroup, StoredOrder};
use crate::persistent_router::{self, DecisionWire, RouteError};
use crate::portfolio::SnapshotTuple;
use crate::records::{
    to_object, CorporateActionAppliedWire, FillWire, NativeDecision, OrderUpdateWire, OrderWire,
    RecordPayload, SnapshotWire,
};
use crate::session::py_float;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::sync::Arc;

/// Python `EventPriority`와 같은 값. 같은 세션 안에서 숫자가 작을수록 먼저 처리한다.
pub(crate) const PRIORITY_MARKET: u8 = 10;
pub(crate) const PRIORITY_FILL: u8 = 20;
pub(crate) const PRIORITY_NOTIFY: u8 = 25;
pub(crate) const PRIORITY_SESSION_CLOSE: u8 = 30;
pub(crate) const PRIORITY_ORDER: u8 = 40;

/// wire 상수 대조용 (name, value) 목록. name은 Python `EventPriority` 멤버명의 소문자다.
/// `lib.rs`가 모듈 상수로 노출하고 `tests/test_core_parity.py`가 Python 정본과 대조한다.
pub(crate) const EVENT_PRIORITY_NAMES: [(&str, u8); 5] = [
    ("market", PRIORITY_MARKET),
    ("fill", PRIORITY_FILL),
    ("notify", PRIORITY_NOTIFY),
    ("session_close", PRIORITY_SESSION_CLOSE),
    ("order", PRIORITY_ORDER),
];

/// `configure_run`으로 한 번 받는 실행 설정 (RunConfig + requirements 일부).
#[derive(Clone, Debug)]
pub(crate) struct RunSettings {
    pub(crate) fee_rate: f64,
    pub(crate) default_participation: Option<String>,
    pub(crate) slippage: (String, f64, f64),
    pub(crate) schedule: String,
    pub(crate) short_borrow_bps_annual: f64,
    pub(crate) margin_interest_bps_annual: f64,
    pub(crate) annualization_days: u32,
    pub(crate) warmup_sessions: usize,
    pub(crate) notify_fill: bool,
    pub(crate) notify_order_update: bool,
    pub(crate) notify_corporate_action: bool,
}

/// 정산 세션이 확정된 자본변동 사건. index는 Python side table의 위치다.
#[derive(Clone, Debug)]
pub(crate) struct CorporateActionEntry {
    pub(crate) key: String,
    pub(crate) symbol: String,
    pub(crate) action_type: String,
    pub(crate) ratio: String,
    pub(crate) event_ts: String,
    pub(crate) confirmed: bool,
}

/// 전략에 전달할 알림 payload (requirements().events에 선언된 것만 큐에 실린다).
#[derive(Clone, Debug)]
pub(crate) enum NotifyPayload {
    Fill(FillWire),
    OrderUpdate {
        order_id: String,
        status: String,
        detail: Option<String>,
    },
    CorporateAction(usize),
}

impl NotifyPayload {
    pub(crate) fn event_kind(&self) -> &'static str {
        match self {
            NotifyPayload::Fill(_) => "fill",
            NotifyPayload::OrderUpdate { .. } => "order_update",
            NotifyPayload::CorporateAction(_) => "corporate_action",
        }
    }

    pub(crate) fn to_py(&self, py: Python<'_>) -> PyResult<PyObject> {
        match self {
            NotifyPayload::Fill(fill) => fill.to_py(py),
            NotifyPayload::OrderUpdate {
                order_id,
                status,
                detail,
            } => to_object(py, (order_id.as_str(), status.as_str(), detail.as_deref())),
            NotifyPayload::CorporateAction(index) => to_object(py, *index),
        }
    }
}

/// 큐 payload. token은 `queued` Vec의 index다.
///
/// 세션은 큐 엔트리의 정렬 키가 단일 진실 원천이라 payload에 담지 않는다.
#[derive(Clone, Debug)]
pub(crate) enum Queued {
    Market,
    Fill(FillWire),
    Notify(NotifyPayload),
    SessionClose,
    Order(OrderWire),
}

impl PersistentEngine {
    fn settings(&self) -> PyResult<&RunSettings> {
        self.run.as_deref().ok_or_else(|| {
            PyValueError::new_err("persistent run is not configured — call configure_run first")
        })
    }

    /// 실행 설정을 `self` 빌림과 분리해 꺼낸다. 세션 루프가 `&mut self` 메서드를 부르는 동안에도
    /// 설정을 읽어야 해서, 예전에는 `RunSettings` 전체를 clone(문자열 3개 할당)했다.
    /// `Arc` 복제는 참조 카운트 증가뿐이라 세션마다 드는 할당이 사라진다.
    fn settings_arc(&self) -> PyResult<Arc<RunSettings>> {
        self.run.clone().ok_or_else(|| {
            PyValueError::new_err("persistent run is not configured — call configure_run first")
        })
    }

    /// 실행 설정이 있는지만 확인한다 — 값을 쓰지 않는 진입 가드에서 의도를 드러낸다.
    fn require_configured(&self) -> PyResult<()> {
        self.settings().map(|_| ())
    }

    fn feed_ref(&self) -> PyResult<&crate::feed::PersistentFeed> {
        self.feed
            .as_ref()
            .ok_or_else(|| PyValueError::new_err("persistent feed is not loaded"))
    }

    fn instrument_id_for_key(&self, key: &str) -> PyResult<u32> {
        self.feed_ref()?.instrument_id(key).ok_or_else(|| {
            PyValueError::new_err(format!(
                "instrument is not in the loaded feed registry — key={key}"
            ))
        })
    }

    fn push(&mut self, session: usize, priority: u8, payload: Queued) -> PyResult<()> {
        let token = self.queued.len() as u64;
        self.queued.push(Some(payload));
        self.event_queue.push(session as i64, priority, token)
    }

    /// 큐에서 `(세션, payload)`를 꺼낸다. 세션은 `push`가 정렬 키로 넣은 값 그대로다.
    fn pop(&mut self) -> PyResult<Option<(usize, Queued)>> {
        if self.event_queue.len() == 0 {
            return Ok(None);
        }
        let (session, token) = self.event_queue.pop()?;
        let token = token as usize;
        let payload = self
            .queued
            .get_mut(token)
            .and_then(Option::take)
            .ok_or_else(|| {
                PyValueError::new_err(format!("queue token has no payload — token={token}"))
            })?;
        Ok(Some((session as usize, payload)))
    }

    fn record(&mut self, session: usize, payload: RecordPayload) -> PyResult<()> {
        self.records.append(session, payload)
    }

    /// ORDER_UPDATE 레코드 + 선언 시 NOTIFY 큐 적재 (`loop._record_compact_update`).
    fn record_update(
        &mut self,
        session: usize,
        order_id: String,
        status: String,
        detail: Option<String>,
    ) -> PyResult<()> {
        let notify = self.settings()?.notify_order_update;
        self.record(
            session,
            RecordPayload::OrderUpdate(Box::new(OrderUpdateWire {
                order_id: order_id.clone(),
                status: status.clone(),
                detail: detail.clone(),
            })),
        )?;
        if notify {
            self.push(
                session,
                PRIORITY_NOTIFY,
                Queued::Notify(NotifyPayload::OrderUpdate {
                    order_id,
                    status,
                    detail,
                }),
            )?;
        }
        Ok(())
    }

    pub(crate) fn order_wire(&self, order: &StoredOrder) -> PyResult<OrderWire> {
        Ok(OrderWire {
            order_id: order.order_id.clone(),
            decision_id: order.decision_id.clone(),
            instrument_id: self.instrument_id_for_key(&order.key)?,
            quantity: order.quantity,
            side: order.side.clone(),
            order_type: order.order_type.clone(),
            limit_text: order.limit_text.clone(),
            stop_text: order.stop_text.clone(),
            tif: order.tif.clone(),
            group_id: order.group_id.clone(),
            action_index: order.action_index,
            leg_index: order.leg_index,
        })
    }

    pub(crate) fn snapshot_wire(&self) -> PyResult<SnapshotWire> {
        self.snapshot_wire_from(self.portfolio.snapshot()?)
    }

    /// 포트폴리오 스냅샷 튜플의 key를 instrument id로 바꾼다 — `close_current_session`이 이미
    /// 만든 스냅샷을 재사용해 세션마다 원장을 두 번 훑지 않는다.
    fn snapshot_wire_from(&self, snapshot: SnapshotTuple) -> PyResult<SnapshotWire> {
        let (cash, rows, equity, gross_exposure) = snapshot;
        let rows = rows
            .into_iter()
            .map(|(key, quantity, average, mark, market_value, unrealized)| {
                Ok((
                    self.instrument_id_for_key(&key)?,
                    quantity,
                    average,
                    mark,
                    market_value,
                    unrealized,
                ))
            })
            .collect::<PyResult<Vec<_>>>()?;
        Ok(SnapshotWire {
            cash,
            rows,
            equity,
            gross_exposure,
        })
    }

    fn open_orders_wire(&self) -> PyResult<Vec<(OrderWire, i64)>> {
        self.orders
            .iter()
            .map(|order| Ok((self.order_wire(order)?, order.remaining)))
            .collect()
    }

    /// 콜백 프레임을 만들고 runtime을 AwaitingDecision으로 전환한다. 포트폴리오·대기 주문은
    /// 콜백 시점 계약상 여기서 Rust wire로 clone하고, Python 객체 변환은 getter가 읽을 때만 한다.
    fn make_frame(
        &mut self,
        event_kind: &str,
        session: usize,
        event: Option<NotifyPayload>,
        snapshot: SnapshotWire,
    ) -> PyResult<CallbackFrame> {
        let ts = self.feed_ref()?.session_at(session)?.to_string();
        self.callback_seq = self.callback_seq.checked_add(1).ok_or_else(|| {
            PyValueError::new_err("persistent callback token sequence exhausted u64")
        })?;
        let token = self.callback_seq;
        let open_orders = self.open_orders_wire()?;
        self.lifecycle = Lifecycle::AwaitingDecision(token);
        self.awaiting_session = session;
        Ok(CallbackFrame {
            token,
            event_kind: event_kind.to_string(),
            session_index: session,
            ts,
            event,
            snapshot,
            open_orders,
        })
    }

    /// 다음 전략 콜백까지 세션을 진행한다. 콜백이 더 없으면 `None`.
    pub(crate) fn drive_internal(&mut self) -> PyResult<Option<CallbackFrame>> {
        match self.lifecycle {
            Lifecycle::AwaitingDecision(token) => {
                return Err(PyValueError::new_err(format!(
                    "callback already awaiting a decision — token={token}"
                )))
            }
            Lifecycle::Failed => {
                return Err(PyValueError::new_err(format!(
                    "persistent runtime is failed — detail={:?}",
                    self.failure_message
                )))
            }
            Lifecycle::Finished => {
                return Err(PyValueError::new_err(
                    "persistent runtime is already finished",
                ))
            }
            Lifecycle::Ready | Lifecycle::Running => {}
        }
        self.require_configured()?;
        match self.drain_until_callback() {
            Ok(frame) => Ok(frame),
            Err(error) => {
                // 드레인에서 올라오는 모든 오류가 runtime을 실패로 고정한다 — 도메인 오류(자본
                // 소진·음수 현금·정산 bar 없음)뿐 아니라 오용 오류(설정 누락, 큐 토큰 불일치)도
                // 포함한다. 어느 쪽이든 세션 처리가 중간에 끊긴 상태라, 재진입하면 비용은
                // 반영됐지만 SNAPSHOT이 없는 세션 뒤로 조용히 이어진다.
                //
                // 드레인 안의 `submit_native`가 이미 Failed로 바꿨다면 그쪽 detail이 더 구체적이라
                // 덮어쓰지 않는다.
                if !matches!(self.lifecycle, Lifecycle::Failed) {
                    self.lifecycle = Lifecycle::Failed;
                    self.failure_message = Some(error.to_string());
                }
                Err(error)
            }
        }
    }

    /// `drive_internal`의 본체: 큐를 드레인하다가 전략 콜백이 필요하면 프레임을 돌려준다.
    fn drain_until_callback(&mut self) -> PyResult<Option<CallbackFrame>> {
        if !self.started {
            // Python `_execute`처럼 모든 세션의 MARKET 이벤트를 먼저 큐에 싣는다.
            let sessions = self.feed_ref()?.session_len();
            for session in 0..sessions {
                self.push(session, PRIORITY_MARKET, Queued::Market)?;
            }
            self.started = true;
            self.lifecycle = Lifecycle::Running;
        }
        while let Some((session, event)) = self.pop()? {
            match event {
                Queued::Market => self.on_market(session)?,
                Queued::Fill(fill) => {
                    self.record(session, RecordPayload::Fill(Box::new(fill)))?;
                }
                Queued::Notify(payload) => {
                    // 큐 엔트리의 세션이 곧 피드 커서(`current_session_count()` − 1)다.
                    // MARKET은 세션 안에서 우선순위가 가장 낮아 같은 세션 키의 다른 이벤트보다
                    // 먼저 팝되고 피드 커서를 그 세션으로 옮긴다. 파생 이벤트는 모두 그때의
                    // `session`을 키로 push하므로, 팝 시점의 커서와 엔트리 세션이 항상 같다.
                    // 따라서 warmup 판정(`session + 1 < warmup_sessions`)도 그대로 동치다.
                    if session + 1 < self.settings()?.warmup_sessions {
                        continue;
                    }
                    let snapshot = self.snapshot_wire()?;
                    let kind = payload.event_kind();
                    let frame = self.make_frame(kind, session, Some(payload), snapshot)?;
                    if self.tape.is_some() {
                        self.submit_native(&frame)?;
                        continue;
                    }
                    return Ok(Some(frame));
                }
                Queued::SessionClose => {
                    if let Some(snapshot) = self.on_session_close(session)? {
                        let frame = self.make_frame("market", session, None, snapshot)?;
                        if self.tape.is_some() {
                            self.submit_native(&frame)?;
                            continue;
                        }
                        return Ok(Some(frame));
                    }
                }
                Queued::Order(order) => {
                    self.record(session, RecordPayload::Order(Box::new(order)))?;
                }
            }
        }
        Ok(None)
    }

    /// `loop._on_market` + `_on_market_persistent` + `_apply_corporate_action`.
    fn on_market(&mut self, session: usize) -> PyResult<()> {
        self.record(session, RecordPayload::Market)?;
        if self.debug_panic_on_market {
            panic!("forced persistent runtime panic for boundary verification");
        }
        // 이전 세션 ORDER priority에서 공개된 주문을 일괄 활성화한다. 자본변동 취소가
        // 해당 주문까지 볼 수 있어야 하므로 corporate action 처리보다 앞선다.
        self.activate_pending_internal()?;

        let actions: Vec<usize> = self
            .ca_by_session
            .get(&session)
            .cloned()
            .unwrap_or_default();
        for index in actions {
            self.apply_corporate_action_entry(session, index)?;
        }

        let settings = self.settings_arc()?;
        // 체결 payload가 필요한 주문 메타를 처리 전에 잡아둔다 — 전량 체결된 주문은 ops 적용
        // 중 제거된다.
        let order_meta: HashMap<String, (u32, String)> = self
            .orders
            .iter()
            .map(|order| {
                Ok((
                    order.order_id.clone(),
                    (self.instrument_id_for_key(&order.key)?, order.side.clone()),
                ))
            })
            .collect::<PyResult<_>>()?;
        let ops = self.process_market_index(
            session,
            settings.fee_rate,
            settings.default_participation.as_deref(),
            &settings.slippage,
        )?;
        for (kind, order_id, quantity, price, slip, fee, payload) in ops {
            match kind.as_str() {
                "fill" => {
                    let (instrument_id, side) =
                        order_meta.get(&order_id).cloned().ok_or_else(|| {
                            PyValueError::new_err(format!(
                                "rust core filled an order that is not open — order_id={order_id}"
                            ))
                        })?;
                    let fill = FillWire {
                        fill_id: payload,
                        order_id,
                        instrument_id,
                        quantity,
                        side,
                        price,
                        fee,
                        slippage_per_share: slip,
                    };
                    self.push(session, PRIORITY_FILL, Queued::Fill(fill.clone()))?;
                    if settings.notify_fill {
                        self.push(
                            session,
                            PRIORITY_NOTIFY,
                            Queued::Notify(NotifyPayload::Fill(fill)),
                        )?;
                    }
                }
                "update" => {
                    let (status, detail) = match payload.split_once('|') {
                        Some((status, detail)) => (status.to_string(), detail.to_string()),
                        None => (payload.clone(), String::new()),
                    };
                    let detail = if detail.is_empty() {
                        None
                    } else {
                        Some(detail)
                    };
                    self.record_update(session, order_id, status, detail)?;
                }
                // mutable 상태는 process_market 안에서 이미 적용됐다.
                "trigger" | "remove" | "drop_group" => {}
                other => {
                    return Err(PyValueError::new_err(format!(
                        "unknown op from persistent rust core — kind={other:?}"
                    )))
                }
            }
        }
        self.push(session, PRIORITY_SESSION_CLOSE, Queued::SessionClose)
    }

    fn apply_corporate_action_entry(&mut self, session: usize, index: usize) -> PyResult<()> {
        let entry = self.corporate_actions[index].clone();
        self.record(session, RecordPayload::CorporateAction(index))?;
        if entry.confirmed {
            // 가격 수준이 무의미해지는 확인된 분할·병합만 대기 주문을 취소한다 (스펙 결정 3).
            let remaining_by_id: HashMap<String, i64> = self
                .orders
                .iter()
                .map(|order| (order.order_id.clone(), order.remaining))
                .collect();
            let session_ts = self.feed_ref()?.session_at(session)?.to_string();
            let cancelled = self.cancel_for_key(&entry.key);
            for order_id in cancelled {
                let remaining = remaining_by_id.get(&order_id).copied().unwrap_or(0);
                let detail = format!(
                    "cancelled by corporate action — instrument={} action={} ratio={} \
                     event_ts={} settled_at={} remaining={}",
                    entry.symbol,
                    entry.action_type,
                    entry.ratio,
                    entry.event_ts,
                    session_ts,
                    remaining
                );
                self.record_update(session, order_id, "cancelled".to_string(), Some(detail))?;
            }
            let settlement_price =
                self.feed_ref()?
                    .open_at(session, &entry.key)
                    .ok_or_else(|| {
                        PyValueError::new_err(format!(
                            "corporate action settlement session has no bar — instrument={} \
                         session={session_ts}",
                            entry.symbol
                        ))
                    })?;
            let applied =
                self.apply_corporate_action_ratio(&entry.key, &entry.ratio, settlement_price)?;
            if let Some((old_quantity, new_quantity, old_average, new_average, cash_paid)) = applied
            {
                self.record(
                    session,
                    RecordPayload::CorporateActionApplied(Box::new(CorporateActionAppliedWire {
                        corporate_action: index,
                        old_quantity,
                        new_quantity,
                        old_average_price: old_average,
                        new_average_price: new_average,
                        cash_paid,
                    })),
                )?;
            }
        }
        if self.settings()?.notify_corporate_action {
            self.push(
                session,
                PRIORITY_NOTIFY,
                Queued::Notify(NotifyPayload::CorporateAction(index)),
            )?;
        }
        Ok(())
    }

    /// `loop._on_session_close`: 비용·스냅샷 기록 후 일정과 warmup을 만족하면 스냅샷을 돌려준다.
    fn on_session_close(&mut self, session: usize) -> PyResult<Option<SnapshotWire>> {
        let settings = self.settings_arc()?;
        let (should_dispatch, costs, closed) = self.close_current_session(
            &settings.schedule,
            settings.short_borrow_bps_annual,
            settings.margin_interest_bps_annual,
            settings.annualization_days,
        )?;
        for (kind, key, amount) in costs {
            let instrument_id = match key {
                Some(key) => Some(self.instrument_id_for_key(&key)?),
                None => None,
            };
            self.record(
                session,
                RecordPayload::Cost {
                    kind,
                    instrument_id,
                    amount,
                },
            )?;
        }
        let snapshot = self.snapshot_wire_from(closed)?;
        if snapshot.equity < 0.0 {
            let feed = self.feed_ref()?;
            let positions: Vec<String> = snapshot
                .rows
                .iter()
                .map(|row| format!("('{}', '{}')", feed.symbol_of(row.0), row.1))
                .collect();
            return Err(PyValueError::new_err(format!(
                "equity_wiped_out: equity fell below zero at session close — ts={} equity={} \
                 cash={} positions=[{}]",
                feed.session_at(session)?,
                py_float(snapshot.equity),
                py_float(snapshot.cash),
                positions.join(", ")
            )));
        }
        self.record(session, RecordPayload::Snapshot(Box::new(snapshot.clone())))?;
        // warmup 판정은 NOTIFY 분기와 같은 식이다 — 팝된 세션이 곧 피드 커서라는 근거는
        // 그쪽 주석에 있다.
        if should_dispatch && session + 1 >= settings.warmup_sessions {
            return Ok(Some(snapshot));
        }
        Ok(None)
    }

    /// `loop._dispatch`의 결정 이후 절반: DECISION 기록 → 라우팅 → ORDER_UPDATE 기록 → ORDER 큐.
    pub(crate) fn submit_internal(
        &mut self,
        token: u64,
        decision: DecisionWire,
        native: Option<NativeDecision>,
    ) -> PyResult<(String, Option<RouteError>)> {
        match self.lifecycle {
            Lifecycle::AwaitingDecision(expected) if token == expected => {}
            Lifecycle::AwaitingDecision(expected) => {
                return Err(PyValueError::new_err(format!(
                    "stale callback token — expected={expected} got={token}"
                )))
            }
            Lifecycle::Failed => {
                return Err(PyValueError::new_err(format!(
                    "persistent runtime is failed — detail={:?}",
                    self.failure_message
                )))
            }
            Lifecycle::Finished => {
                return Err(PyValueError::new_err(
                    "persistent runtime is already finished",
                ))
            }
            Lifecycle::Ready | Lifecycle::Running => {
                return Err(PyValueError::new_err(format!(
                    "no callback is awaiting a decision — token={token}"
                )))
            }
        }
        let session = self.awaiting_session;
        let decision_id = Self::next_id(&mut self.decision_seq, 'D');
        // 라우팅 오류여도 DECISION은 남는다 — Python `_dispatch`가 raise 전에 기록한다.
        self.record(
            session,
            RecordPayload::Decision {
                decision_id: decision_id.clone(),
                native: native.map(Box::new),
            },
        )?;
        let (orders, updates, error) = match self.route_with_id(&decision_id, &decision) {
            Ok(routed) => routed,
            Err(error) => {
                self.lifecycle = Lifecycle::Failed;
                self.failure_message = Some(error.to_string());
                return Err(error);
            }
        };
        if let Some(error) = error {
            self.lifecycle = Lifecycle::Failed;
            self.failure_message = Some(error.1.clone());
            return Ok((decision_id, Some(error)));
        }
        for (order_id, status, detail) in updates {
            self.record_update(session, order_id, status, Some(detail))?;
        }
        for order in orders {
            self.push(session, PRIORITY_ORDER, Queued::Order(order))?;
        }
        self.lifecycle = Lifecycle::Running;
        Ok((decision_id, None))
    }

    /// 라우터를 돌리고 신규 주문·그룹을 pending 영역에 저장한다. 반환 주문은 ORDER 레코드용 wire.
    #[allow(clippy::type_complexity)]
    fn route_with_id(
        &mut self,
        decision_id: &str,
        decision: &DecisionWire,
    ) -> PyResult<(
        Vec<OrderWire>,
        Vec<(String, String, String)>,
        Option<RouteError>,
    )> {
        let bars = self.feed_ref()?.current_closes()?;
        let (orders, updates, groups, error) = persistent_router::route_basic_decision(
            &self.portfolio,
            &mut self.orders,
            &self.router_config,
            &mut self.order_seq,
            &mut self.group_seq,
            self.allow_short,
            decision_id,
            decision,
            bars,
        )?;
        if error.is_some() {
            return Ok((Vec::new(), updates, error));
        }
        // 심볼 폴백은 그날 바가 아니라 피드 등록부 전체에서 찾는다 — 바가 끊긴 보유 종목(정지·상폐)의
        // REPLACE 청산 주문이 "instrument metadata is missing" 으로 run 을 죽이지 않도록.
        // 등록부 표는 적재 시 한 번 만들어 두므로 결정마다 재조립하지 않는다.
        let fallback_symbols = self.feed_ref()?.registry_symbols();
        let staged_orders = orders
            .iter()
            .map(|order| {
                StoredOrder::from_routed(
                    order,
                    decision,
                    fallback_symbols.get(&order.1).map(String::as_str),
                    decision_id,
                )
            })
            .collect::<PyResult<Vec<_>>>()?;
        let wires = staged_orders
            .iter()
            .map(|order| self.order_wire(order))
            .collect::<PyResult<Vec<_>>>()?;
        let staged_groups: Vec<StoredGroup> = groups
            .iter()
            .map(|group| StoredGroup {
                group_id: group.0.clone(),
                policy: group.1.clone(),
                order_ids: group.2.clone(),
            })
            .collect();
        self.pending_orders.extend(staged_orders);
        self.pending_groups.extend(staged_groups);
        Ok((wires, updates, None))
    }

    /// `loop._execute` 종료부: 잔여 주문을 취소 레코드로 남기고 스토어를 닫는다.
    pub(crate) fn finish_internal(&mut self) -> PyResult<()> {
        match self.lifecycle {
            Lifecycle::AwaitingDecision(token) => {
                return Err(PyValueError::new_err(format!(
                    "cannot finish while callback awaits a decision — token={token}"
                )))
            }
            Lifecycle::Failed => {
                return Err(PyValueError::new_err(format!(
                    "cannot finish failed persistent runtime — detail={:?}",
                    self.failure_message
                )))
            }
            Lifecycle::Finished => {
                return Err(PyValueError::new_err(
                    "persistent runtime finish called more than once",
                ))
            }
            Lifecycle::Ready | Lifecycle::Running => {}
        }
        // 남은 주문은 결과에서 조용히 사라지지 않도록 취소로 기록한다. 마지막 세션에 낸
        // DAY/IOC/FOK는 "당일 만료", GTC만 "run 종료"가 사유다.
        self.orders.append(&mut self.pending_orders);
        let remaining = std::mem::take(&mut self.orders);
        if !remaining.is_empty() {
            let last = self
                .feed_ref()?
                .session_len()
                .checked_sub(1)
                .ok_or_else(|| {
                    PyValueError::new_err("orders remain but the feed has no sessions")
                })?;
            for order in remaining {
                let reason = if order.tif == "gtc" {
                    "run ended with GTC order still open — ".to_string()
                } else {
                    format!("{} order expired at last session — ", order.tif)
                };
                self.record(
                    last,
                    RecordPayload::OrderUpdate(Box::new(OrderUpdateWire {
                        order_id: order.order_id,
                        status: "cancelled".to_string(),
                        detail: Some(format!(
                            "{reason}instrument={} remaining={}",
                            order.symbol, order.remaining
                        )),
                    })),
                )?;
            }
        }
        self.lifecycle = Lifecycle::Finished;
        self.records.finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::persistent_router::{ExecutionWire, TargetWire};
    use crate::records::{
        KIND_DECISION, KIND_FILL, KIND_MARKET, KIND_ORDER, KIND_ORDER_UPDATE, KIND_SNAPSHOT,
    };

    const KEY: &str = "XKRX:005930:equity:KRW";

    fn settings(warmup: usize) -> RunSettings {
        RunSettings {
            fee_rate: 0.0,
            default_participation: None,
            slippage: ("none".into(), 0.0, 0.0),
            schedule: "every_session".into(),
            short_borrow_bps_annual: 0.0,
            margin_interest_bps_annual: 0.0,
            annualization_days: 252,
            warmup_sessions: warmup,
            notify_fill: false,
            notify_order_update: false,
            notify_corporate_action: false,
        }
    }

    /// 2세션 피드를 실은 runtime.
    ///
    /// 여기서 인터프리터를 올린다 — 드레인 오류를 단언하는 테스트는 실패 경로가
    /// `PyErr::to_string()`으로 detail을 남기므로 PyErr 포맷에 인터프리터가 필요하고,
    /// `cargo test` 바이너리에는 기본적으로 인터프리터가 없다. 호출은 멱등하다.
    fn runtime_with(allow_short: bool, settings: RunSettings) -> PersistentEngine {
        pyo3::prepare_freethreaded_python();
        let mut runtime = PersistentEngine::new(100_000.0, allow_short, false, 1.0).unwrap();
        runtime
            .load_feed(
                vec![KEY.into()],
                vec!["005930".into()],
                vec!["2026-08-01 00:00:00".into(), "2026-08-02 00:00:00".into()],
                vec![0, 1, 2],
                vec![0, 0],
                vec![100.0, 110.0],
                vec![100.0, 120.0],
                vec![100.0, 110.0],
                vec![100.0, 120.0],
                vec![1_000, 1_000],
            )
            .unwrap();
        runtime.configure_router(
            vec!["no_action".into(), "set_portfolio_target".into()],
            vec![],
        );
        runtime.run = Some(Arc::new(settings));
        runtime
    }

    fn runtime(warmup: usize) -> PersistentEngine {
        runtime_with(false, settings(warmup))
    }

    fn no_action(ts: &str) -> DecisionWire {
        (
            1,
            ts.to_string(),
            None,
            vec![("no_action".into(), vec![], None, None, None, vec![])],
        )
    }

    fn weight_target(weight: f64) -> TargetWire {
        (
            "weight".into(),
            KEY.into(),
            "005930".into(),
            "KRW".into(),
            None,
            Some(weight),
            None,
        )
    }

    fn market_next_open() -> ExecutionWire {
        ("market".into(), "next_open".into(), "day".into(), None)
    }

    fn target(ts: &str, weight: f64) -> DecisionWire {
        (
            1,
            ts.to_string(),
            None,
            vec![(
                "set_portfolio_target".into(),
                vec![weight_target(weight)],
                Some("replace".into()),
                Some(market_next_open()),
                None,
                vec![],
            )],
        )
    }

    /// leg 하나짜리 basket — 라우터가 그룹 id 까지 만드는 경로를 탄다.
    fn basket(ts: &str, weight: f64) -> DecisionWire {
        (
            1,
            ts.to_string(),
            None,
            vec![(
                "basket".into(),
                vec![],
                Some("best_effort".into()),
                None,
                None,
                vec![(
                    "set_position_target".into(),
                    Some(weight_target(weight)),
                    Some(market_next_open()),
                    None,
                    None,
                )],
            )],
        )
    }

    fn kinds(runtime: &PersistentEngine) -> Vec<u8> {
        runtime
            .records
            .records()
            .iter()
            .map(|record| record.payload.kind())
            .collect()
    }

    #[test]
    fn drive_returns_one_market_frame_per_scheduled_session_then_none() {
        let mut runtime = runtime(0);
        let first = runtime.drive_internal().unwrap().unwrap();
        assert_eq!(
            (first.token, first.session_index, first.event_kind.as_str()),
            (1, 0, "market")
        );
        assert!(first.event.is_none());
        let (decision_id, error) = runtime
            .submit_internal(first.token, no_action(&first.ts), None)
            .unwrap();
        assert_eq!((decision_id.as_str(), error), ("D-000001", None));
        let second = runtime.drive_internal().unwrap().unwrap();
        assert_eq!((second.token, second.session_index), (2, 1));
        runtime
            .submit_internal(second.token, no_action(&second.ts), None)
            .unwrap();
        assert!(runtime.drive_internal().unwrap().is_none());
        runtime.finish_internal().unwrap();
        assert_eq!(
            kinds(&runtime),
            vec![
                KIND_MARKET,
                KIND_SNAPSHOT,
                KIND_DECISION,
                KIND_MARKET,
                KIND_SNAPSHOT,
                KIND_DECISION,
            ]
        );
        assert_eq!(runtime.lifecycle_state(), "finished");
    }

    #[test]
    fn warmup_skips_callbacks_but_still_records_sessions() {
        let mut runtime = runtime(3);
        assert!(runtime.drive_internal().unwrap().is_none());
        assert_eq!(
            kinds(&runtime),
            vec![KIND_MARKET, KIND_SNAPSHOT, KIND_MARKET, KIND_SNAPSHOT]
        );
    }

    #[test]
    fn orders_are_recorded_after_close_and_filled_next_session() {
        let mut runtime = runtime(0);
        let first = runtime.drive_internal().unwrap().unwrap();
        runtime
            .submit_internal(first.token, target(&first.ts, 0.5), None)
            .unwrap();
        let second = runtime.drive_internal().unwrap().unwrap();
        assert_eq!(second.session_index, 1);
        runtime
            .submit_internal(second.token, no_action(&second.ts), None)
            .unwrap();
        assert!(runtime.drive_internal().unwrap().is_none());
        runtime.finish_internal().unwrap();
        assert_eq!(
            kinds(&runtime),
            vec![
                KIND_MARKET,
                KIND_SNAPSHOT,
                KIND_DECISION,
                KIND_ORDER,
                KIND_MARKET,
                KIND_ORDER_UPDATE,
                KIND_FILL,
                KIND_SNAPSHOT,
                KIND_DECISION,
            ]
        );
        assert_eq!(runtime.records.traded_notional().unwrap(), 500.0 * 110.0);
        assert_eq!(runtime.portfolio.held_qty(KEY), 500);
    }

    #[test]
    fn identifier_prefixes_come_from_the_paths_that_mint_them() {
        // 접두어마다 생성 경로가 다르다 — D는 submit_internal, O·G는 라우터, F는
        // apply_market_ops. 시퀀스 필드와 접두어를 테스트가 직접 짝지으면 코드에서 짝이
        // 어긋나도 통과하므로, 실제 경로가 내놓은 id 를 읽어 단언한다.
        let mut runtime = runtime(0);
        runtime.configure_router(vec!["no_action".into(), "basket".into()], vec![]);

        let first = runtime.drive_internal().unwrap().unwrap();
        let (decision_id, error) = runtime
            .submit_internal(first.token, basket(&first.ts, 0.5), None)
            .unwrap();
        assert_eq!((decision_id.as_str(), error), ("D-000001", None));
        let staged: Vec<(&str, Option<&str>)> = runtime
            .pending_orders
            .iter()
            .map(|order| (order.order_id.as_str(), order.group_id.as_deref()))
            .collect();
        assert_eq!(staged, vec![("O-000001", Some("G-000001"))]);

        let second = runtime.drive_internal().unwrap().unwrap();
        let (next_decision_id, _) = runtime
            .submit_internal(second.token, no_action(&second.ts), None)
            .unwrap();
        assert_eq!(next_decision_id, "D-000002");
        let fills: Vec<(&str, &str)> = runtime
            .records
            .records()
            .iter()
            .filter_map(|record| match &record.payload {
                RecordPayload::Fill(fill) => Some((fill.fill_id.as_str(), fill.order_id.as_str())),
                _ => None,
            })
            .collect();
        assert_eq!(fills, vec![("F-000001", "O-000001")]);
    }

    #[test]
    fn drive_rejects_reentry_while_awaiting_decision() {
        let mut runtime = runtime(0);
        let frame = runtime.drive_internal().unwrap().unwrap();
        // PyErr 메시지 포맷은 GIL이 필요하므로 (cargo test에는 인터프리터가 없다) 실패 여부만
        // 단언한다. 메시지 접두어는 tests/test_rust_driver.py가 고정한다.
        assert!(runtime.drive_internal().is_err());
        assert!(matches!(runtime.lifecycle, Lifecycle::AwaitingDecision(1)));
        assert!(runtime
            .submit_internal(frame.token + 1, no_action(&frame.ts), None)
            .is_err());
        assert!(matches!(runtime.lifecycle, Lifecycle::AwaitingDecision(1)));
    }

    #[test]
    fn month_end_schedule_dispatches_only_on_the_last_session_of_each_month() {
        // GAP-2: `configure_run`이 "month_end"를 받고 `feed.schedule_matches`가 판정하지만
        // 드라이버가 그 판정대로 콜백을 거르는지는 고정된 적이 없었다. Python capability
        // 게이트가 MonthEndSession을 아직 거절하므로 Python 경로와의 대조는 불가능하다.
        let mut runtime = PersistentEngine::new(100_000.0, false, false, 1.0).unwrap();
        runtime
            .load_feed(
                vec![KEY.into()],
                vec!["005930".into()],
                vec![
                    "2026-08-31 00:00:00".into(),
                    "2026-09-01 00:00:00".into(),
                    "2026-09-30 00:00:00".into(),
                ],
                vec![0, 1, 2, 3],
                vec![0, 0, 0],
                vec![100.0, 110.0, 120.0],
                vec![100.0, 110.0, 120.0],
                vec![100.0, 110.0, 120.0],
                vec![100.0, 110.0, 120.0],
                vec![1_000, 1_000, 1_000],
            )
            .unwrap();
        runtime.configure_router(vec!["no_action".into()], vec![]);
        let mut month_end = settings(0);
        month_end.schedule = "month_end".into();
        runtime.run = Some(Arc::new(month_end));

        let mut dispatched = Vec::new();
        while let Some(frame) = runtime.drive_internal().unwrap() {
            dispatched.push(frame.session_index);
            runtime
                .submit_internal(frame.token, no_action(&frame.ts), None)
                .unwrap();
        }
        runtime.finish_internal().unwrap();
        // 08-31은 다음 세션이 9월이라 월말, 09-01은 아니고, 마지막 세션은 뒤가 없어 월말이다.
        assert_eq!(dispatched, vec![0, 2]);
        assert_eq!(
            kinds(&runtime),
            vec![
                KIND_MARKET,
                KIND_SNAPSHOT,
                KIND_DECISION,
                KIND_MARKET,
                KIND_SNAPSHOT,
                KIND_MARKET,
                KIND_SNAPSHOT,
                KIND_DECISION,
            ]
        );
    }

    #[test]
    fn session_close_domain_error_poisons_the_runtime() {
        // DEFECT-R01: 세션 처리 도중 난 도메인 오류도 라우팅 오류처럼 runtime을 실패로 고정해야
        // 한다. 고정하지 않으면 비용은 반영됐지만 SNAPSHOT이 없는 세션 뒤로 같은 runtime이
        // 조용히 이어지고 finish()가 정상 종료해버린다.
        let mut wipeout = settings(0);
        // 숏 차입 이자를 비현실적으로 크게 잡아 세션 1 마감에서 equity를 음수로 만든다.
        wipeout.short_borrow_bps_annual = 1e9;
        let mut runtime = runtime_with(true, wipeout);
        let first = runtime.drive_internal().unwrap().unwrap();
        runtime
            .submit_internal(first.token, target(&first.ts, -0.5), None)
            .unwrap();
        // 세션 1 마감: 차입 비용이 현금을 삼켜 equity_wiped_out.
        assert!(runtime.drive_internal().is_err());
        assert_eq!(runtime.lifecycle_state(), "failed");
        let detail = runtime.failure_message.clone().unwrap();
        assert!(detail.contains("equity_wiped_out:"), "detail={detail}");
        // 재진입과 종료 모두 막혀야 한다.
        assert!(runtime.drive_internal().is_err());
        assert!(runtime.finish_internal().is_err());
        assert_eq!(runtime.lifecycle_state(), "failed");
    }
}
