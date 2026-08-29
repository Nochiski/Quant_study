//! backtest_engine의 결정론적 코어 (로드맵 6a).
//!
//! Python 구현(`src/backtest_engine/engine/{broker,portfolio}.py`, `sizing.py`)이 진실 원천이다.
//! 여기 함수는 그 구현과 **같은 부동소수 연산 순서**로 같은 결과를 내야 하며, 동일성은
//! `tests/test_core_parity.py`가 두 코어를 나란히 돌려 고정한다.
//!
//! 경계는 원시 타입만 쓴다: 종목은 `"venue:symbol"` 키 문자열, 수량은 정수 주식 수(i64),
//! 가격·현금은 f64. Decimal 비율이 필요한 자본변동 산술은 Python 어댑터가 하고 여기는
//! 결과만 적용한다.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::HashMap;

// ---------------------------------------------------------------------------
// 체결 가격 규칙 (broker.execution_price와 동일)
// ---------------------------------------------------------------------------

/// 일봉 OHLC에서 주문 종류·방향별 체결 가격을 정한다.
///
/// 반환: `(가격 또는 None, 이번 세션에 STOP이 발동했는가)`.
/// 규칙: 시가에서 이미 조건 충족이면 시가, 장중 충족이면 조건 가격, 아니면 None.
/// STOP_LIMIT은 발동 가격이 지정가 안이면 그 가격, 아니면 (None, true)로 발동만 알린다.
#[pyfunction]
#[pyo3(signature = (order_type, side, open, high, low, limit_price=None, stop_price=None, already_triggered=false))]
#[allow(clippy::too_many_arguments)]
fn execution_price(
    order_type: &str,
    side: &str,
    open: f64,
    high: f64,
    low: f64,
    limit_price: Option<f64>,
    stop_price: Option<f64>,
    already_triggered: bool,
) -> PyResult<(Option<f64>, bool)> {
    let buy = match side {
        "buy" => true,
        "sell" => false,
        other => {
            return Err(PyValueError::new_err(format!(
                "side must be buy|sell — got {other:?}"
            )))
        }
    };
    let require = |label: &str, value: Option<f64>| -> PyResult<f64> {
        value.ok_or_else(|| {
            PyValueError::new_err(format!(
                "order type {order_type} requires {label} — got None"
            ))
        })
    };
    match order_type {
        "market" => Ok((Some(open), false)),
        "limit" => Ok((
            limit_fill(open, high, low, require("limit_price", limit_price)?, buy),
            false,
        )),
        "stop" => {
            if already_triggered {
                // 발동 후 잔량은 시장가로 취급한다 (부분체결 이월)
                return Ok((Some(open), false));
            }
            Ok((
                stop_fill(open, high, low, require("stop_price", stop_price)?, buy),
                false,
            ))
        }
        "stop_limit" => {
            let limit = require("limit_price", limit_price)?;
            if already_triggered {
                return Ok((limit_fill(open, high, low, limit, buy), false));
            }
            let trigger = stop_fill(open, high, low, require("stop_price", stop_price)?, buy);
            match trigger {
                None => Ok((None, false)),
                Some(price) => {
                    let within = if buy { price <= limit } else { price >= limit };
                    Ok((if within { Some(price) } else { None }, true))
                }
            }
        }
        other => Err(PyValueError::new_err(format!(
            "order_type must be market|limit|stop|stop_limit — got {other:?}"
        ))),
    }
}

fn limit_fill(open: f64, high: f64, low: f64, limit: f64, buy: bool) -> Option<f64> {
    if buy {
        if open <= limit {
            return Some(open);
        }
        return if low <= limit { Some(limit) } else { None };
    }
    if open >= limit {
        return Some(open);
    }
    if high >= limit {
        Some(limit)
    } else {
        None
    }
}

fn stop_fill(open: f64, high: f64, low: f64, stop: f64, buy: bool) -> Option<f64> {
    if buy {
        if open >= stop {
            return Some(open);
        }
        return if high >= stop { Some(stop) } else { None };
    }
    if open <= stop {
        return Some(open);
    }
    if low <= stop {
        Some(stop)
    } else {
        None
    }
}

// ---------------------------------------------------------------------------
// 수량 변환 (sizing.floor_delta_shares와 동일)
// ---------------------------------------------------------------------------

/// 목표 금액 변화량을 정수 주식 수로 변환한다 (절대값 floor). 방향은 호출 측 부호 판단.
#[pyfunction]
fn floor_delta_shares(delta_notional: f64, reference_price: f64) -> PyResult<i64> {
    if reference_price <= 0.0 {
        return Err(PyValueError::new_err(format!(
            "reference price must be > 0 — reference_price={reference_price} delta_notional={delta_notional}"
        )));
    }
    Ok((delta_notional.abs() / reference_price).floor() as i64)
}

// ---------------------------------------------------------------------------
// 포트폴리오 회계 (engine/portfolio.Portfolio와 동일)
// ---------------------------------------------------------------------------

/// 스냅샷 포지션 행: `(key, quantity, average_price, market_price, market_value, unrealized_pnl)`.
type PositionRow = (String, i64, f64, f64, f64, f64);
/// 스냅샷: `(cash, positions, equity, gross_exposure)`.
type SnapshotTuple = (f64, Vec<PositionRow>, f64, f64);

