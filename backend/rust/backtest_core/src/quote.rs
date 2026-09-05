use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

// ---------------------------------------------------------------------------
// 6b: 견적 산술 (broker.BrokerSim.quote의 수치 부분) + 매수 여력 누산기
// ---------------------------------------------------------------------------

/// 십진 문자열(예: "0.07")을 (분자, 10^k 분모)로 파싱한다 — Python `Decimal(str(p))`와 동일 값.
pub(crate) fn parse_decimal_ratio(text: &str) -> PyResult<(i128, i128)> {
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
    let overflow = || PyValueError::new_err(format!("decimal scale out of range — text={text:?}"));
    if scale >= 0 {
        let den = 10_i128.checked_pow(u32::try_from(scale).map_err(|_| overflow())?);
        Ok((numerator, den.ok_or_else(overflow)?))
    } else {
        let factor = 10_i128
            .checked_pow(u32::try_from(-scale).map_err(|_| overflow())?)
            .ok_or_else(overflow)?;
        Ok((numerator.checked_mul(factor).ok_or_else(overflow)?, 1))
    }
}

/// floor(volume × participation) — participation은 십진 문자열 (정확한 정수 산술).
#[pyfunction]
pub(crate) fn liquidity_cap(volume: i64, participation: &str) -> PyResult<i64> {
    let (num, den) = parse_decimal_ratio(participation)?;
    if num < 0 {
        return Err(PyValueError::new_err(format!(
            "participation must be >= 0 — participation={participation}"
        )));
    }
    let cap = (volume as i128).checked_mul(num).ok_or_else(|| {
        PyValueError::new_err(format!(
            "liquidity cap overflow — volume={volume} participation={participation}"
        ))
    })? / den;
    i64::try_from(cap).map_err(|_| {
        PyValueError::new_err(format!(
            "liquidity cap out of i64 range — volume={volume} participation={participation}"
        ))
    })
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
pub(crate) fn quote_numbers(
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

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(liquidity_cap, m)?)?;
    m.add_function(wrap_pyfunction!(quote_numbers, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decimal_ratio_and_cap_are_exact_and_checked() {
        assert_eq!(liquidity_cap(90, "0.7").unwrap(), 63);
        assert_eq!(liquidity_cap(1000, "7e-02").unwrap(), 70);
        assert_eq!(liquidity_cap(1000, ".5").unwrap(), 500);
        assert!(liquidity_cap(1000, "5e-324").is_err());
        assert!(liquidity_cap(1000, "1e+40").is_err());
    }
}
