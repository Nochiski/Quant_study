use crate::persistent::StoredOrder;
use crate::portfolio::Portfolio;
use crate::quote::parse_decimal_ratio;
use pyo3::prelude::*;
use std::collections::{HashMap, HashSet};

pub(crate) type ExecutionWire = (String, String, String, Option<f64>);
pub(crate) type TargetWire = (
    String,
    String,
    String,
    String,
    Option<String>,
    Option<f64>,
    Option<String>,
);
pub(crate) type RequestWire = (
    String,
    TargetWire,
    String,
    String,
    String,
    Option<String>,
    Option<String>,
);
pub(crate) type BasketLegWire = (
    String,
    Option<TargetWire>,
    Option<ExecutionWire>,
    Option<RequestWire>,
    Option<String>,
);
pub(crate) type ActionWire = (
    String,
    Vec<TargetWire>,
    Option<String>,
    Option<ExecutionWire>,
    Option<RequestWire>,
    Vec<BasketLegWire>,
);
pub(crate) type DecisionWire = (i64, String, Option<String>, Vec<ActionWire>);
pub(crate) type CloseWire = (String, f64);
pub(crate) type RoutedOrder = (
    String,
    String,
    i64,
    String,
    String,
    Option<String>,
    Option<String>,
    String,
    Option<String>,
    usize,
    Option<usize>,
);
pub(crate) type RoutedUpdate = (String, String, String);
pub(crate) type RoutedGroup = (String, String, Vec<String>);
pub(crate) type RouteError = (String, String);
type RoutedDecision = (
    Vec<RoutedOrder>,
    Vec<RoutedUpdate>,
    Vec<RoutedGroup>,
    Option<RouteError>,
);

#[derive(Default)]
pub(crate) struct RouterConfig {
    declared_actions: HashSet<String>,
    declared_features: HashSet<String>,
}

impl RouterConfig {
    pub(crate) fn configure(&mut self, actions: Vec<String>, features: Vec<String>) {
        self.declared_actions = actions.into_iter().collect();
        self.declared_features = features.into_iter().collect();
    }

    fn action_declared(&self, action: &str) -> bool {
        self.declared_actions.contains(action)
    }

    fn feature_declared(&self, feature: &str) -> bool {
        self.declared_features.contains(feature)
    }

    fn sorted_actions(&self) -> Vec<&str> {
        let mut values: Vec<_> = self.declared_actions.iter().map(String::as_str).collect();
        values.sort_unstable();
        values
    }

    fn sorted_features(&self) -> Vec<&str> {
        let mut values: Vec<_> = self.declared_features.iter().map(String::as_str).collect();
        values.sort_unstable();
        values
    }
}

struct RouteContext<'a> {
    decision_id: &'a str,
    portfolio_positions: &'a [(String, i64, f64, f64, f64, f64)],
    bars: &'a HashMap<String, CloseWire>,
    orders: &'a mut Vec<StoredOrder>,
    config: &'a RouterConfig,
    allow_short: bool,
    order_seq: &'a mut u64,
    group_seq: &'a mut u64,
    routed_sells: HashMap<String, i64>,
    updates: Vec<RoutedUpdate>,
    groups: Vec<RoutedGroup>,
}