#[derive(Clone, Debug)]
struct Ledger {
    quantity: i64,
    average_price: f64,
}

/// 현금·보유 원장. Fill / 비용 / 자본변동 적용 시점에만 상태가 변한다.
#[pyclass]
struct Portfolio {
    cash: f64,
    /// Python dict와 같은 삽입 순서를 유지한다 — 스냅샷 순서와 equity 합산 순서가 여기에 의존한다.
    ledgers: Vec<(String, Ledger)>,
    marks: HashMap<String, f64>,
    allow_short: bool,
    allow_margin: bool,
}

impl Portfolio {
    fn ledger_index(&self, key: &str) -> Option<usize> {
        self.ledgers.iter().position(|(k, _)| k == key)
    }

    fn remove_ledger(&mut self, key: &str) {
        if let Some(index) = self.ledger_index(key) {
            self.ledgers.remove(index);
        }
    }
}

#[pymethods]
impl Portfolio {
    #[new]
    #[pyo3(signature = (initial_cash, allow_short=false, allow_margin=false))]
    fn new(initial_cash: f64, allow_short: bool, allow_margin: bool) -> Self {
        Self {
            cash: initial_cash,
            ledgers: Vec::new(),
            marks: HashMap::new(),
            allow_short,
            allow_margin,
        }
    }

    /// 체결 적용. 실패는 ValueError 메시지 접두어로 종류를 알린다:
    /// `negative_position:` / `negative_cash:` — Python 어댑터가 도메인 예외로 바꾼다.
    fn apply(
        &mut self,
        key: &str,
        side: &str,
        quantity: i64,
        price: f64,
        fee: f64,
    ) -> PyResult<()> {
        let buy = match side {
            "buy" => true,
            "sell" => false,
            other => {
                return Err(PyValueError::new_err(format!(
                    "side must be buy|sell — got {other:?}"
                )))
            }
        };
        if quantity <= 0 {
            return Err(PyValueError::new_err(format!(
                "fill quantity must be > 0 — key={key} quantity={quantity}"
            )));
        }
        let qty_f = quantity as f64;
        let notional = qty_f * price;
        let old_quantity = self
            .ledger_index(key)
            .map(|i| self.ledgers[i].1.quantity)
            .unwrap_or(0);
        let signed = if buy { quantity } else { -quantity };
        let new_quantity = old_quantity + signed;

        if new_quantity < 0 && !self.allow_short {
            return Err(PyValueError::new_err(format!(
                "negative_position: sell fill exceeds held quantity — key={key} sell={quantity} held={old_quantity} (SHORT_SELLING not declared)"
            )));
        }
        let new_cash = if buy {
            self.cash - notional - fee
        } else {
            self.cash + notional - fee
        };
        if new_cash < 0.0 && !self.allow_margin {
            return Err(PyValueError::new_err(format!(
                "negative_cash: buy fill would make cash negative — key={key} quantity={quantity} price={price} fee={fee} cash={} (MARGIN not declared)",
                self.cash
            )));
        }

        if new_quantity == 0 {
            self.remove_ledger(key);
        } else {
            let crossed = old_quantity == 0 || (old_quantity > 0) != (new_quantity > 0);
            match self.ledger_index(key) {
                Some(index) if !crossed => {
                    let ledger = &mut self.ledgers[index].1;
                    if new_quantity.abs() > old_quantity.abs() {
                        let old_abs = old_quantity.abs() as f64;
                        ledger.average_price = (old_abs * ledger.average_price + notional)
                            / (new_quantity.abs() as f64);
                    }
                    ledger.quantity = new_quantity;
                }
                Some(index) => {
                    // 방향 전환: Python은 dict 항목을 덮어쓰므로 위치는 유지된다.
                    self.ledgers[index].1 = Ledger {
                        quantity: new_quantity,
                        average_price: price,
                    };
                }
                None => self.ledgers.push((
                    key.to_string(),
                    Ledger {
                        quantity: new_quantity,
                        average_price: price,
                    },
                )),
            }
        }
        self.cash = new_cash;
        self.marks.entry(key.to_string()).or_insert(price);
        Ok(())
    }

    /// 비용(차입·이자) 차감.
    fn charge(&mut self, amount: f64) -> PyResult<()> {
        if amount <= 0.0 {
            return Err(PyValueError::new_err(format!(
                "cost amount must be > 0 — amount={amount}"
            )));
        }
        self.cash -= amount;
        Ok(())
    }

    /// 자본변동 결과 적용. 산술(floor(qty×ratio), 단주 현금)은 Python 어댑터가 Decimal로 하고
    /// 여기는 새 수량·평균단가·현금 지급·정산가 마크만 반영한다.
    fn apply_corporate_action(
        &mut self,
        key: &str,
        new_quantity: i64,
        new_average_price: f64,
        cash_paid: f64,
        settlement_price: f64,
    ) -> PyResult<()> {
        if settlement_price <= 0.0 {
            return Err(PyValueError::new_err(format!(
                "corporate action settlement price must be > 0 — key={key} price={settlement_price}"
            )));
        }
        self.cash += cash_paid;
        if new_quantity == 0 {
            self.remove_ledger(key);
        } else {
            let ledger = Ledger {
                quantity: new_quantity,
                average_price: new_average_price,
            };
            match self.ledger_index(key) {
                Some(index) => self.ledgers[index].1 = ledger,
                None => self.ledgers.push((key.to_string(), ledger)),
            }
        }
        self.marks.insert(key.to_string(), settlement_price);
        Ok(())
    }

