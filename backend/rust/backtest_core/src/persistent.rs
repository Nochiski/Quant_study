use crate::buying_power::BuyingPower;
use crate::callback::CallbackFrame;
use crate::driver::{CorporateActionEntry, Queued, RunSettings};
use crate::event_queue::NativeEventQueue;
use crate::feed::PersistentFeed;
use crate::persistent_router::{
    DecisionWire, ExecutionWire, RouteError, RoutedOrder, RouterConfig, TargetWire,
};
use crate::portfolio::Portfolio;
use crate::quote::parse_decimal_ratio;
use crate::records::{RecordIndexWire, RecordStore};
use crate::session::{self, BarTuple, EntryTuple, Op};
use crate::tape::NativeTape;
use pyo3::exceptions::{PyKeyError, PyValueError};
use pyo3::prelude::*;
use std::collections::HashMap;
use std::sync::Arc;

#[derive(Clone, Debug)]
pub(crate) enum Lifecycle {
    Ready,
    Running,
    AwaitingDecision(u64),
    Failed,
    Finished,
}

type GroupTuple = (String, String, Vec<String>);
type CostTuple = (String, Option<String>, f64);
type CorporateActionTuple = (i64, i64, f64, f64, f64);

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
    /// 결정 추적 메타. 드라이버가 결정을 주문으로 풀 때 채운다.
    pub(crate) decision_id: String,
    pub(crate) action_index: usize,
    pub(crate) leg_index: Option<usize>,
}

impl StoredOrder {
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
    /// 큐 payload arena. 힙 엔트리는 이 Vec의 index(token)만 들고 다닌다.
    pub(crate) queued: Vec<Option<Queued>>,
    /// `pop`이 payload를 가져가 비운 arena 자리. `push`가 여기서 먼저 꺼내 쓴다.
    pub(crate) free_slots: Vec<usize>,
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
    pub(crate) run: Option<Arc<RunSettings>>,
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

