use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// 일봉 OHLC에서 주문 종류·방향별 체결 가격을 정한다.
///
/// 반환: `(가격 또는 None, 이번 세션에 STOP이 발동했는가)`.
/// 규칙: 시가에서 이미 조건 충족이면 시가, 장중 충족이면 조건 가격, 아니면 None.
/// STOP_LIMIT은 발동 가격이 지정가 안이면 그 가격, 아니면 (None, true)로 발동만 알린다.
#[pyfunction]
#[pyo3(signature = (order_type, side, open, high, low, limit_price=None, stop_price=None, already_triggered=false))]
#[allow(clippy::too_many_arguments)]
pub(crate) fn execution_price(
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
pub(crate) fn floor_delta_shares(delta_notional: f64, reference_price: f64) -> PyResult<i64> {
    if reference_price <= 0.0 {
        return Err(PyValueError::new_err(format!(
            "reference price must be > 0 — reference_price={reference_price} delta_notional={delta_notional}"
        )));
    }
    Ok((delta_notional.abs() / reference_price).floor() as i64)
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(execution_price, m)?)?;
    m.add_function(wrap_pyfunction!(floor_delta_shares, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn limit_buy_rules() {
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
}