    /// 세션 종가로 평가 가격 갱신.
    fn mark(&mut self, closes: Vec<(String, f64)>) {
        for (key, close) in closes {
            self.marks.insert(key, close);
        }
    }

    #[getter]
    fn cash(&self) -> f64 {
        self.cash
    }

    fn held_qty(&self, key: &str) -> i64 {
        self.ledger_index(key)
            .map(|i| self.ledgers[i].1.quantity)
            .unwrap_or(0)
    }

    fn average_price(&self, key: &str) -> Option<f64> {
        self.ledger_index(key)
            .map(|i| self.ledgers[i].1.average_price)
    }

    /// 스냅샷: `(cash, positions, equity, gross_exposure)`. 포지션은 원장 삽입 순서(Python dict와
    /// 동일)이고, equity·gross는 Python의 `cash + sum(mv)` / `sum(|mv|)`와 같은 결합 순서로
    /// 누산한다 (비트 동일성).
    fn snapshot(&self) -> PyResult<SnapshotTuple> {
        let mut positions = Vec::with_capacity(self.ledgers.len());
        let mut total_value = 0.0_f64;
        let mut gross = 0.0_f64;
        for (key, ledger) in &self.ledgers {
            let mark = *self.marks.get(key).ok_or_else(|| {
                PyValueError::new_err(format!("no mark price for held instrument — key={key}"))
            })?;
            let qty_f = ledger.quantity as f64;
            let market_value = qty_f * mark;
            let unrealized = (mark - ledger.average_price) * qty_f;
            total_value += market_value;
            gross += market_value.abs();
            positions.push((
                key.clone(),
                ledger.quantity,
                ledger.average_price,
                mark,
                market_value,
                unrealized,
            ));
        }
        let equity = self.cash + total_value;
        let gross_exposure = if equity != 0.0 { gross / equity } else { 0.0 };
        Ok((self.cash, positions, equity, gross_exposure))
    }
}

// ---------------------------------------------------------------------------
// 6b: 견적 산술 (broker.BrokerSim.quote의 수치 부분) + 매수 여력 누산기
// ---------------------------------------------------------------------------

/// 십진 문자열(예: "0.07")을 (분자, 10^k 분모)로 파싱한다 — Python `Decimal(str(p))`와 동일 값.
fn parse_decimal_ratio(text: &str) -> PyResult<(i128, i128)> {
    let trimmed = text.trim();
    let (mantissa, exponent) = match trimmed.split_once(['e', 'E']) {
        Some((m, e)) => (
            m,
            e.parse::<i32>().map_err(|_| {
                PyValueError::new_err(format!("invalid decimal exponent — text={text:?}"))
            })?,
        ),
        None => (trimmed, 0),
    };
    let (int_part, frac_part) = mantissa.split_once('.').unwrap_or((mantissa, ""));
    let digits = format!("{int_part}{frac_part}");
    let numerator = digits
        .parse::<i128>()
        .map_err(|_| PyValueError::new_err(format!("invalid decimal — text={text:?}")))?;
    let scale = frac_part.len() as i32 - exponent;
    if scale >= 0 {
        Ok((numerator, 10_i128.pow(scale as u32)))
    } else {
        Ok((numerator * 10_i128.pow((-scale) as u32), 1))
    }
}

/// floor(volume × participation) — participation은 십진 문자열 (정확한 정수 산술).
#[pyfunction]
fn liquidity_cap(volume: i64, participation: &str) -> PyResult<i64> {
    let (num, den) = parse_decimal_ratio(participation)?;
    if num < 0 {
        return Err(PyValueError::new_err(format!(
            "participation must be >= 0 — participation={participation}"
        )));
    }
    Ok(((volume as i128) * num / den) as i64)
}

/// 수수료 포함 여력으로 살 수 있는 최대 정수 수량 (Python `_affordable_quantity`와 동일 연산).
fn affordable_quantity(buying_power: f64, price: f64, fee_rate: f64) -> i64 {
    if price <= 0.0 || buying_power <= 0.0 {
        return 0;
    }
    (buying_power / (price * (1.0 + fee_rate))).floor() as i64
}

