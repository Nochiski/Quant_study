use crate::buying_power::BuyingPower;
use crate::callback::CallbackFrame;
use crate::driver::{CorporateActionEntry, Queued, RunSettings};
use crate::event_queue::NativeEventQueue;
use crate::feed::PersistentFeed;
use crate::persistent_router::{
    self, CloseWire, DecisionWire, ExecutionWire, RouteError, RoutedGroup, RoutedOrder,
    RoutedUpdate, RouterConfig, TargetWire,
};
use crate::portfolio::{Portfolio, SnapshotTuple};
use crate::quote::parse_decimal_ratio;
use crate::records::{RecordStore, RecordWire};
use crate::session::{self, BarTuple, EntryTuple, Op};
use crate::tape::NativeTape;
use pyo3::exceptions::{PyKeyError, PyValueError};
use pyo3::prelude::*;
use std::collections::HashMap;

#[derive(Clone, Debug)]
pub(crate) enum Lifecycle {
    Ready,
    Running,
    AwaitingDecision(u64),
    Failed,
    Finished,
}

type GroupTuple = (String, String, Vec<String>);
type OrderState = (String, i64, bool);
type CostTuple = (String, Option<String>, f64);
type CorporateActionTuple = (i64, i64, f64, f64, f64);
type RouteResponse = (
    String,
    Vec<RoutedOrder>,
    Vec<RoutedUpdate>,
    Vec<RoutedGroup>,
    Option<RouteError>,
);

fn scaled_corporate_action_quantity(quantity: i64, ratio: &str) -> PyResult<(i64, f64)> {
    const QUANTUM: i128 = 1_000_000_000;
    let (ratio_num, ratio_den) = parse_decimal_ratio(ratio)?;
    if ratio_num <= 0 {
        return Err(PyValueError::new_err(format!(
            "corporate action ratio must be > 0 — ratio={ratio}"
        )));
    }
    let scaled_numerator = i128::from(quantity)
        .checked_mul(ratio_num)
        .and_then(|value| value.checked_mul(QUANTUM))
        .ok_or_else(|| {
            PyValueError::new_err(format!(
                "corporate action quantity overflow — quantity={quantity} ratio={ratio}"
            ))
        })?;
    let mut scaled_units = scaled_numerator / ratio_den;
    let remainder = scaled_numerator % ratio_den;
    let twice_remainder = remainder.abs().checked_mul(2).ok_or_else(|| {
        PyValueError::new_err(format!(
            "corporate action rounding overflow — quantity={quantity} ratio={ratio}"
        ))
    })?;
    if twice_remainder > ratio_den || (twice_remainder == ratio_den && scaled_units.abs() % 2 == 1)
    {
        scaled_units += scaled_numerator.signum();
    }
    let new_quantity = i64::try_from(scaled_units / QUANTUM).map_err(|_| {
        PyValueError::new_err(format!(
            "corporate action quantity outside i64 range — quantity={quantity} ratio={ratio}"
        ))
    })?;
    let fractional_units = scaled_units - i128::from(new_quantity) * QUANTUM;
    Ok((new_quantity, fractional_units as f64 / QUANTUM as f64))
}

#[derive(Clone, Debug)]
pub(crate) struct StoredOrder {
    pub(crate) order_id: String,
    pub(crate) key: String,
    pub(crate) symbol: String,
    pub(crate) side: String,
    pub(crate) order_type: String,
    limit_price: Option<f64>,
    stop_price: Option<f64>,
    pub(crate) limit_text: Option<String>,
    pub(crate) stop_text: Option<String>,
    pub(crate) tif: String,
    /// 최초 주문 수량 (ORDER 레코드용). `remaining`은 체결·정정으로 줄어든다.
    pub(crate) quantity: i64,
    pub(crate) remaining: i64,
    triggered: bool,
    pub(crate) group_id: Option<String>,
    participation: Option<String>,
    /// 결정 추적 메타. 레거시 `place_order` 경로에서는 비어 있다.
    pub(crate) decision_id: String,
    pub(crate) action_index: usize,
    pub(crate) leg_index: Option<usize>,
}

impl StoredOrder {
    fn from_tuple(value: EntryTuple) -> PyResult<Self> {
        if value.7 <= 0 {
            return Err(PyValueError::new_err(format!(
                "order remaining must be > 0 — order_id={} remaining={}",
                value.0, value.7
            )));
        }
        let (limit_price, stop_price, limit_text, stop_text) = value.5;
        Ok(Self {
            order_id: value.0,
            key: value.1,
            symbol: value.2,
            side: value.3,
            order_type: value.4,
            limit_price,
            stop_price,
            limit_text,
            stop_text,
            tif: value.6,
            quantity: value.7,
            remaining: value.7,
            triggered: value.8,
            group_id: value.9,
            participation: value.10,
            decision_id: String::new(),
            action_index: 0,
            leg_index: None,
        })
    }