impl RouteContext<'_> {
    fn held(&self, key: &str) -> i64 {
        self.portfolio_positions
            .iter()
            .find(|row| row.0 == key)
            .map(|row| row.1)
            .unwrap_or(0)
    }

    fn market_value(&self, key: &str) -> f64 {
        self.portfolio_positions
            .iter()
            .find(|row| row.0 == key)
            .map(|row| row.4)
            .unwrap_or(0.0)
    }

    fn open_sell_quantity(&self, key: &str) -> i64 {
        self.orders
            .iter()
            .filter(|order| order.key == key && order.side == "sell")
            .map(|order| order.remaining)
            .sum()
    }

    fn sellable(&self, key: &str) -> i64 {
        self.held(key)
            - self.open_sell_quantity(key)
            - self.routed_sells.get(key).copied().unwrap_or(0)
    }

    fn error(&self, code: &str, message: String) -> RouteError {
        (code.to_string(), message)
    }

    fn check_execution(&self, execution: &ExecutionWire) -> Result<(), RouteError> {
        if execution.0 != "market" {
            return Err(self.error(
                "unsupported_action_value",
                format!(
                    "execution style not implemented in v1 — style={} decision_id={}, only MARKET is supported",
                    execution.0, self.decision_id
                ),
            ));
        }
        if let Some(participation) = execution.3 {
            if !self.config.feature_declared("partial_fill") {
                return Err(self.error(
                    "undeclared_feature",
                    format!(
                        "max_participation={participation} requires a feature not declared in requirements() — feature=partial_fill declared={:?} decision_id={}",
                        self.config.sorted_features(),
                        self.decision_id
                    ),
                ));
            }
            if !(0.0 < participation && participation <= 1.0) {
                return Err(self.error(
                    "unsupported_action_value",
                    format!(
                        "max_participation must be in (0, 1] — max_participation={participation} decision_id={}",
                        self.decision_id
                    ),
                ));
            }
        }
        Ok(())
    }

    fn require_feature(&self, feature: &str, what: String) -> Result<(), RouteError> {
        if self.config.feature_declared(feature) {
            return Ok(());
        }
        Err(self.error(
            "undeclared_feature",
            format!(
                "{what} requires a feature not declared in requirements() — feature={feature} declared={:?} decision_id={}",
                self.config.sorted_features(),
                self.decision_id
            ),
        ))
    }

    fn close(&self, target: &TargetWire) -> Result<f64, RouteError> {
        self.bars.get(&target.1).map(|bar| bar.1).ok_or_else(|| {
            let available: Vec<_> = self.bars.values().map(|bar| bar.0.as_str()).collect();
            self.error(
                "instrument_not_snapshot",
                format!(
                    "instrument not in snapshot — requested={} available={available:?}",
                    target.2
                ),
            )
        })
    }

    fn integer(&self, target: &TargetWire) -> Result<i64, RouteError> {
        let text = target.6.as_deref().ok_or_else(|| {
            self.error(
                "value_error",
                format!("quantity wire is missing — instrument={}", target.2),
            )
        })?;
        let (numerator, denominator) = parse_decimal_ratio(text).map_err(|error| {
            self.error(
                "unsupported_action_value",
                format!(
                    "fractional shares not supported — quantity must be an integer, got {text} instrument={} decision_id={} ({error})",
                    target.2, self.decision_id
                ),
            )
        })?;
        if numerator % denominator != 0 {
            return Err(self.error(
                "unsupported_action_value",
                format!(
                    "fractional shares not supported — quantity must be an integer, got {text} instrument={} decision_id={}",
                    target.2, self.decision_id
                ),
            ));
        }
        i64::try_from(numerator / denominator).map_err(|_| {
            self.error(
                "unsupported_action_value",
                format!(
                    "quantity is outside the Rust integer range — quantity={text} instrument={} decision_id={}",
                    target.2, self.decision_id
                ),
            )
        })
    }

    fn target_delta(&self, target: &TargetWire, equity: f64) -> Result<(i64, bool), RouteError> {
        let held = self.held(&target.1);
        match target.0.as_str() {
            "weight" => {
                let weight = target.5.ok_or_else(|| {
                    self.error(
                        "value_error",
                        format!("weight wire is missing — instrument={}", target.2),
                    )
                })?;
                if weight < 0.0 && !self.allow_short {
                    return Err(self.error(
                        "unsupported_action_value",
                        format!(
                            "negative target weight requires SHORT_SELLING feature (not implemented) — instrument={} weight={weight} decision_id={}",
                            target.2, self.decision_id
                        ),
                    ));
                }
                let target_notional = weight * equity;
                let delta_notional = target_notional - self.market_value(&target.1);
                let shares = (delta_notional.abs() / self.close(target)?).floor() as i64;
                let delta = if delta_notional >= 0.0 {
                    shares
                } else {
                    -shares
                };
                let flips =
                    held != 0 && target_notional != 0.0 && (held > 0) != (target_notional > 0.0);
                Ok((delta, !flips))
            }
            "notional" => {
                let money_currency = target.4.as_deref().unwrap_or("");
                if money_currency != target.3 {
                    return Err(self.error(
                        "unsupported_action_value",
                        format!(
                            "notional currency does not match instrument currency — notional={money_currency} instrument={}/{} decision_id={}",
                            target.2, target.3, self.decision_id
                        ),
                    ));
                }
                let text = target.6.as_deref().unwrap_or("");
                let target_notional = text.parse::<f64>().map_err(|_| {
                    self.error(
                        "value_error",
                        format!(
                            "invalid target notional — value={text} instrument={}",
                            target.2
                        ),
                    )
                })?;
                if target_notional < 0.0 && !self.allow_short {
                    return Err(self.error(
                        "unsupported_action_value",
                        format!(
                            "negative target notional requires SHORT_SELLING feature (not implemented) — instrument={} notional={text} decision_id={}",
                            target.2, self.decision_id
                        ),
                    ));
                }
                let delta_notional = target_notional - self.market_value(&target.1);
                let shares = (delta_notional.abs() / self.close(target)?).floor() as i64;
                Ok((
                    if delta_notional >= 0.0 {
                        shares
                    } else {
                        -shares
                    },
                    false,
                ))
            }
            "quantity" => {
                let quantity = self.integer(target)?;
                if quantity < 0 && !self.allow_short {
                    return Err(self.error(
                        "unsupported_action_value",
                        format!(
                            "negative target quantity requires SHORT_SELLING feature (not implemented) — instrument={} quantity={} decision_id={}",
                            target.2,
                            target.6.as_deref().unwrap_or(""),
                            self.decision_id
                        ),
                    ));
                }
                Ok((quantity - held, false))
            }
            other => Err(self.error(
                "value_error",
                format!("unknown target wire kind — kind={other:?}"),
            )),
        }
    }

    fn adjust_delta(&self, target: &TargetWire) -> Result<i64, RouteError> {
        match target.0.as_str() {
            "quantity_delta" => self.integer(target),
            "notional_delta" => {
                let money_currency = target.4.as_deref().unwrap_or("");
                if money_currency != target.3 {
                    return Err(self.error(
                        "unsupported_action_value",
                        format!(
                            "notional currency does not match instrument currency — notional={money_currency} instrument={}/{} decision_id={}",
                            target.2, target.3, self.decision_id
                        ),
                    ));
                }
                let text = target.6.as_deref().unwrap_or("");
                let notional = text.parse::<f64>().map_err(|_| {
                    self.error(
                        "value_error",
                        format!(
                            "invalid notional delta — value={text} instrument={}",
                            target.2
                        ),
                    )
                })?;
                let shares = (notional.abs() / self.close(target)?).floor() as i64;
                Ok(if notional >= 0.0 { shares } else { -shares })
            }
            other => Err(self.error(
                "value_error",
                format!("unknown delta wire kind — kind={other:?}"),
            )),
        }
    }

    fn take_open_order(&mut self, order_id: &str) -> Result<StoredOrder, RouteError> {
        let Some(index) = self
            .orders
            .iter()
            .position(|order| order.order_id == order_id)
        else {
            let open: Vec<_> = self
                .orders
                .iter()
                .map(|order| order.order_id.as_str())
                .collect();
            return Err(self.error(
                "unknown_order_id",
                format!(
                    "order is not open (unknown, filled, cancelled or expired) — order_id={order_id} open={open:?} decision_id={}",
                    self.decision_id
                ),
            ));
        };
        Ok(self.orders.remove(index))
    }

    fn order_from_request(
        &mut self,
        request: &RequestWire,
        action_index: usize,
    ) -> Result<RoutedOrder, RouteError> {
        let feature = match request.0.as_str() {
            "market" => None,
            "limit" => Some("limit_order"),
            "stop" | "stop_limit" => Some("stop_order"),
            other => {
                return Err(self.error(
                    "value_error",
                    format!("unknown order request wire — order_type={other:?}"),
                ));
            }
        };
        if let Some(feature) = feature {
            self.require_feature(feature, format!("order_type={}", request.0))?;
        }
        if matches!(request.4.as_str(), "ioc" | "fok") {
            self.require_feature("partial_fill", format!("time_in_force={}", request.4))?;
        }
        let mut quantity_target = request.1.clone();
        quantity_target.6 = Some(request.3.clone());
        let quantity = self.integer(&quantity_target)?;
        if quantity <= 0 {
            return Err(self.error(
                "value_error",
                format!(
                    "order quantity must be > 0 — instrument={} side={} quantity={quantity}",
                    request.1 .2, request.2
                ),
            ));
        }
        if request.2 == "sell" && !self.allow_short {
            let sellable = self.sellable(&request.1 .1);
            if quantity > sellable {
                return Err(self.error(
                    "unsupported_action_value",
                    format!(
                        "resulting position would be negative — requires SHORT_SELLING feature (not implemented) — instrument={} held={} sell={quantity} already_routed={} open_sell={} decision_id={}",
                        request.1.2,
                        self.held(&request.1.1),
                        self.routed_sells.get(&request.1.1).copied().unwrap_or(0),
                        self.open_sell_quantity(&request.1.1),
                        self.decision_id
                    ),
                ));
            }
            *self.routed_sells.entry(request.1 .1.clone()).or_default() += quantity;
        }
        *self.order_seq += 1;
        Ok((
            format!("O-{:06}", *self.order_seq),
            request.1 .1.clone(),
            quantity,
            request.2.clone(),
            request.0.clone(),
            request.5.clone(),
            request.6.clone(),
            request.4.clone(),
            None,
            action_index,
            None,
        ))
    }

    fn route_basket_leg(
        &mut self,
        leg: &BasketLegWire,
        equity: f64,
        action_index: usize,
        leg_index: usize,
    ) -> Result<Option<RoutedOrder>, RouteError> {
        let mut routed = match leg.0.as_str() {
            "set_position_target" => {
                let execution = leg.2.as_ref().ok_or_else(|| {
                    self.error(
                        "value_error",
                        "basket execution wire is missing".to_string(),
                    )
                })?;
                self.check_execution(execution)?;
                let target = leg.1.as_ref().ok_or_else(|| {
                    self.error("value_error", "basket target wire is missing".to_string())
                })?;
                let (delta, clamp) = self.target_delta(target, equity)?;
                self.delta_order(target, delta, clamp, &execution.2, action_index)?
            }
            "adjust_position" => {
                let execution = leg.2.as_ref().ok_or_else(|| {
                    self.error(
                        "value_error",
                        "basket execution wire is missing".to_string(),
                    )
                })?;
                self.check_execution(execution)?;
                let target = leg.1.as_ref().ok_or_else(|| {
                    self.error(
                        "value_error",
                        "basket adjustment wire is missing".to_string(),
                    )
                })?;
                let delta = self.adjust_delta(target)?;
                self.delta_order(target, delta, false, &execution.2, action_index)?
            }
            "liquidate_position" => {
                let execution = leg.2.as_ref().ok_or_else(|| {
                    self.error(
                        "value_error",
                        "basket execution wire is missing".to_string(),
                    )
                })?;
                self.check_execution(execution)?;
                let target = leg.1.as_ref().ok_or_else(|| {
                    self.error(
                        "value_error",
                        "basket liquidation wire is missing".to_string(),
                    )
                })?;
                let cancel =
                    leg.4.as_deref().unwrap_or("false|once").split('|').next() == Some("true");
                self.liquidate(target, cancel, &execution.2, action_index)?
            }
            "submit_order" => {
                let request = leg.3.as_ref().ok_or_else(|| {
                    self.error(
                        "value_error",
                        "basket order request wire is missing".to_string(),
                    )
                })?;
                Some(self.order_from_request(request, action_index)?)
            }
            other => {
                return Err(self.error(
                    "value_error",
                    format!("unsupported basket leg wire — kind={other:?}"),
                ));
            }
        };
        if let Some(order) = routed.as_mut() {
            order.10 = Some(leg_index);
        }
        Ok(routed)
    }

    fn basket_instrument<'a>(&self, leg: &'a BasketLegWire) -> Option<&'a TargetWire> {
        leg.1
            .as_ref()
            .or_else(|| leg.3.as_ref().map(|request| &request.1))
    }

    fn liquidate(
        &mut self,
        target: &TargetWire,
        cancel_open_orders: bool,
        time_in_force: &str,
        action_index: usize,
    ) -> Result<Option<RoutedOrder>, RouteError> {
        if cancel_open_orders {
            let mut cancelled = Vec::new();
            self.orders.retain(|order| {
                if order.key == target.1 {
                    cancelled.push(order.order_id.clone());
                    false
                } else {
                    true
                }
            });
            self.updates.extend(cancelled.into_iter().map(|order_id| {
                (
                    order_id,
                    "cancelled".to_string(),
                    format!(
                        "cancelled by LiquidatePosition — instrument={} decision_id={}",
                        target.2, self.decision_id
                    ),
                )
            }));
        }
        let held = self.held(&target.1);
        let (side, quantity) = if held > 0 {
            let sellable = self.sellable(&target.1);
            if sellable <= 0 {
                return Ok(None);
            }
            *self.routed_sells.entry(target.1.clone()).or_default() += sellable;
            ("sell", sellable)
        } else if held < 0 {
            ("buy", held.unsigned_abs() as i64)
        } else {
            return Ok(None);
        };
        *self.order_seq += 1;
        Ok(Some((
            format!("O-{:06}", *self.order_seq),
            target.1.clone(),
            quantity,
            side.to_string(),
            "market".to_string(),
            None,
            None,
            time_in_force.to_string(),
            None,
            action_index,
            None,
        )))
    }

    fn delta_order(
        &mut self,
        target: &TargetWire,
        delta: i64,
        clamp_sell: bool,
        time_in_force: &str,
        action_index: usize,
    ) -> Result<Option<RoutedOrder>, RouteError> {
        if delta == 0 {
            return Ok(None);
        }
        let side = if delta > 0 { "buy" } else { "sell" };
        let mut quantity = delta.unsigned_abs() as i64;
        let held = self.held(&target.1);
        if clamp_sell && self.allow_short && held != 0 && (held > 0) != (delta > 0) {
            quantity = quantity.min(held.unsigned_abs() as i64);
            if quantity <= 0 {
                return Ok(None);
            }
        }
        if side == "sell" && !self.allow_short {
            let sellable = self.sellable(&target.1);
            if quantity > sellable {
                if !clamp_sell {
                    return Err(self.error(
                        "unsupported_action_value",
                        format!(
                            "resulting position would be negative — requires SHORT_SELLING feature (not implemented) — instrument={} held={held} sell={quantity} already_routed={} open_sell={} decision_id={}",
                            target.2,
                            self.routed_sells.get(&target.1).copied().unwrap_or(0),
                            self.open_sell_quantity(&target.1),
                            self.decision_id
                        ),
                    ));
                }
                quantity = sellable;
            }
            if quantity <= 0 {
                return Ok(None);
            }
            *self.routed_sells.entry(target.1.clone()).or_default() += quantity;
        }
        *self.order_seq += 1;
        Ok(Some((
            format!("O-{:06}", *self.order_seq),
            target.1.clone(),
            quantity,
            side.to_string(),
            "market".to_string(),
            None,
            None,
            time_in_force.to_string(),
            None,
            action_index,
            None,
        )))
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn route_basic_decision(
    portfolio: &Portfolio,
    orders: &mut Vec<StoredOrder>,
    config: &RouterConfig,
    order_seq: &mut u64,
    group_seq: &mut u64,
    allow_short: bool,
    decision_id: &str,
    decision: DecisionWire,
    bars: HashMap<String, CloseWire>,
) -> PyResult<RoutedDecision> {
    if decision.0 != 1 {
        return Ok((
            Vec::new(),
            Vec::new(),
            Vec::new(),
            Some((
                "schema_version".to_string(),
                format!(
                    "decision schema version mismatch — expected 1, got {} as_of={} reason={:?}",
                    decision.0, decision.1, decision.2
                ),
            )),
        ));
    }
    for action in &decision.3 {
        if !config.action_declared(&action.0) {
            return Ok((
                Vec::new(),
                Vec::new(),
                Vec::new(),
                Some((
                    "undeclared_action".to_string(),
                    format!(
                        "action kind was not declared in requirements() — kind={} declared={:?} decision_id={decision_id} as_of={}",
                        action.0,
                        config.sorted_actions(),
                        decision.1
                    ),
                )),
            ));
        }
    }
    if decision.3.iter().all(|action| action.0 == "no_action") {
        return Ok((Vec::new(), Vec::new(), Vec::new(), None));
    }
    let (_, positions, equity, _) = portfolio.snapshot()?;
    let mut context = RouteContext {
        decision_id,
        portfolio_positions: &positions,
        bars: &bars,
        orders,
        config,
        allow_short,
        order_seq,
        group_seq,
        routed_sells: HashMap::new(),
        updates: Vec::new(),
        groups: Vec::new(),
    };
    let mut routed = Vec::new();
    for (action_index, action) in decision.3.iter().enumerate() {
        let result = match action.0.as_str() {
            "no_action" => Ok(()),
            "set_position_target" => {
                let execution = action.3.as_ref().ok_or_else(|| {
                    context.error("value_error", "execution wire is missing".to_string())
                });
                execution.and_then(|execution| {
                    context.check_execution(execution)?;
                    let target = action.1.first().ok_or_else(|| {
                        context.error("value_error", "position target wire is missing".to_string())
                    })?;
                    let (delta, clamp) = context.target_delta(target, equity)?;
                    if let Some(order) =
                        context.delta_order(target, delta, clamp, &execution.2, action_index)?
                    {
                        routed.push(order);
                    }
                    Ok(())
                })
            }
            "set_portfolio_target" => {
                let execution = action.3.as_ref().ok_or_else(|| {
                    context.error("value_error", "execution wire is missing".to_string())
                });
                execution.and_then(|execution| {
                    context.check_execution(execution)?;
                    let mut seen = HashSet::new();
                    let mut deltas = Vec::with_capacity(action.1.len() + positions.len());
                    for target in &action.1 {
                        if !seen.insert(target.1.clone()) {
                            return Err(context.error(
                                "value_error",
                                format!(
                                    "duplicate instrument in portfolio target — instrument={} decision_id={decision_id}",
                                    target.2
                                ),
                            ));
                        }
                        let (delta, clamp) = context.target_delta(target, equity)?;
                        deltas.push((target.clone(), delta, clamp));
                    }
                    if action.2.as_deref() == Some("replace") {
                        for position in &positions {
                            if !seen.contains(&position.0) {
                                deltas.push((
                                    (
                                        "quantity".to_string(),
                                        position.0.clone(),
                                        String::new(),
                                        String::new(),
                                        None,
                                        None,
                                        Some("0".to_string()),
                                    ),
                                    -position.1,
                                    true,
                                ));
                            }
                        }
                    }
                    for (target, delta, clamp) in &deltas {
                        if let Some(order) = context.delta_order(
                            target,
                            *delta,
                            *clamp,
                            &execution.2,
                            action_index,
                        )? {
                            routed.push(order);
                        }
                    }
                    Ok(())
                })
            }
            "adjust_position" => {
                let execution = action.3.as_ref().ok_or_else(|| {
                    context.error("value_error", "execution wire is missing".to_string())
                });
                execution.and_then(|execution| {
                    context.check_execution(execution)?;
                    let target = action.1.first().ok_or_else(|| {
                        context.error("value_error", "adjustment wire is missing".to_string())
                    })?;
                    let delta = context.adjust_delta(target)?;
                    if let Some(order) =
                        context.delta_order(target, delta, false, &execution.2, action_index)?
                    {
                        routed.push(order);
                    }
                    Ok(())
                })
            }
            "liquidate_position" => {
                let execution = action.3.as_ref().ok_or_else(|| {
                    context.error("value_error", "execution wire is missing".to_string())
                });
                execution.and_then(|execution| {
                    context.check_execution(execution)?;
                    let target = action.1.first().ok_or_else(|| {
                        context.error("value_error", "liquidation wire is missing".to_string())
                    })?;
                    let cancel_open_orders = action
                        .2
                        .as_deref()
                        .unwrap_or("false|once")
                        .split('|')
                        .next()
                        == Some("true");
                    if let Some(order) =
                        context.liquidate(target, cancel_open_orders, &execution.2, action_index)?
                    {
                        routed.push(order);
                    }
                    Ok(())
                })
            }
            "submit_order" => {
                let request = action.4.as_ref().ok_or_else(|| {
                    context.error("value_error", "order request wire is missing".to_string())
                });
                request.and_then(|request| {
                    routed.push(context.order_from_request(request, action_index)?);
                    Ok(())
                })
            }
            "cancel_order" => {
                let order_id = action.2.as_deref().ok_or_else(|| {
                    context.error("value_error", "cancel order_id wire is missing".to_string())
                });
                order_id.and_then(|order_id| {
                    context.take_open_order(order_id)?;
                    context.updates.push((
                        order_id.to_string(),
                        "cancelled".to_string(),
                        format!("cancelled by strategy — decision_id={decision_id}"),
                    ));
                    Ok(())
                })
            }
            "replace_order" => {
                let order_id = action.2.as_deref().ok_or_else(|| {
                    context.error(
                        "value_error",
                        "replace order_id wire is missing".to_string(),
                    )
                });
                order_id.and_then(|order_id| {
                    context.take_open_order(order_id)?;
                    let request = action.4.as_ref().ok_or_else(|| {
                        context.error("value_error", "replacement wire is missing".to_string())
                    })?;
                    let replacement = context.order_from_request(request, action_index)?;
                    context.updates.push((
                        order_id.to_string(),
                        "replaced".to_string(),
                        format!(
                            "replaced by strategy — replaced_by={} decision_id={decision_id}",
                            replacement.0
                        ),
                    ));
                    routed.push(replacement);
                    Ok(())
                })
            }
            "basket" => (|| -> Result<(), RouteError> {
                let policy = action.2.as_deref().unwrap_or("best_effort");
                if policy == "proportional" {
                    context
                        .require_feature("proportional_basket", format!("group_policy={policy}"))?;
                }
                let mut seen = HashSet::new();
                for leg in &action.5 {
                    let target = context.basket_instrument(leg).ok_or_else(|| {
                        context.error(
                            "value_error",
                            "basket instrument wire is missing".to_string(),
                        )
                    })?;
                    if !seen.insert(target.1.clone()) {
                        return Err(context.error(
                                "value_error",
                                format!(
                                    "duplicate instrument in basket — instrument={} decision_id={decision_id}",
                                    target.2
                                ),
                            ));
                    }
                }
                *context.group_seq += 1;
                let group_id = format!("G-{:06}", *context.group_seq);
                let mut order_ids = Vec::new();
                for (leg_index, leg) in action.5.iter().enumerate() {
                    if let Some(mut order) =
                        context.route_basket_leg(leg, equity, action_index, leg_index)?
                    {
                        order.8 = Some(group_id.clone());
                        order_ids.push(order.0.clone());
                        routed.push(order);
                    }
                }
                context
                    .groups
                    .push((group_id, policy.to_string(), order_ids));
                Ok(())
            })(),
            other => Err(context.error(
                "value_error",
                format!("unsupported basic action wire — kind={other:?}"),
            )),
        };
        if let Err(error) = result {
            return Ok((Vec::new(), context.updates, context.groups, Some(error)));
        }
    }
    Ok((routed, context.updates, context.groups, None))
}