/// 견적의 수치 부분. 슬리피지(주당, ≥0)는 호출 측이 준다 (플러그인 포트).
///
/// 반환 `(price, quantity, slip_applied, status)`; status는
/// `filled | liquidity_limited | cash_limited | rejected_no_cash | not_filled | fok_rejected`.
/// 흐름은 Python과 같다: 유동성 캡 → 슬리피지·지정가 clip → 여력 캡(숏 진입분 포함) → FOK.
#[pyfunction]
#[pyo3(signature = (side, base_price, remaining, volume, participation, slip, limit_price, buying_power, held, fee_rate, fok))]
#[allow(clippy::too_many_arguments)]
fn quote_numbers(
    side: &str,
    base_price: f64,
    remaining: i64,
    volume: i64,
    participation: Option<&str>,
    slip: f64,
    limit_price: Option<f64>,
    buying_power: f64,
    held: i64,
    fee_rate: f64,
    fok: bool,
) -> PyResult<(f64, i64, f64, String)> {
    let buy = match side {
        "buy" => true,
        "sell" => false,
        other => {
            return Err(PyValueError::new_err(format!(
                "side must be buy|sell — got {other:?}"
            )))
        }
    };
    if slip < 0.0 {
        return Err(PyValueError::new_err(format!(
            "slippage must be >= 0 — slip={slip}"
        )));
    }
    let mut quantity = remaining;
    let mut status = "filled";

    if let Some(text) = participation {
        let cap = liquidity_cap(volume, text)?;
        if cap < quantity {
            if cap <= 0 {
                return Ok((base_price, 0, 0.0, "not_filled".to_string()));
            }
            quantity = cap;
            status = "liquidity_limited";
        }
    }

    let mut price = if buy {
        base_price + slip
    } else {
        base_price - slip
    };
    if let Some(limit) = limit_price {
        price = if buy {
            price.min(limit)
        } else {
            price.max(limit)
        };
    }
    let applied = (price - base_price).abs();

    let short_entry = if buy { 0 } else { quantity - held.max(0) };
    if buy || short_entry > 0 {
        let affordable = if buy {
            affordable_quantity(buying_power, price, fee_rate)
        } else {
            let closing = held.max(0);
            let freed_power = buying_power + closing as f64 * price;
            affordable_quantity(freed_power, price, fee_rate) + closing
        };
        if affordable <= 0 {
            return Ok((price, 0, applied, "rejected_no_cash".to_string()));
        }
        if affordable < quantity {
            quantity = affordable;
            status = "cash_limited";
        }
    }

    if fok && quantity < remaining {
        return Ok((price, 0, applied, "fok_rejected".to_string()));
    }
    Ok((price, quantity, applied, status.to_string()))
}

/// 세션 안 매수 여력 = leverage × equity − 총노출 (core.PythonBuyingPower와 동일 연산 순서).
#[pyclass]
struct BuyingPower {
    leverage: f64,
    equity: f64,
    gross: f64,
    quantities: HashMap<String, i64>,
    marks: HashMap<String, f64>,
}

type PowerCheckpoint = (f64, f64, Vec<(String, i64)>, Vec<(String, f64)>);

#[pymethods]
impl BuyingPower {
    /// positions: `(key, quantity, market_price, market_value)` — 스냅샷 포지션.
    #[new]
    fn new(equity: f64, leverage: f64, positions: Vec<(String, i64, f64, f64)>) -> Self {
        let mut gross = 0.0_f64;
        let mut quantities = HashMap::new();
        let mut marks = HashMap::new();
        for (key, quantity, mark, market_value) in positions {
            gross += market_value.abs();
            quantities.insert(key.clone(), quantity);
            marks.insert(key, mark);
        }
        Self {
            leverage,
            equity,
            gross,
            quantities,
            marks,
        }
    }

    #[getter]
    fn available(&self) -> f64 {
        self.leverage * self.equity - self.gross
    }

    fn quantity_of(&self, key: &str) -> i64 {
        *self.quantities.get(key).unwrap_or(&0)
    }

    fn consume(
        &mut self,
        key: &str,
        side: &str,
        quantity: i64,
        price: f64,
        fee: f64,
    ) -> PyResult<()> {
        let buy = match side {
            "buy" => true,
            "sell" => false,
            other => {
                return Err(PyValueError::new_err(format!(
                    "side must be buy|sell — got {other:?}"
                )))
            }
        };
        let old = self.quantity_of(key);
        let new = if buy { old + quantity } else { old - quantity };
        let old_mark = *self.marks.get(key).unwrap_or(&price);
        self.gross += (new.abs() as f64) * price - (old.abs() as f64) * old_mark;
        self.equity += (old as f64) * (price - old_mark) - fee;
        self.marks.insert(key.to_string(), price);
        self.quantities.insert(key.to_string(), new);
        Ok(())
    }

    fn checkpoint(&self) -> PowerCheckpoint {
        (
            self.equity,
            self.gross,
            self.quantities
                .iter()
                .map(|(k, v)| (k.clone(), *v))
                .collect(),
            self.marks.iter().map(|(k, v)| (k.clone(), *v)).collect(),
        )
    }

    fn restore(&mut self, state: PowerCheckpoint) {
        let (equity, gross, quantities, marks) = state;
        self.equity = equity;
        self.gross = gross;
        self.quantities = quantities.into_iter().collect();
        self.marks = marks.into_iter().collect();
    }
}

// ---------------------------------------------------------------------------
// 6c: 세션 MARKET 처리 (loop._on_market의 그룹·단일·만료 루프) — 계획(ops)을 돌려준다
// ---------------------------------------------------------------------------

/// 대기 주문 스냅샷 (Python OrderManager가 진실 원천, 여기서는 읽기 전용 입력).
#[derive(Clone, Debug)]
struct EntryIn {
    order_id: String,
    key: String,
    symbol: String,
    side: String,
    order_type: String,
    limit_price: Option<f64>,
    stop_price: Option<f64>,
    tif: String,
    remaining: i64,
    triggered: bool,
    group_id: Option<String>,
    participation: Option<String>,
}