    fn as_tuple(&self) -> EntryTuple {
        (
            self.order_id.clone(),
            self.key.clone(),
            self.symbol.clone(),
            self.side.clone(),
            self.order_type.clone(),
            (
                self.limit_price,
                self.stop_price,
                self.limit_text.clone(),
                self.stop_text.clone(),
            ),
            self.tif.clone(),
            self.remaining,
            self.triggered,
            self.group_id.clone(),
            self.participation.clone(),
        )
    }

    pub(crate) fn from_routed(
        value: &RoutedOrder,
        decision: &DecisionWire,
        fallback_symbol: Option<&str>,
        decision_id: &str,
    ) -> PyResult<Self> {
        let action = decision.3.get(value.9).ok_or_else(|| {
            PyValueError::new_err(format!(
                "routed order action index out of range — order_id={} action_index={}",
                value.0, value.9
            ))
        })?;
        let (target, participation) = if let Some(leg_index) = value.10 {
            let leg = action.5.get(leg_index).ok_or_else(|| {
                PyValueError::new_err(format!(
                    "routed order basket leg index out of range — order_id={} leg_index={leg_index}",
                    value.0
                ))
            })?;
            let target = leg
                .1
                .as_ref()
                .or_else(|| leg.3.as_ref().map(|request| &request.1));
            let participation = leg.2.as_ref().and_then(|execution| execution.3);
            (target, participation)
        } else {
            let target = action
                .4
                .as_ref()
                .map(|request| &request.1)
                .or_else(|| action.1.iter().find(|target| target.1 == value.1));
            let participation = action.3.as_ref().and_then(|execution| execution.3);
            (target, participation)
        };
        let symbol = target
            .map(|target| target.2.as_str())
            .filter(|symbol| !symbol.is_empty())
            .or(fallback_symbol)
            .ok_or_else(|| {
                PyValueError::new_err(format!(
                    "routed order instrument metadata is missing — order_id={} key={}",
                    value.0, value.1
                ))
            })?;
        let parse_price = |name: &str, text: &Option<String>| -> PyResult<Option<f64>> {
            text.as_ref()
                .map(|raw| {
                    raw.parse::<f64>().map_err(|_| {
                        PyValueError::new_err(format!(
                            "invalid routed order {name} — order_id={} value={raw:?}",
                            value.0
                        ))
                    })
                })
                .transpose()
        };
        Ok(Self {
            order_id: value.0.clone(),
            key: value.1.clone(),
            symbol: symbol.to_string(),
            side: value.3.clone(),
            order_type: value.4.clone(),
            limit_price: parse_price("limit price", &value.5)?,
            stop_price: parse_price("stop price", &value.6)?,
            limit_text: value.5.clone(),
            stop_text: value.6.clone(),
            tif: value.7.clone(),
            quantity: value.2,
            remaining: value.2,
            triggered: false,
            group_id: value.8.clone(),
            participation: participation.map(|value| value.to_string()),
            decision_id: decision_id.to_string(),
            action_index: value.9,
            leg_index: value.10,
        })
    }
}

#[derive(Clone, Debug)]
pub(crate) struct StoredGroup {
    pub(crate) group_id: String,
    pub(crate) policy: String,
    pub(crate) order_ids: Vec<String>,
}

impl StoredGroup {
    fn as_tuple(&self) -> GroupTuple {
        (
            self.group_id.clone(),
            self.policy.clone(),
            self.order_ids.clone(),
        )
    }
}