    /// MARKET 처리 계획만 세운다 — 상태를 바꾸지 않으므로 `&self`다.
    ///
    /// 적용(`apply_market_ops`)과 나눠 둔 이유는 빌림이다. `bars`가 피드를 빌린 채
    /// 들어오므로, 계획 단계까지 `&mut self`를 잡으면 같은 `self`의 피드 빌림과 겹친다.
    fn plan_market_ops(
        &self,
        ts: &str,
        bars: &HashMap<&str, BarTuple>,
        fee_rate: f64,
        default_participation: Option<&str>,
        slippage: &(String, f64, f64),
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
        session::process_market_impl(
            ts,
            entries,
            groups,
            bars,
            &mut power,
            fee_rate,
            default_participation,
            slippage,
        )
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

    pub(crate) fn process_market_index(
        &mut self,
        session_index: usize,
        fee_rate: f64,
        default_participation: Option<&str>,
        slippage: &(String, f64, f64),
    ) -> PyResult<Vec<Op>> {
        self.feed
            .as_mut()
            .ok_or_else(|| PyValueError::new_err("persistent feed is not loaded"))?
            .set_current(session_index)?;
        // 계획 단계는 피드를 빌린 ts·bars를 그대로 읽는다 — 둘 다 `&self`라 겹치지 않는다.
        let mut ops = {
            let feed = self
                .feed
                .as_ref()
                .ok_or_else(|| PyValueError::new_err("persistent feed is not loaded"))?;
            let (ts, bars) = feed.session_market(session_index);
            self.plan_market_ops(ts, &bars, fee_rate, default_participation, slippage)?
        };
        self.apply_market_ops(&mut ops)?;
        Ok(ops)
    }

    pub(crate) fn close_current_session(
        &mut self,
        schedule: &str,
        short_borrow_bps_annual: f64,
        margin_interest_bps_annual: f64,
        annualization_days: u32,
    ) -> PyResult<(bool, Vec<CostTuple>)> {
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
        self.portfolio.mark_refs(&marks);
        // 비용 계산은 key를 읽기만 하므로 원장에서 빌린다 — 실제 String이 필요한 것은
        // 레코드로 나가는 공매도 차입 비용뿐이라, 세션마다 포지션 수만큼 나던 복제가
        // 공매도 포지션 수만큼으로 줄어든다.
        let (cash, positions, _, _) = self.portfolio.snapshot_refs()?;
        let borrow_daily = short_borrow_bps_annual / 10_000.0 / f64::from(annualization_days);
        let mut costs = Vec::new();
        if borrow_daily > 0.0 {
            for position in &positions {
                if position.1 < 0 {
                    let amount = position.4.abs() * borrow_daily;
                    if amount > 0.0 {
                        costs.push((
                            "short_borrow".to_string(),
                            Some(position.0.to_string()),
                            amount,
                        ));
                    }
                }
            }
        }
        drop(positions);
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
        // 마감 스냅샷은 호출부가 필요할 때 직접 만든다 — 여기서 만들어 돌려주면 key를
        // 소유해야 하고, 드라이버는 그 key를 instrument id로 바꾼 뒤 바로 버린다.
        Ok((should_dispatch, costs))
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
            free_slots: Vec::new(),
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
        self.run = Some(Arc::new(RunSettings {
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
        }));
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

    /// 잔여 주문 취소 기록 후 레코드 인덱스 `(seq, session_index, kind)`를 돌려준다.
    /// 큐 arena는 더 쓰지 않으므로 여기서 해제한다.
    fn finish(&mut self) -> PyResult<Vec<RecordIndexWire>> {
        self.finish_internal()?;
        self.queued = Vec::new();
        self.free_slots = Vec::new();
        // tape 프레임은 결정 생성에만 쓰였다 — 재구성 정보는 DECISION 레코드에 있다.
        self.tape = None;
        self.event_queue = NativeEventQueue::default();
        Ok(self.records.index())
    }

    /// 종료 여부와 무관한 현재 레코드 인덱스 (전략 예외 시 partial trace 조회용).
    fn record_batch(&self) -> PyResult<Vec<RecordIndexWire>> {
        self.records.index_for_trace()
    }

    /// kind 하나의 `(seq, session_index, payload)`를 seq 순서로 한 번에 돌려준다.
    /// 종료 전 partial trace에서도 동작한다 — 그 시점까지 쌓인 레코드만 답한다.
    fn record_payloads(&self, py: Python<'_>, kind: u8) -> PyResult<Vec<(u64, usize, PyObject)>> {
        self.records.payloads_of(py, kind)
    }

    /// kind 하나의 payload를 `limit`개까지 넘기면서 그 자리를 해제한다. 빈 목록이면 끝이다.
    ///
    /// 호출 순서 계약: Python은 `finish()` 직후 `equity_series`/`traded_notional`로 metrics를
    /// 먼저 계산하고, 그 뒤 결과 조회에서만 kind를 넘겨받는다. 넘긴 payload를
    /// `record_payloads`·`equity_series`·`traded_notional`로 다시 읽으면
    /// 오류다. 모든 레코드를 넘기면 인덱스까지 돌려주므로 `record_batch`도 오류가 된다
    /// (인덱스는 `finish()`가 이미 Python에 넘겼다).
    fn drain_payloads(
        &mut self,
        py: Python<'_>,
        kind: u8,
        limit: usize,
    ) -> PyResult<Vec<(u64, usize, PyObject)>> {
        self.records.drain(py, kind, limit)
    }

    /// 결과 집계용 columnar 테이블
    /// `(snapshot_rows, position_rows, order_rows, fill_rows, cost_rows, fill_totals)`.
    ///
    /// 레코드를 한 번 순회해 primitive 행만 만든다 — 행 원소의 의미는 Python
    /// `backtest_engine/types/result_tables.py`가 정본이다. 비파괴 조회라 payload를 해제하지
    /// 않으므로 이후 `drain_payloads`로 같은 레코드를 공개 Event 객체로 다시 읽을 수 있다.
    fn result_tables(&self, py: Python<'_>) -> PyResult<PyObject> {
        self.records.result_tables(py)
    }

    fn equity_series(&self) -> PyResult<Vec<f64>> {
        self.records.equity_series()
    }

    fn traded_notional(&self) -> PyResult<f64> {
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
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PersistentEngine>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn market_order(order_id: &str) -> StoredOrder {
        StoredOrder {
            order_id: order_id.to_string(),
            key: "X:ONE:equity:KRW".to_string(),
            symbol: "ONE".to_string(),
            side: "buy".to_string(),
            order_type: "market".to_string(),
            limit_price: None,
            stop_price: None,
            limit_text: None,
            stop_text: None,
            tif: "day".to_string(),
            quantity: 10,
            remaining: 10,
            triggered: false,
            group_id: None,
            participation: None,
            decision_id: String::new(),
            action_index: 0,
            leg_index: None,
        }
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
    fn market_processing_mutates_persistent_order_state() {
        let mut runtime = PersistentEngine::new(10_000.0, false, false, 1.0).unwrap();
        runtime.orders.push(market_order("O-000001"));
        let bars = HashMap::from([("X:ONE:equity:KRW", (100.0, 110.0, 90.0, 1_000))]);

        let mut ops = runtime
            .plan_market_ops(
                "2026-01-02 00:00:00",
                &bars,
                0.0,
                None,
                &("none".into(), 0.0, 0.0),
            )
            .unwrap();
        runtime.apply_market_ops(&mut ops).unwrap();

        assert!(runtime.orders.is_empty());
        assert_eq!(ops[0].0, "fill");
        assert_eq!(ops[0].6, "F-000001");
        assert_eq!(runtime.portfolio.held_qty("X:ONE:equity:KRW"), 10);
        assert_eq!(runtime.portfolio.cash(), 9_000.0);
    }
}