type EntryTuple = (
    String,
    String,
    String,
    String,
    String,
    Option<f64>,
    Option<f64>,
    String,
    i64,
    bool,
    Option<String>,
    Option<String>,
);

impl EntryIn {
    fn from_tuple(t: EntryTuple) -> Self {
        Self {
            order_id: t.0,
            key: t.1,
            symbol: t.2,
            side: t.3,
            order_type: t.4,
            limit_price: t.5,
            stop_price: t.6,
            tif: t.7,
            remaining: t.8,
            triggered: t.9,
            group_id: t.10,
            participation: t.11,
        }
    }
}

/// bar: `(open, high, low, volume)`.
type BarTuple = (f64, f64, f64, i64);

/// 슬리피지 설정: `("none", 0, 0)` | `("fixed_bps", bps, 0)` | `("volume_share", volume_limit, price_impact)`.
fn slippage_per_share(
    model: &(String, f64, f64),
    base_price: f64,
    quantity: i64,
    volume: i64,
) -> PyResult<f64> {
    match model.0.as_str() {
        "none" => Ok(0.0),
        "fixed_bps" => Ok(base_price * model.1 / 10_000.0),
        "volume_share" => {
            let share = if volume <= 0 {
                model.1
            } else {
                (quantity as f64 / volume as f64).min(model.1)
            };
            Ok(share * share * model.2 * base_price)
        }
        other => Err(PyValueError::new_err(format!(
            "unsupported slippage model for rust core — model={other:?}"
        ))),
    }
}

/// Python `repr(float)`와 같은 표기 (정수값은 `100000.0`).
fn py_float(value: f64) -> String {
    if value.is_finite() && value.fract() == 0.0 && value.abs() < 1e16 {
        format!("{value:.1}")
    } else {
        format!("{value}")
    }
}

/// Python `repr(list[str])`와 같은 표기 (`['a', 'b']`).
fn py_list(items: &[String]) -> String {
    let inner: Vec<String> = items.iter().map(|s| format!("'{s}'")).collect();
    format!("[{}]", inner.join(", "))
}

/// 계획 항목. Python 루프가 순서대로 적용한다.
/// `("fill", order_id, quantity, price, slip, fee, "")`
/// `("update", order_id, 0, 0, 0, 0, "status|detail")`
/// `("trigger", order_id, ...)`, `("remove", order_id, ...)`, `("drop_group", group_id, ...)`
type Op = (String, String, i64, f64, f64, f64, String);

struct Session<'a> {
    ts: &'a str,
    bars: &'a HashMap<String, BarTuple>,
    power: &'a mut BuyingPower,
    fee_rate: f64,
    default_participation: Option<&'a str>,
    slippage: &'a (String, f64, f64),
    ops: Vec<Op>,
}

#[derive(Clone)]
struct QuoteOut {
    price: f64,
    quantity: i64,
    slip: f64,
    status: String,
    no_price: bool, // 가격 규칙 미충족(지정가·스톱) — 진단 없이 대기
}

impl<'a> Session<'a> {
    fn quote(&self, e: &EntryIn) -> PyResult<QuoteOut> {
        let bar = self.bars.get(&e.key).ok_or_else(|| {
            PyValueError::new_err(format!(
                "no bar for entry — order_id={} key={}",
                e.order_id, e.key
            ))
        })?;
        let (price, triggered) = execution_price(
            &e.order_type,
            &e.side,
            bar.0,
            bar.1,
            bar.2,
            e.limit_price,
            e.stop_price,
            e.triggered,
        )?;
        let Some(base_price) = price else {
            return Ok(QuoteOut {
                price: 0.0,
                quantity: 0,
                slip: 0.0,
                status: if triggered {
                    "triggered_unfilled"
                } else {
                    "not_filled"
                }
                .to_string(),
                no_price: true,
            });
        };
        let participation = e.participation.as_deref().or(self.default_participation);
        let mut capped = e.remaining;
        if let Some(text) = participation {
            capped = capped.min(liquidity_cap(bar.3, text)?.max(0));
        }
        let slip = slippage_per_share(self.slippage, base_price, capped, bar.3)?;
        let held = self.power.quantity_of(&e.key);
        let (p, q, applied, status) = quote_numbers(
            &e.side,
            base_price,
            e.remaining,
            bar.3,
            participation,
            slip,
            e.limit_price,
            self.power.available(),
            held,
            self.fee_rate,
            e.tif == "fok",
        )?;
        Ok(QuoteOut {
            price: p,
            quantity: q,
            slip: applied,
            status,
            no_price: false,
        })
    }