/// 세션 루프 전체를 소유하는 persistent runtime.
///
/// feed·큐·레코드·주문·그룹·포트폴리오·ID 시퀀스·자본변동 일정이 여기 있고, Python은 전략
/// 콜백(`drive()` → `submit_decision()`)과 결과 조회(`finish()` 배치)에서만 왕복한다.
/// 세션 진행 로직은 `driver.rs`에 있다.
#[pyclass]
pub(crate) struct PersistentEngine {
    pub(crate) portfolio: Portfolio,
    pub(crate) orders: Vec<StoredOrder>,
    pub(crate) groups: Vec<StoredGroup>,
    pub(crate) pending_orders: Vec<StoredOrder>,
    pub(crate) pending_groups: Vec<StoredGroup>,
    pub(crate) event_queue: NativeEventQueue,
    pub(crate) queued: Vec<Option<Queued>>,
    pub(crate) lifecycle: Lifecycle,
    pub(crate) callback_seq: u64,
    pub(crate) awaiting_session: usize,
    pub(crate) started: bool,
    pub(crate) failure_message: Option<String>,
    pub(crate) records: RecordStore,
    pub(crate) decision_seq: u64,
    pub(crate) order_seq: u64,
    pub(crate) fill_seq: u64,
    pub(crate) group_seq: u64,
    pub(crate) leverage: f64,
    pub(crate) allow_short: bool,
    pub(crate) router_config: RouterConfig,
    pub(crate) feed: Option<PersistentFeed>,
    pub(crate) run: Option<RunSettings>,
    pub(crate) corporate_actions: Vec<CorporateActionEntry>,
    pub(crate) ca_by_session: HashMap<usize, Vec<usize>>,
    pub(crate) debug_panic_on_market: bool,
    /// 선언형 tape. 있으면 `drive()`가 콜백을 Python에 넘기지 않고 Rust에서 결정한다.
    pub(crate) tape: Option<NativeTape>,
}

impl PersistentEngine {
    fn order_index(&self, order_id: &str) -> Option<usize> {
        self.orders
            .iter()
            .position(|order| order.order_id == order_id)
    }

    fn require_order_index(&self, order_id: &str) -> PyResult<usize> {
        self.order_index(order_id)
            .ok_or_else(|| PyKeyError::new_err(format!("order not open — order_id={order_id}")))
    }

    fn remove_order_at(&mut self, index: usize) -> StoredOrder {
        self.orders.remove(index)
    }

    fn settle_internal(&mut self, order_id: &str, filled: i64) -> PyResult<i64> {
        let index = self.require_order_index(order_id)?;
        let remaining = self.orders[index].remaining;
        if filled <= 0 || filled > remaining {
            return Err(PyValueError::new_err(format!(
                "fill quantity out of range — order_id={order_id} filled={filled} remaining={remaining}"
            )));
        }
        let new_remaining = remaining - filled;
        if new_remaining == 0 {
            self.remove_order_at(index);
        } else {
            self.orders[index].remaining = new_remaining;
        }
        Ok(new_remaining)
    }

    pub(crate) fn next_id(sequence: &mut u64, prefix: char) -> String {
        *sequence += 1;
        format!("{prefix}-{:06}", *sequence)
    }

    fn group_is_open(&self, group: &StoredGroup) -> bool {
        group
            .order_ids
            .iter()
            .any(|order_id| self.order_index(order_id).is_some())
    }

    fn apply_market_ops(&mut self, ops: &mut [Op]) -> PyResult<()> {
        for op in ops {
            match op.0.as_str() {
                "fill" => {
                    let index = self.require_order_index(&op.1)?;
                    let key = self.orders[index].key.clone();
                    let side = self.orders[index].side.clone();
                    self.portfolio.apply(&key, &side, op.2, op.3, op.5)?;
                    self.settle_internal(&op.1, op.2)?;
                    op.6 = Self::next_id(&mut self.fill_seq, 'F');
                }
                "trigger" => {
                    let index = self.require_order_index(&op.1)?;
                    self.orders[index].triggered = true;
                }
                "remove" => {
                    let index = self.require_order_index(&op.1)?;
                    self.remove_order_at(index);
                }
                "drop_group" => {
                    if let Some(index) = self.groups.iter().position(|group| group.group_id == op.1)
                    {
                        self.groups.remove(index);
                    }
                }
                "update" => {}
                other => {
                    return Err(PyValueError::new_err(format!(
                        "unknown persistent market op — kind={other:?}"
                    )))
                }
            }
        }
        Ok(())
    }

    fn process_market_values(
        &mut self,
        ts: &str,
        bars: HashMap<String, BarTuple>,
        fee_rate: f64,
        default_participation: Option<&str>,
        slippage: (String, f64, f64),
    ) -> PyResult<Vec<Op>> {
        let (_, positions, equity, _) = self.portfolio.snapshot()?;
        let power_positions = positions
            .into_iter()
            .map(|row| (row.0, row.1, row.3, row.4))
            .collect();
        let mut power = BuyingPower::new(equity, self.leverage, power_positions);
        let entries = self.orders.iter().map(StoredOrder::as_tuple).collect();
        let groups = self
            .groups
            .iter()
            .filter(|group| self.group_is_open(group))
            .map(StoredGroup::as_tuple)
            .collect();
        let mut ops = session::process_market_impl(
            ts,
            entries,
            groups,
            bars,
            &mut power,
            fee_rate,
            default_participation,
            slippage,
        )?;
        self.apply_market_ops(&mut ops)?;
        Ok(ops)
    }

