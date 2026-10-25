use crate::capacity::Capacity;
use crate::power_projection::ExecutionState;
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

/// 견적의 수치 부분. 슬리피지(주당, ≥0)는 호출 측이 준다 (플러그인 포트).
///
/// 반환 `(price, quantity, slip_applied, status)`; status는
/// `filled | liquidity_limited | cash_limited | rejected_no_cash | not_filled | fok_rejected`.
/// 흐름은 Python과 같다: 유동성 캡 → 슬리피지·지정가 clip → 여력 캡(숏 진입분 포함) → FOK.
#[pyfunction]
#[pyo3(signature = (side, base_price, remaining, volume, participation, slip, limit_price, buying_power, held, fee_rate, fok, sell_tax_rate=0.0, leverage=1.0, held_mark=None, valuation=None, execution_state=None))]
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
    sell_tax_rate: f64,
    leverage: f64,
    held_mark: Option<f64>,
    valuation: Option<(f64, f64)>,
    execution_state: Option<ExecutionState>,
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
    // 체결 원장의 양수 가격 계약은 예산 탐색과 청산 허용보다 먼저 검증한다.
    if price <= 0.0 {
        return Ok((price, 0, applied, "rejected_no_cash".to_string()));
    }

    let closing = if buy { (-held).max(0) } else { held.max(0) };
    if quantity > closing || execution_state.is_some() {
        let (equity, gross) = valuation.unwrap_or((buying_power / leverage, 0.0));
        let mark = held_mark.unwrap_or(price);
        let affordable = Capacity {
            buy,
            price,
            held,
            fee_rate,
            sell_tax_rate,
            leverage,
            equity,
            gross,
            mark,
            state: execution_state.as_ref(),
        }
        .maximum(quantity);
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
    fn nonmonotone_cash_uses_the_largest_actual_feasible_quantity() {
        let tax = f64::from_bits(0.9_f64.to_bits() + 1);
        let result = quote_numbers(
            "sell",
            1.0,
            20,
            100,
            None,
            0.0,
            None,
            -1.0,
            30,
            0.1,
            false,
            tax,
            1.0,
            Some(1.0),
            Some((29.0, 31.0)),
            Some((-1e-18, vec![30.0, -1.0], 0, false)),
        )
        .unwrap();
        assert_eq!(result.1, 19);
    }

    #[test]
    fn cash_recovery_lower_bound_does_not_cut_off_feasible_sales() {
        // 비용으로 현금 -4, A10/B-1 보유: 자본5. 매도4주부터 현금이 회복되고,
        // 청산을 넘는 반전은 총노출5 이하인 14주까지 가능하다.
        for (remaining, expected) in [(3, 0), (4, 4), (5, 5), (10, 10), (15, 14)] {
            let result = quote_numbers(
                "sell",
                1.0,
                remaining,
                100,
                None,
                0.0,
                None,
                -6.0,
                10,
                0.0,
                false,
                0.0,
                1.0,
                Some(1.0),
                Some((5.0, 11.0)),
                Some((-4.0, vec![10.0, -1.0], 0, false)),
            )
            .unwrap();
            assert_eq!(result.1, expected);
        }
        // 매도 비용이 대금보다 크면 현금 상한을 따로 탐색한다.
        let result = quote_numbers(
            "sell",
            1.0,
            5,
            100,
            None,
            0.0,
            None,
            1.0,
            10,
            2.0,
            false,
            0.0,
            1.0,
            Some(1.0),
            Some((11.0, 10.0)),
            Some((1.0, vec![10.0], 0, false)),
        )
        .unwrap();
        assert_eq!(result.1, 1);
    }

    #[test]
    fn execution_cash_boundary_matches_ledger_without_tolerance() {
        for (cash, expected) in [(300.03, 2), (f64::from_bits(300.03_f64.to_bits() + 1), 3)] {
            let result = quote_numbers(
                "buy",
                100.0,
                3,
                100,
                None,
                0.0,
                None,
                cash,
                0,
                0.0001,
                false,
                0.0,
                1.0,
                None,
                Some((cash, 0.0)),
                Some((cash, vec![], 0, false)),
            )
            .unwrap();
            assert_eq!(result.1, expected);
        }
        // MARGIN 기능과 L=1은 별개다. 기존 노출 감소 청산의 음수 현금 허용을 보존한다.
        for (allow_margin, expected) in [(false, 0), (true, 1)] {
            let result = quote_numbers(
                "buy",
                100.0,
                1,
                100,
                None,
                0.0,
                None,
                -199.0,
                -1,
                0.0,
                false,
                0.0,
                1.0,
                Some(100.0),
                Some((-99.0, 100.0)),
                Some((1.0, vec![-100.0], 0, allow_margin)),
            )
            .unwrap();
            assert_eq!(result.1, expected);
        }
    }

    #[test]
    fn decimal_ratio_and_cap_are_exact_and_checked() {
        assert_eq!(liquidity_cap(90, "0.7").unwrap(), 63);
        assert_eq!(liquidity_cap(1000, "7e-02").unwrap(), 70);
        assert_eq!(liquidity_cap(1000, ".5").unwrap(), 500);
        assert!(liquidity_cap(1000, "5e-324").is_err());
        assert!(liquidity_cap(1000, "1e+40").is_err());
    }
}