    fn detail(&self, q: &QuoteOut, e: &EntryIn) -> Option<String> {
        let bar = self.bars.get(&e.key).copied().unwrap_or((0.0, 0.0, 0.0, 0));
        let power = self.power.available();
        match q.status.as_str() {
            "not_filled" => Some(format!(
                "no liquidity in session — order_id={} instrument={} volume={} ts={}",
                e.order_id, e.symbol, bar.3, self.ts
            )),
            "liquidity_limited" => Some(format!(
                "fill capped by volume participation — order_id={} instrument={} remaining={} cap={} volume={}",
                e.order_id, e.symbol, e.remaining, q.quantity, bar.3
            )),
            "rejected_no_cash" => Some(format!(
                "cannot afford a single share — order_id={} instrument={} price={} buying_power={}",
                e.order_id,
                e.symbol,
                py_float(q.price),
                py_float(power)
            )),
            "cash_limited" => Some(format!(
                "buy capped by buying_power — order_id={} instrument={} remaining={} filled={} price={} buying_power={}",
                e.order_id,
                e.symbol,
                e.remaining,
                q.quantity,
                py_float(q.price),
                py_float(power)
            )),
            "fok_rejected" => Some(format!(
                "FOK not fillable in full — order_id={} instrument={} remaining={} price={} buying_power={}",
                e.order_id,
                e.symbol,
                e.remaining,
                py_float(q.price),
                py_float(power)
            )),
            _ => None,
        }
    }

    fn update(&mut self, order_id: &str, status: &str, detail: Option<String>) {
        self.ops.push((
            "update".into(),
            order_id.into(),
            0,
            0.0,
            0.0,
            0.0,
            format!("{status}|{}", detail.unwrap_or_default()),
        ));
    }

    /// 견적을 체결로 확정: fill op, 여력 소모, 잔량 갱신(호출 측 entry 갱신), 상태 기록.
    fn apply(&mut self, e: &mut EntryIn, q: &QuoteOut, quantity: i64) -> PyResult<()> {
        // Python은 견적 시점(여력 소모·잔량 갱신 전)에 detail을 만든다.
        let detail_before = self.detail(q, e);
        let notional = quantity as f64 * q.price;
        let fee = notional * self.fee_rate;
        self.power
            .consume(&e.key, &e.side, quantity, q.price, fee)?;
        self.ops.push((
            "fill".into(),
            e.order_id.clone(),
            quantity,
            q.price,
            q.slip,
            fee,
            String::new(),
        ));
        e.remaining -= quantity;
        if e.remaining == 0 {
            self.update(&e.order_id.clone(), "filled", None);
        } else {
            if (e.order_type == "stop" || e.order_type == "stop_limit") && !e.triggered {
                e.triggered = true;
                self.ops.push((
                    "trigger".into(),
                    e.order_id.clone(),
                    0,
                    0.0,
                    0.0,
                    0.0,
                    String::new(),
                ));
            }
            self.update(&e.order_id.clone(), "partially_filled", detail_before);
        }
        Ok(())
    }

    fn remove(&mut self, order_id: &str) {
        self.ops.push((
            "remove".into(),
            order_id.into(),
            0,
            0.0,
            0.0,
            0.0,
            String::new(),
        ));
    }

    fn single(&mut self, e: &mut EntryIn) -> PyResult<()> {
        let q = self.quote(e)?;
        if q.quantity > 0 {
            let qty = q.quantity;
            self.apply(e, &q, qty)?;
        } else if q.status == "triggered_unfilled" {
            e.triggered = true;
            self.ops.push((
                "trigger".into(),
                e.order_id.clone(),
                0,
                0.0,
                0.0,
                0.0,
                String::new(),
            ));
            let d = format!(
                "stop triggered, limit not met — instrument={} stop={:?} limit={:?} ts={}",
                e.symbol,
                e.stop_price.map(|p| p.to_string()).unwrap_or_default(),
                e.limit_price.map(|p| p.to_string()).unwrap_or_default(),
                self.ts
            );
            self.update(&e.order_id.clone(), "triggered", Some(d));
        } else if (q.status == "rejected_no_cash" || q.status == "not_filled") && !q.no_price {
            if let Some(d) = self.detail(&q, e) {
                self.update(&e.order_id.clone(), "open", Some(d));
            }
        } else if q.status == "fok_rejected" {
            let d = self.detail(&q, e);
            self.remove(&e.order_id.clone());
            self.update(&e.order_id.clone(), "cancelled", d);
        }
        Ok(())
    }

    fn cancel_all(
        &mut self,
        entries: &[EntryIn],
        order_ids: &[String],
        policy: &str,
        group_id: &str,
        reason: &str,
    ) {
        // Python은 group.order_ids(라우팅 순)로 취소한다 — 매도 우선 정렬과 다르다.
        let mut ordered: Vec<&EntryIn> = entries.iter().collect();
        ordered.sort_by_key(|e| order_ids.iter().position(|id| *id == e.order_id));
        for e in ordered {
            self.remove(&e.order_id);
            let d = format!(
                "basket {policy} — {reason} group_id={group_id} remaining={} ts={}",
                e.remaining, self.ts
            );
            self.update(&e.order_id, "cancelled", Some(d));
        }
        self.ops.push((
            "drop_group".into(),
            group_id.into(),
            0,
            0.0,
            0.0,
            0.0,
            String::new(),
        ));
    }