    pub(crate) fn activate_pending_internal(&mut self) -> PyResult<()> {
        if let Some(order) = self.pending_orders.iter().find(|pending| {
            self.orders
                .iter()
                .any(|active| active.order_id == pending.order_id)
        }) {
            return Err(PyValueError::new_err(format!(
                "duplicate pending order id — order_id={}",
                order.order_id
            )));
        }
        if let Some(group) = self.pending_groups.iter().find(|pending| {
            self.groups
                .iter()
                .any(|active| active.group_id == pending.group_id)
        }) {
            return Err(PyValueError::new_err(format!(
                "duplicate pending basket group id — group_id={}",
                group.group_id
            )));
        }
        self.orders.append(&mut self.pending_orders);
        self.groups.append(&mut self.pending_groups);
        Ok(())
    }
}

#[pymethods]
impl PersistentEngine {
    #[new]
    #[pyo3(signature = (initial_cash, allow_short=false, allow_margin=false, leverage=1.0))]
    pub(crate) fn new(
        initial_cash: f64,
        allow_short: bool,
        allow_margin: bool,
        leverage: f64,
    ) -> PyResult<Self> {
        if leverage <= 0.0 {
            return Err(PyValueError::new_err(format!(
                "leverage must be > 0 — leverage={leverage}"
            )));
        }
        Ok(Self {
            portfolio: Portfolio::new(initial_cash, allow_short, allow_margin),
            orders: Vec::new(),
            groups: Vec::new(),
            pending_orders: Vec::new(),
            pending_groups: Vec::new(),
            event_queue: NativeEventQueue::default(),
            queued: Vec::new(),
            lifecycle: Lifecycle::Ready,
            callback_seq: 0,
            awaiting_session: 0,
            started: false,
            failure_message: None,
            records: RecordStore::default(),
            decision_seq: 0,
            order_seq: 0,
            fill_seq: 0,
            group_seq: 0,
            leverage,
            allow_short,
            router_config: RouterConfig::default(),
            feed: None,
            run: None,
            corporate_actions: Vec::new(),
            ca_by_session: HashMap::new(),
            debug_panic_on_market: false,
            tape: None,
        })
    }

    /// 선언형 tape 적재: `(session_index, weight targets, scope, execution, reason)` 목록과 idle 사유.
    #[allow(clippy::type_complexity)]
    fn load_target_tape(
        &mut self,
        frames: Vec<(usize, Vec<TargetWire>, String, ExecutionWire, String)>,
        idle_reason: String,
    ) -> PyResult<()> {
        self.load_target_tape_internal(frames, idle_reason)
    }

    /// RunConfig와 requirements에서 온 실행 설정. `drive()` 전에 한 번 호출한다.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (fee_rate, default_participation, slippage, schedule, short_borrow_bps_annual, margin_interest_bps_annual, annualization_days, warmup_sessions, notify_fill, notify_order_update, notify_corporate_action))]
    fn configure_run(
        &mut self,
        fee_rate: f64,
        default_participation: Option<String>,
        slippage: (String, f64, f64),
        schedule: String,
        short_borrow_bps_annual: f64,
        margin_interest_bps_annual: f64,
        annualization_days: u32,
        warmup_sessions: usize,
        notify_fill: bool,
        notify_order_update: bool,
        notify_corporate_action: bool,
    ) -> PyResult<()> {
        if !matches!(schedule.as_str(), "every_session" | "month_end") {
            return Err(PyValueError::new_err(format!(
                "unsupported persistent schedule — schedule={schedule:?}"
            )));
        }
        self.run = Some(RunSettings {
            fee_rate,
            default_participation,
            slippage,
            schedule,
            short_borrow_bps_annual,
            margin_interest_bps_annual,
            annualization_days,
            warmup_sessions,
            notify_fill,
            notify_order_update,
            notify_corporate_action,
        });
        Ok(())
    }

