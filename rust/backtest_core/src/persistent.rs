use crate::buying_power::BuyingPower;
use crate::feed::PersistentFeed;
use crate::persistent_router::{
    self, CloseWire, DecisionWire, RouteError, RoutedGroup, RoutedOrder, RoutedUpdate, RouterConfig,
};
use crate::portfolio::{Portfolio, SnapshotTuple};
use crate::session::{self, BarTuple, EntryTuple, Op};
use pyo3::exceptions::{PyKeyError, PyValueError};
use pyo3::prelude::*;
use std::collections::HashMap;

type GroupTuple = (String, String, Vec<String>);
type OrderState = (String, i64, bool);
type RouteResponse = (
    String,
    Vec<RoutedOrder>,
    Vec<RoutedUpdate>,
    Vec<RoutedGroup>,
    Option<RouteError>,
);

#[derive(Clone, Debug)]
pub(crate) struct StoredOrder {
    pub(crate) order_id: String,
    pub(crate) key: String,
    symbol: String,
    pub(crate) side: String,
    order_type: String,
    limit_price: Option<f64>,
    stop_price: Option<f64>,
    limit_text: Option<String>,
    stop_text: Option<String>,
    tif: String,
    pub(crate) remaining: i64,
    triggered: bool,
    group_id: Option<String>,
    participation: Option<String>,
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
            remaining: value.7,
            triggered: value.8,
            group_id: value.9,
            participation: value.10,
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
}

#[derive(Clone, Debug)]
struct StoredGroup {
    group_id: String,
    policy: String,
    order_ids: Vec<String>,
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

/// 세션 사이에 포트폴리오·대기 주문·그룹·식별자 시퀀스를 유지하는 첫 persistent runtime.
///
/// 전략·Router·EventQueue는 M1 동안 Python에 남고, mutable 주문 상태의 단일 진실 원천만
/// 이 객체로 옮긴다. `process_market`에는 Bar만 전달하며 open entries/groups는 내부 상태를 쓴다.
#[pyclass]
pub(crate) struct PersistentEngine {
    portfolio: Portfolio,
    orders: Vec<StoredOrder>,
    groups: Vec<StoredGroup>,
    decision_seq: u64,
    order_seq: u64,
    fill_seq: u64,
    group_seq: u64,
    leverage: f64,
    allow_short: bool,
    router_config: RouterConfig,
    feed: Option<PersistentFeed>,
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

    fn next_id(sequence: &mut u64, prefix: char) -> String {
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
        let mut ops = session::process_market(
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
}

#[pymethods]
impl PersistentEngine {
    #[new]
    #[pyo3(signature = (initial_cash, allow_short=false, allow_margin=false, leverage=1.0))]
    fn new(
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
            decision_seq: 0,
            order_seq: 0,
            fill_seq: 0,
            group_seq: 0,
            leverage,
            allow_short,
            router_config: RouterConfig::default(),
            feed: None,
        })
    }

    #[allow(clippy::too_many_arguments)]
    fn load_feed(
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

    fn configure_router(&mut self, actions: Vec<String>, features: Vec<String>) {
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
        Ok((decision_id, orders, updates, groups, error))
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

    fn cancel_for_key(&mut self, key: &str) -> Vec<String> {
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
    fn process_market_index(
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

    fn current_session_count(&self) -> PyResult<usize> {
        Ok(self
            .feed
            .as_ref()
            .ok_or_else(|| PyValueError::new_err("persistent feed is not loaded"))?
            .current_session_count())
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