    fn group(
        &mut self,
        group_id: &str,
        policy: &str,
        order_ids: &[String],
        entries: &mut [EntryIn],
    ) -> PyResult<()> {
        let mut legs: Vec<EntryIn> = entries
            .iter()
            .filter(|e| e.group_id.as_deref() == Some(group_id))
            .cloned()
            .collect();
        if legs.is_empty() {
            return Ok(());
        }
        let missing: Vec<String> = legs
            .iter()
            .filter(|e| !self.bars.contains_key(&e.key))
            .map(|e| e.symbol.clone())
            .collect();
        if !missing.is_empty() && policy != "best_effort" {
            let reason = format!(
                "leg without bar in session instruments={}",
                py_list(&missing)
            );
            self.cancel_all(&legs, order_ids, policy, group_id, &reason);
            return Ok(());
        }
        if policy != "best_effort" && legs.len() < order_ids.len() {
            let open: Vec<&str> = legs.iter().map(|e| e.order_id.as_str()).collect();
            let mut gone: Vec<String> = order_ids
                .iter()
                .filter(|id| !open.contains(&id.as_str()))
                .cloned()
                .collect();
            gone.sort();
            let reason = format!(
                "leg(s) no longer open before group execution missing={}",
                py_list(&gone)
            );
            self.cancel_all(&legs, order_ids, policy, group_id, &reason);
            return Ok(());
        }
        // 견적 패스: 매도 먼저, 임시 소모 후 복원.
        legs.retain(|e| self.bars.contains_key(&e.key));
        legs.sort_by(|a, b| {
            (a.side != "sell", a.order_id.clone()).cmp(&(b.side != "sell", b.order_id.clone()))
        });
        let checkpoint = self.power.checkpoint();
        let mut quotes: Vec<QuoteOut> = Vec::with_capacity(legs.len());
        for e in &legs {
            let q = self.quote(e)?;
            if q.quantity > 0 {
                let notional = q.quantity as f64 * q.price;
                self.power.consume(
                    &e.key,
                    &e.side,
                    q.quantity,
                    q.price,
                    notional * self.fee_rate,
                )?;
            }
            quotes.push(q);
        }
        self.power.restore(checkpoint);

        let sync_back = |entries: &mut [EntryIn], legs: &[EntryIn]| {
            for leg in legs {
                if let Some(target) = entries.iter_mut().find(|x| x.order_id == leg.order_id) {
                    target.remaining = leg.remaining;
                    target.triggered = leg.triggered;
                }
            }
        };

        match policy {
            "best_effort" => {
                for (i, q) in quotes.iter().enumerate() {
                    if q.quantity > 0 {
                        let qty = q.quantity;
                        self.apply(&mut legs[i], q, qty)?;
                    }
                }
                sync_back(entries, &legs);
                if legs.iter().all(|e| e.remaining == 0) {
                    self.ops.push((
                        "drop_group".into(),
                        group_id.into(),
                        0,
                        0.0,
                        0.0,
                        0.0,
                        String::new(),
                    ));
                }
            }
            "all_or_none" => {
                let short: Vec<String> = legs
                    .iter()
                    .zip(&quotes)
                    .filter(|(e, q)| q.quantity < e.remaining)
                    .map(|(e, q)| format!("{}:{}/{}", e.symbol, q.quantity, e.remaining))
                    .collect();
                if !short.is_empty() {
                    let reason = format!("not fillable in full legs={}", py_list(&short));
                    self.cancel_all(&legs, order_ids, policy, group_id, &reason);
                    return Ok(());
                }
                for (i, q) in quotes.iter().enumerate() {
                    let qty = q.quantity;
                    self.apply(&mut legs[i], q, qty)?;
                }
                sync_back(entries, &legs);
                self.ops.push((
                    "drop_group".into(),
                    group_id.into(),
                    0,
                    0.0,
                    0.0,
                    0.0,
                    String::new(),
                ));
            }
            _ => {
                // proportional: 최저 비율 leg j 기준 정수 교차곱.
                let (j, _) = legs
                    .iter()
                    .zip(&quotes)
                    .enumerate()
                    .min_by(|(_, (ea, qa)), (_, (eb, qb))| {
                        ((qa.quantity as i128) * (eb.remaining as i128))
                            .cmp(&((qb.quantity as i128) * (ea.remaining as i128)))
                    })
                    .map(|(i, _)| (i, ()))
                    .unwrap_or((0, ()));
                let tight_q = quotes[j].quantity;
                let tight_rem = legs[j].remaining;
                if tight_q <= 0 {
                    self.cancel_all(&legs, order_ids, policy, group_id, "no leg fillable");
                    return Ok(());
                }
                let scale = tight_q as f64 / tight_rem as f64;
                let planned: Vec<i64> = legs
                    .iter()
                    .map(|e| ((e.remaining as i128 * tight_q as i128) / tight_rem as i128) as i64)
                    .collect();
                for (i, qty) in planned.iter().enumerate() {
                    if *qty > 0 {
                        let q = quotes[i].clone();
                        self.apply(&mut legs[i], &q, *qty)?;
                    }
                }
                let mut leftovers: Vec<&EntryIn> =
                    legs.iter().filter(|e| e.remaining > 0).collect();
                leftovers.sort_by_key(|e| order_ids.iter().position(|id| *id == e.order_id));
                for e in leftovers {
                    {
                        self.remove(&e.order_id);
                        let d = format!(
                            "basket proportional remainder cancelled — scale={scale:.6} group_id={group_id} remaining={} ts={}",
                            e.remaining, self.ts
                        );
                        self.update(&e.order_id, "cancelled", Some(d));
                    }
                }
                for leg in legs.iter_mut() {
                    if leg.remaining > 0 {
                        leg.remaining = 0;
                    }
                }
                sync_back(entries, &legs);
                self.ops.push((
                    "drop_group".into(),
                    group_id.into(),
                    0,
                    0.0,
                    0.0,
                    0.0,
                    String::new(),
                ));
            }
        }
        Ok(())
    }
}