    /// 정산 세션이 확정된 자본변동 목록. 입력 순서가 같은 세션 안의 적용 순서다.
    #[allow(clippy::type_complexity)]
    fn load_corporate_actions(
        &mut self,
        actions: Vec<(usize, String, String, String, String, String, bool)>,
    ) -> PyResult<()> {
        let sessions = self
            .feed
            .as_ref()
            .ok_or_else(|| PyValueError::new_err("persistent feed is not loaded"))?
            .session_len();
        self.corporate_actions.clear();
        self.ca_by_session.clear();
        for (index, (session_index, key, symbol, action_type, ratio, event_ts, confirmed)) in
            actions.into_iter().enumerate()
        {
            if session_index >= sessions {
                return Err(PyValueError::new_err(format!(
                    "corporate action session index out of range — index={session_index} sessions={sessions}"
                )));
            }
            self.corporate_actions.push(CorporateActionEntry {
                key,
                symbol,
                action_type,
                ratio,
                event_ts,
                confirmed,
            });
            self.ca_by_session
                .entry(session_index)
                .or_default()
                .push(index);
        }
        Ok(())
    }

    /// 다음 전략 콜백까지 세션을 진행한다. 콜백이 더 없으면 `None`.
    fn drive(&mut self) -> PyResult<Option<CallbackFrame>> {
        self.drive_internal()
    }

    /// 전략 결정을 라우팅하고 `(decision_id, route_error)`를 돌려준다.
    fn submit_decision(
        &mut self,
        token: u64,
        decision: DecisionWire,
    ) -> PyResult<(String, Option<RouteError>)> {
        self.submit_internal(token, decision, None)
    }

    /// 잔여 주문 취소 기록 후 전체 레코드 배치를 돌려준다.
    fn finish(&mut self, py: Python<'_>) -> PyResult<Vec<RecordWire>> {
        self.finish_internal()?;
        self.records.batch(py)
    }

    /// 종료 여부와 무관한 현재 레코드 배치 (전략 예외 시 partial trace 조회용).
    fn record_batch(&self, py: Python<'_>) -> PyResult<Vec<RecordWire>> {
        self.records.batch(py)
    }

    fn record_count(&self) -> usize {
        self.records.len()
    }

    fn equity_series(&self) -> Vec<f64> {
        self.records.equity_series()
    }

    fn traded_notional(&self) -> f64 {
        self.records.traded_notional()
    }

    #[doc(hidden)]
    fn _debug_force_panic_on_market(&mut self) {
        self.debug_panic_on_market = true;
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn load_feed(
        &mut self,
        keys: Vec<String>,
        symbols: Vec<String>,
        sessions: Vec<String>,
        offsets: Vec<usize>,
        instrument_ids: Vec<u32>,
        opens: Vec<f64>,
        highs: Vec<f64>,
        lows: Vec<f64>,
        closes: Vec<f64>,
        volumes: Vec<i64>,
    ) -> PyResult<()> {
        self.feed = Some(PersistentFeed::new(
            keys,
            symbols,
            sessions,
            offsets,
            instrument_ids,
            opens,
            highs,
            lows,
            closes,
            volumes,
        )?);
        Ok(())
    }

    pub(crate) fn configure_router(&mut self, actions: Vec<String>, features: Vec<String>) {
        self.router_config.configure(actions, features);
    }

    #[pyo3(signature = (decision, bars=None))]
    fn route_basic_decision(
        &mut self,
        decision: DecisionWire,
        bars: Option<HashMap<String, CloseWire>>,
    ) -> PyResult<RouteResponse> {
        let decision_id = Self::next_id(&mut self.decision_seq, 'D');
        let bars = match bars {
            Some(bars) => bars,
            None => self
                .feed
                .as_ref()
                .ok_or_else(|| PyValueError::new_err("persistent feed is not loaded"))?
                .current_closes()?,
        };
        // 심볼 폴백은 그날 바뿐 아니라 피드 등록부 전체에서 찾는다 — 바가 끊긴 보유 종목(정지·상폐)의
        // REPLACE 청산 주문이 "instrument metadata is missing" 으로 run 을 죽이지 않도록.
        let mut fallback_symbols: HashMap<String, String> = self
            .feed
            .as_ref()
            .map(PersistentFeed::registry_symbols)
            .unwrap_or_default();
        for (key, (symbol, _)) in bars.iter() {
            fallback_symbols.insert(key.clone(), symbol.clone());
        }
        let decision_for_orders = decision.clone();
        let (orders, updates, groups, error) = persistent_router::route_basic_decision(
            &self.portfolio,
            &mut self.orders,
            &self.router_config,
            &mut self.order_seq,
            &mut self.group_seq,
            self.allow_short,
            &decision_id,
            decision,
            bars,
        )?;
        if error.is_none() {
            let staged_orders = orders
                .iter()
                .map(|order| {
                    StoredOrder::from_routed(
                        order,
                        &decision_for_orders,
                        fallback_symbols.get(&order.1).map(String::as_str),
                        &decision_id,
                    )
                })
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
        }
        Ok((decision_id, orders, updates, groups, error))
    }

    fn fail_callback(&mut self, token: u64, detail: String) -> PyResult<()> {
        match self.lifecycle {
            Lifecycle::AwaitingDecision(expected) if token == expected => {
                self.lifecycle = Lifecycle::Failed;
                self.failure_message = Some(detail);
                Ok(())
            }
            Lifecycle::AwaitingDecision(expected) => Err(PyValueError::new_err(format!(
                "stale callback token while failing — expected={expected} got={token}"
            ))),
            _ => Err(PyValueError::new_err(format!(
                "cannot fail callback from lifecycle state {} — token={token}",
                self.lifecycle_state()
            ))),
        }
    }

    pub(crate) fn lifecycle_state(&self) -> &'static str {
        match self.lifecycle {
            Lifecycle::Ready => "ready",
            Lifecycle::Running => "running",
            Lifecycle::AwaitingDecision(_) => "awaiting_decision",
            Lifecycle::Failed => "failed",
            Lifecycle::Finished => "finished",
        }
    }

