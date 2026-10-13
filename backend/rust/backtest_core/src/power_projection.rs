/// 비용 차감은 견적과 실제 원장이 같은 연산을 사용한다.
pub(crate) fn project_cost(cash: f64, amount: f64) -> f64 {
    cash - amount
}

/// 실제 체결 원장과 후보 검증이 공유하는 현금 연산 순서다.
pub(crate) fn project_cash(cash: f64, buy: bool, notional: f64, fee: f64, tax: f64) -> f64 {
    let after_notional = if buy {
        cash - notional
    } else {
        cash + notional
    };
    let after_fee = after_notional - fee;
    if tax > 0.0 {
        project_cost(after_fee, tax)
    } else {
        after_fee
    }
}

/// 포지션 삽입 순서대로 합한 뒤 현금을 더한다. 원장 스냅샷과 동일하다.
pub(crate) fn summarize(cash: f64, values: impl Iterator<Item = f64>) -> (f64, f64) {
    let (mut total, mut gross) = (0.0, 0.0);
    for value in values {
        total += value;
        gross += value.abs();
    }
    (cash + total, gross)
}

pub(crate) type ExecutionState = (f64, Vec<f64>, usize, bool);

/// 대상 포지션은 제자리 갱신, 전량 청산은 제거, 신규 진입은 마지막에 추가한다.
#[allow(clippy::too_many_arguments)]
pub(crate) fn project_execution(
    state: &ExecutionState,
    buy: bool,
    quantity: i64,
    price: f64,
    fee: f64,
    tax: f64,
    new_quantity: i64,
    mark: f64,
) -> (f64, f64, f64) {
    let cash = project_cash(state.0, buy, quantity as f64 * price, fee, tax);
    let mut values = state.1.clone();
    if state.2 < values.len() {
        if new_quantity == 0 {
            values.remove(state.2);
        } else {
            values[state.2] = new_quantity as f64 * mark;
        }
    } else if new_quantity != 0 {
        values.push(new_quantity as f64 * mark);
    }
    let (equity, gross) = summarize(cash, values.into_iter());
    (cash, equity, gross)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cash_uses_actual_fill_then_fee_then_tax_order() {
        let cash = project_cash(300.03, true, 300.0, 0.03, 0.0);
        assert!(cash < 0.0);
        assert_eq!(cash, (300.03 - 300.0) - 0.03);
        assert_eq!(project_cash(0.0, false, 700.0, 35.0, 70.0), 595.0);
    }

    #[test]
    fn projection_preserves_position_order_and_closing_removal() {
        let state = (10.0, vec![1e16, 1.0, -1e16], 1, false);
        assert_eq!(
            project_execution(&state, false, 1, 2.0, 0.0, 0.0, 0, 2.0),
            (12.0, 12.0, 2e16)
        );
        let state = (660.0, vec![], 0, false);
        assert_eq!(
            project_execution(&state, false, 6, 100.0, 0.0, 60.0, -6, 100.0),
            (1200.0, 600.0, 600.0)
        );
    }
}