/// 세션 MARKET 처리 계획. 그룹(순서대로) → 단일(매도 먼저, order_id 순) → DAY/IOC/FOK 만료.
#[pyfunction]
#[pyo3(signature = (ts, entries, groups, bars, power, fee_rate, default_participation, slippage))]
#[allow(clippy::too_many_arguments)]
fn process_market(
    ts: &str,
    entries: Vec<EntryTuple>,
    groups: Vec<(String, String, Vec<String>)>,
    bars: HashMap<String, BarTuple>,
    power: &mut BuyingPower,
    fee_rate: f64,
    default_participation: Option<&str>,
    slippage: (String, f64, f64),
) -> PyResult<Vec<Op>> {
    let mut entries: Vec<EntryIn> = entries.into_iter().map(EntryIn::from_tuple).collect();
    let mut session = Session {
        ts,
        bars: &bars,
        power,
        fee_rate,
        default_participation,
        slippage: &slippage,
        ops: Vec::new(),
    };
    for (group_id, policy, order_ids) in &groups {
        session.group(group_id, policy, order_ids, &mut entries)?;
    }
    let mut singles: Vec<usize> = entries
        .iter()
        .enumerate()
        .filter(|(_, e)| e.group_id.is_none() && bars.contains_key(&e.key))
        .map(|(i, _)| i)
        .collect();
    singles.sort_by(|&a, &b| {
        (entries[a].side != "sell", entries[a].order_id.clone())
            .cmp(&(entries[b].side != "sell", entries[b].order_id.clone()))
    });
    for i in singles {
        let mut e = entries[i].clone();
        session.single(&mut e)?;
        entries[i] = e;
    }
    // 만료: 잔량이 남은 non-GTC 주문 (그룹 취소·FOK로 이미 제거된 것은 제외 — remaining>0이면서 제거 op가 없는 것)
    let removed: std::collections::HashSet<String> = session
        .ops
        .iter()
        .filter(|op| op.0 == "remove")
        .map(|op| op.1.clone())
        .collect();
    let expiring: Vec<EntryIn> = entries
        .iter()
        .filter(|e| e.tif != "gtc" && e.remaining > 0 && !removed.contains(&e.order_id))
        .cloned()
        .collect();
    for e in expiring {
        session.remove(&e.order_id);
        let reason = if bars.contains_key(&e.key) {
            format!(
                "{} order expired unfilled — instrument={} remaining={} ts={}",
                e.tif, e.symbol, e.remaining, ts
            )
        } else {
            format!(
                "no bar for instrument in session — instrument={} ts={}",
                e.symbol, ts
            )
        };
        session.update(&e.order_id, "cancelled", Some(reason));
    }
    Ok(session.ops)
}

#[pymodule]
fn backtest_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(execution_price, m)?)?;
    m.add_function(wrap_pyfunction!(floor_delta_shares, m)?)?;
    m.add_class::<Portfolio>()?;
    m.add_function(wrap_pyfunction!(liquidity_cap, m)?)?;
    m.add_function(wrap_pyfunction!(quote_numbers, m)?)?;
    m.add_class::<BuyingPower>()?;
    m.add_function(wrap_pyfunction!(process_market, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn limit_buy_rules() {
        // open 100, high 110, low 90
        assert_eq!(limit_fill(100.0, 110.0, 90.0, 105.0, true), Some(100.0));
        assert_eq!(limit_fill(100.0, 110.0, 90.0, 95.0, true), Some(95.0));
        assert_eq!(limit_fill(100.0, 110.0, 90.0, 85.0, true), None);
    }

    #[test]
    fn stop_sell_rules() {
        assert_eq!(stop_fill(100.0, 110.0, 90.0, 105.0, false), Some(100.0));
        assert_eq!(stop_fill(100.0, 110.0, 90.0, 92.0, false), Some(92.0));
        assert_eq!(stop_fill(100.0, 110.0, 90.0, 85.0, false), None);
    }

    #[test]
    fn equity_identity_after_fills_and_mark() {
        let mut p = Portfolio::new(100_000.0, false, false);
        p.apply("XKRX:005930", "buy", 10, 100.0, 100.0).unwrap();
        p.mark(vec![("XKRX:005930".to_string(), 120.0)]);
        let (cash, positions, equity, gross) = p.snapshot().unwrap();
        assert_eq!(cash, 98_900.0);
        assert_eq!(positions[0].4, 1_200.0);
        assert_eq!(equity, cash + 1_200.0);
        assert!((gross - 1_200.0 / equity).abs() < 1e-12);
    }

    #[test]
    fn long_to_short_flip_resets_average() {
        let mut p = Portfolio::new(10_000.0, true, false);
        p.apply("k", "buy", 5, 100.0, 0.0).unwrap();
        p.apply("k", "sell", 8, 120.0, 0.0).unwrap();
        assert_eq!(p.held_qty("k"), -3);
        assert_eq!(p.average_price("k"), Some(120.0));
        assert_eq!(p.cash(), 10_000.0 - 500.0 + 960.0);
    }
}