    #[getter]
    fn failure_detail(&self) -> Option<String> {
        self.failure_message.clone()
    }

    fn poison(&mut self, detail: String) {
        self.lifecycle = Lifecycle::Failed;
        self.failure_message = Some(detail);
    }

    fn activate_pending(&mut self) -> PyResult<()> {
        self.activate_pending_internal()
    }

    fn next_decision_id(&mut self) -> String {
        Self::next_id(&mut self.decision_seq, 'D')
    }

    fn next_order_id(&mut self) -> String {
        Self::next_id(&mut self.order_seq, 'O')
    }

    fn next_fill_id(&mut self) -> String {
        Self::next_id(&mut self.fill_seq, 'F')
    }

    fn next_group_id(&mut self) -> String {
        Self::next_id(&mut self.group_seq, 'G')
    }

    fn place_order(&mut self, order: EntryTuple) -> PyResult<()> {
        let order = StoredOrder::from_tuple(order)?;
        if self.order_index(&order.order_id).is_some() {
            return Err(PyValueError::new_err(format!(
                "duplicate order id — order_id={}",
                order.order_id
            )));
        }
        self.orders.push(order);
        Ok(())
    }

    fn register_group(
        &mut self,
        group_id: String,
        policy: String,
        order_ids: Vec<String>,
    ) -> PyResult<()> {
        if self.groups.iter().any(|group| group.group_id == group_id) {
            return Err(PyValueError::new_err(format!(
                "duplicate basket group id — group_id={group_id}"
            )));
        }
        self.groups.push(StoredGroup {
            group_id,
            policy,
            order_ids,
        });
        Ok(())
    }

    fn open_order_states(&self) -> Vec<OrderState> {
        self.orders
            .iter()
            .map(|order| (order.order_id.clone(), order.remaining, order.triggered))
            .collect()
    }

    fn open_group_states(&self) -> Vec<GroupTuple> {
        self.groups
            .iter()
            .filter(|group| self.group_is_open(group))
            .map(StoredGroup::as_tuple)
            .collect()
    }

    fn drop_group(&mut self, group_id: &str) {
        if let Some(index) = self
            .groups
            .iter()
            .position(|group| group.group_id == group_id)
        {
            self.groups.remove(index);
        }
    }

    fn remove_order(&mut self, order_id: &str) -> PyResult<OrderState> {
        let index = self.require_order_index(order_id)?;
        let order = self.remove_order_at(index);
        Ok((order.order_id, order.remaining, order.triggered))
    }

    fn settle_order(&mut self, order_id: &str, filled: i64) -> PyResult<i64> {
        self.settle_internal(order_id, filled)
    }

    fn mark_triggered(&mut self, order_id: &str) -> PyResult<()> {
        let index = self.require_order_index(order_id)?;
        self.orders[index].triggered = true;
        Ok(())
    }

    pub(crate) fn cancel_for_key(&mut self, key: &str) -> Vec<String> {
        let mut cancelled = Vec::new();
        self.orders.retain(|order| {
            if order.key == key {
                cancelled.push(order.order_id.clone());
                false
            } else {
                true
            }
        });
        cancelled
    }

    fn drain_orders(&mut self) -> Vec<OrderState> {
        self.orders.append(&mut self.pending_orders);
        self.orders
            .drain(..)
            .map(|order| (order.order_id, order.remaining, order.triggered))
            .collect()
    }

