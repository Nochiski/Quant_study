use crate::buying_power::BuyingPower;
use crate::execution::execution_price;
use crate::quote::{liquidity_cap, quote_numbers};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::HashMap;

mod group;

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
    limit_text: Option<String>, // Python str(Decimal) — 진단 문자열 전용
    stop_text: Option<String>,
}

/// PyO3 튜플 변환은 12원소까지라 가격·표기 필드를 하위 튜플로 묶는다.
type PriceFields = (Option<f64>, Option<f64>, Option<String>, Option<String>);
type EntryTuple = (
    String,
    String,
    String,
    String,
    String,
    PriceFields,
    String,
    i64,
    bool,
    Option<String>,
    Option<String>,
);

impl EntryIn {
    fn from_tuple(t: EntryTuple) -> Self {
        let (limit_price, stop_price, limit_text, stop_text) = t.5;
        Self {
            order_id: t.0,
            key: t.1,
            symbol: t.2,
            side: t.3,
            order_type: t.4,
            limit_price,
            stop_price,
            tif: t.6,
            remaining: t.7,
            triggered: t.8,
            group_id: t.9,
            participation: t.10,
            limit_text,
            stop_text,
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
    if value.is_nan() {
        return "nan".to_string();
    }
    if value.is_infinite() {
        return if value > 0.0 { "inf" } else { "-inf" }.to_string();
    }
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0.0"
        } else {
            "0.0"
        }
        .to_string();
    }
    // Rust `{:e}`는 최단 왕복 자릿수를 주고 지수는 부호·자릿수 패딩이 없다 ("9.9e-6").
    let sci = format!("{value:e}");
    let (mantissa, exponent) = sci.split_once('e').unwrap_or((sci.as_str(), "0"));
    let exponent: i32 = exponent.parse().unwrap_or(0);
    if !(-4..16).contains(&exponent) {
        // Python repr: 지수 표기, 부호 항상, 지수 두 자리 이상 (1e-05, 2e+16).
        let sign = if exponent < 0 { '-' } else { '+' };
        return format!("{mantissa}e{sign}{:02}", exponent.abs());
    }
    let plain = format!("{value}");
    if plain.contains('.') {
        plain
    } else {
        format!("{plain}.0")
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
                "stop triggered, limit not met — instrument={} stop={} limit={} ts={}",
                e.symbol,
                e.stop_text.clone().unwrap_or_else(|| "None".to_string()),
                e.limit_text.clone().unwrap_or_else(|| "None".to_string()),
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

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(process_market, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn py_float_matches_python_repr() {
        assert_eq!(py_float(100000.0), "100000.0");
        assert_eq!(py_float(110.055), "110.055");
        assert_eq!(py_float(9.999999974752427e-06), "9.999999974752427e-06");
        assert_eq!(py_float(2e16), "2e+16");
        assert_eq!(py_float(1e-5), "1e-05");
        assert_eq!(py_float(0.0001), "0.0001");
        assert_eq!(py_float(f64::NAN), "nan");
        assert_eq!(py_float(-0.0), "-0.0");
    }
}