    #[pyo3(signature = (ts, bars, fee_rate, default_participation, slippage))]
    fn process_market(
        &mut self,
        ts: &str,
        bars: HashMap<String, BarTuple>,
        fee_rate: f64,
        default_participation: Option<&str>,
        slippage: (String, f64, f64),
    ) -> PyResult<Vec<Op>> {
        self.process_market_values(ts, bars, fee_rate, default_participation, slippage)
    }

    #[pyo3(signature = (session_index, fee_rate, default_participation, slippage))]
    pub(crate) fn process_market_index(
        &mut self,
        session_index: usize,
        fee_rate: f64,
        default_participation: Option<&str>,
        slippage: (String, f64, f64),
    ) -> PyResult<Vec<Op>> {
        let (ts, bars) = {
            let feed = self
                .feed
                .as_mut()
                .ok_or_else(|| PyValueError::new_err("persistent feed is not loaded"))?;
            feed.set_current(session_index)?;
            feed.session_market(session_index)
        };
        self.process_market_values(&ts, bars, fee_rate, default_participation, slippage)
    }

    fn mark_current_session(&mut self) -> PyResult<()> {
        let marks = self
            .feed
            .as_ref()
            .ok_or_else(|| PyValueError::new_err("persistent feed is not loaded"))?
            .current_marks()?;
        self.portfolio.mark(marks);
        Ok(())
    }

    pub(crate) fn close_current_session(
        &mut self,
        schedule: &str,
        short_borrow_bps_annual: f64,
        margin_interest_bps_annual: f64,
        annualization_days: u32,
    ) -> PyResult<(bool, Vec<CostTuple>, SnapshotTuple)> {
        if short_borrow_bps_annual < 0.0 || margin_interest_bps_annual < 0.0 {
            return Err(PyValueError::new_err(format!(
                "annual cost rates must be >= 0 — short_borrow_bps_annual={short_borrow_bps_annual} margin_interest_bps_annual={margin_interest_bps_annual}"
            )));
        }
        if annualization_days == 0 {
            return Err(PyValueError::new_err(
                "annualization_days must be > 0 — annualization_days=0",
            ));
        }
        let feed = self
            .feed
            .as_ref()
            .ok_or_else(|| PyValueError::new_err("persistent feed is not loaded"))?;
        let should_dispatch = feed.schedule_matches(schedule)?;
        let marks = feed.current_marks()?;
        self.portfolio.mark(marks);
        let (cash, positions, _, _) = self.portfolio.snapshot()?;
        let borrow_daily = short_borrow_bps_annual / 10_000.0 / f64::from(annualization_days);
        let mut costs = Vec::new();
        if borrow_daily > 0.0 {
            for position in &positions {
                if position.1 < 0 {
                    let amount = position.4.abs() * borrow_daily;
                    if amount > 0.0 {
                        costs.push(("short_borrow".to_string(), Some(position.0.clone()), amount));
                    }
                }
            }
        }
        let interest_daily = margin_interest_bps_annual / 10_000.0 / f64::from(annualization_days);
        if interest_daily > 0.0 && cash < 0.0 {
            let amount = -cash * interest_daily;
            if amount > 0.0 {
                costs.push(("margin_interest".to_string(), None, amount));
            }
        }
        for cost in &costs {
            self.portfolio.charge(cost.2)?;
        }
        Ok((should_dispatch, costs, self.portfolio.snapshot()?))
    }

    fn current_session_count(&self) -> PyResult<usize> {
        Ok(self
            .feed
            .as_ref()
            .ok_or_else(|| PyValueError::new_err("persistent feed is not loaded"))?
            .current_session_count())
    }

    fn settlement_session_index(&self, key: &str, event_ts: &str) -> PyResult<Option<usize>> {
        Ok(self
            .feed
            .as_ref()
            .ok_or_else(|| PyValueError::new_err("persistent feed is not loaded"))?
            .settlement_session_index(key, event_ts))
    }

    fn history_window(
        &self,
        keys: Vec<String>,
        field: &str,
        lookback: usize,
        end: &str,
    ) -> PyResult<(Vec<String>, Vec<f64>)> {
        self.feed
            .as_ref()
            .ok_or_else(|| PyValueError::new_err("persistent feed is not loaded"))?
            .history_window(&keys, field, lookback, end)
    }

    fn apply_fill(
        &mut self,
        key: &str,
        side: &str,
        quantity: i64,
        price: f64,
        fee: f64,
    ) -> PyResult<()> {
        self.portfolio.apply(key, side, quantity, price, fee)
    }

    fn charge(&mut self, amount: f64) -> PyResult<()> {
        self.portfolio.charge(amount)
    }

    fn apply_corporate_action(
        &mut self,
        key: &str,
        new_quantity: i64,
        new_average_price: f64,
        cash_paid: f64,
        settlement_price: f64,
    ) -> PyResult<()> {
        self.portfolio.apply_corporate_action(
            key,
            new_quantity,
            new_average_price,
            cash_paid,
            settlement_price,
        )
    }

    pub(crate) fn apply_corporate_action_ratio(
        &mut self,
        key: &str,
        ratio: &str,
        settlement_price: f64,
    ) -> PyResult<Option<CorporateActionTuple>> {
        let old_quantity = self.portfolio.held_qty(key);
        if old_quantity == 0 {
            return Ok(None);
        }
        if settlement_price <= 0.0 {
            return Err(PyValueError::new_err(format!(
                "corporate action settlement price must be > 0 — key={key} price={settlement_price}"
            )));
        }
        let old_average = self.portfolio.average_price(key).ok_or_else(|| {
            PyValueError::new_err(format!("portfolio quantity has no ledger — key={key}"))
        })?;
        let ratio_value = ratio.parse::<f64>().map_err(|_| {
            PyValueError::new_err(format!("invalid corporate action ratio — ratio={ratio:?}"))
        })?;
        let (new_quantity, fractional) = scaled_corporate_action_quantity(old_quantity, ratio)?;
        let cash_paid = fractional * settlement_price;
        let new_average = old_average / ratio_value;
        self.portfolio.apply_corporate_action(
            key,
            new_quantity,
            new_average,
            cash_paid,
            settlement_price,
        )?;
        Ok(Some((
            old_quantity,
            new_quantity,
            old_average,
            new_average,
            cash_paid,
        )))
    }

    fn mark(&mut self, closes: Vec<(String, f64)>) {
        self.portfolio.mark(closes);
    }

    #[getter]
    fn cash(&self) -> f64 {
        self.portfolio.cash()
    }

    fn held_qty(&self, key: &str) -> i64 {
        self.portfolio.held_qty(key)
    }

    fn average_price(&self, key: &str) -> Option<f64> {
        self.portfolio.average_price(key)
    }

    fn portfolio_snapshot(&self) -> PyResult<SnapshotTuple> {
        self.portfolio.snapshot()
    }
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PersistentEngine>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn market_order(order_id: &str) -> EntryTuple {
        (
            order_id.to_string(),
            "X:ONE:equity:KRW".to_string(),
            "ONE".to_string(),
            "buy".to_string(),
            "market".to_string(),
            (None, None, None, None),
            "day".to_string(),
            10,
            false,
            None,
            None,
        )
    }

    #[test]
    fn corporate_action_quantity_matches_decimal_quantize_then_truncate() {
        let thirds = "0.3333333333333333333333333333";
        assert_eq!(
            scaled_corporate_action_quantity(30, thirds).unwrap(),
            (10, 0.0)
        );
        assert_eq!(
            scaled_corporate_action_quantity(-7, "1.5").unwrap(),
            (-10, -0.5)
        );
    }

    #[test]
    fn identifiers_are_deterministic() {
        let mut runtime = PersistentEngine::new(10_000.0, false, false, 1.0).unwrap();
        assert_eq!(runtime.next_decision_id(), "D-000001");
        assert_eq!(runtime.next_order_id(), "O-000001");
        assert_eq!(runtime.next_fill_id(), "F-000001");
        assert_eq!(runtime.next_group_id(), "G-000001");
    }

    #[test]
    fn market_processing_mutates_persistent_order_state() {
        let mut runtime = PersistentEngine::new(10_000.0, false, false, 1.0).unwrap();
        runtime.place_order(market_order("O-000001")).unwrap();
        let bars = HashMap::from([("X:ONE:equity:KRW".to_string(), (100.0, 110.0, 90.0, 1_000))]);

        let ops = runtime
            .process_market(
                "2026-01-02 00:00:00",
                bars,
                0.0,
                None,
                ("none".into(), 0.0, 0.0),
            )
            .unwrap();

        assert!(runtime.open_order_states().is_empty());
        assert_eq!(ops[0].0, "fill");
        assert_eq!(ops[0].6, "F-000001");
        assert_eq!(runtime.portfolio.held_qty("X:ONE:equity:KRW"), 10);
        assert_eq!(runtime.portfolio.cash(), 9_000.0);
    }
}
