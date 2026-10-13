use crate::buying_power::project_valuation;
use crate::power_projection::{project_execution, ExecutionState};

/// 실제 순차 부동소수 원장으로 수량을 판정한다. 단조성을 전제하지 않는다.
pub(crate) struct Capacity<'a> {
    pub buy: bool,
    pub price: f64,
    pub held: i64,
    pub fee_rate: f64,
    pub sell_tax_rate: f64,
    pub leverage: f64,
    pub equity: f64,
    pub gross: f64,
    pub mark: f64,
    pub state: Option<&'a ExecutionState>,
}

impl Capacity<'_> {
    fn closing(&self) -> i64 {
        if self.buy {
            (-self.held).max(0)
        } else {
            self.held.max(0)
        }
    }

    fn new_quantity(&self, quantity: i64) -> i64 {
        if self.buy {
            self.held + quantity
        } else {
            self.held - quantity
        }
    }

    fn fees(&self, quantity: i64) -> (f64, f64) {
        let notional = quantity as f64 * self.price;
        (
            notional * self.fee_rate,
            if self.buy {
                0.0
            } else {
                notional * self.sell_tax_rate
            },
        )
    }

    fn feasible(&self, quantity: i64) -> bool {
        let (fee, tax) = self.fees(quantity);
        let new = self.new_quantity(quantity);
        let (equity, gross) = if let Some(state) = self.state {
            let (cash, equity, gross) = project_execution(
                state, self.buy, quantity, self.price, fee, tax, new, self.price,
            );
            if !state.3 && (cash.is_nan() || cash < 0.0) {
                return false;
            }
            (equity, gross)
        } else {
            project_valuation(
                self.equity,
                self.gross,
                self.held,
                new,
                self.mark,
                self.price,
                fee + tax,
            )
        };
        quantity <= self.closing() || self.leverage * equity - gross >= 0.0
    }

    /// 각 실제 IEEE 연산은 피연산자별 단조다. 큰 양수항/작은 차감항을 원래 순서로
    /// 결합하면 구간 상한이다. 이는 실수 산술 근사가 아니며 허용 오차를 더하지 않는다.
    fn impossible(&self, low: i64, high: i64) -> bool {
        let notional_low = low as f64 * self.price;
        let notional_high = high as f64 * self.price;
        let (fee_low, tax_low) = self.fees(low);
        let (fee_high, tax_high) = self.fees(high);
        let fee_min = fee_low.min(fee_high);
        let tax_min = tax_low.min(tax_high);
        let new_low = self.new_quantity(low);
        let new_high = self.new_quantity(high);
        let value_low = new_low as f64 * self.price;
        let value_high = new_high as f64 * self.price;
        let value_max = value_low.max(value_high);
        let abs_min = if new_low.min(new_high) <= 0 && new_low.max(new_high) >= 0 {
            0.0
        } else {
            value_low.abs().min(value_high.abs())
        };
        let (equity_upper, gross_lower) = if let Some(state) = self.state {
            let after_notional = if self.buy {
                state.0 - notional_low
            } else {
                state.0 + notional_high
            };
            let after_fee = after_notional - fee_min;
            let cash_upper = if tax_min > 0.0 {
                after_fee - tax_min
            } else {
                after_fee
            };
            if !state.3 && cash_upper < 0.0 {
                return true;
            }
            let (mut total_upper, mut gross_lower) = (0.0, 0.0);
            for (index, value) in state.1.iter().enumerate() {
                total_upper += if index == state.2 { value_max } else { *value };
                gross_lower += if index == state.2 {
                    abs_min
                } else {
                    value.abs()
                };
            }
            if state.2 == state.1.len() {
                total_upper += value_max;
                gross_lower += abs_min;
            }
            (cash_upper + total_upper, gross_lower)
        } else {
            let cost_min = fee_min + tax_min;
            (
                self.equity + (self.held as f64 * (self.price - self.mark) - cost_min),
                self.gross + (abs_min - self.held.abs() as f64 * self.mark),
            )
        };
        // 청산 허용 구간이 섞여 있으면 위험 한도로 구간 전체를 버릴 수 없다.
        low > self.closing() && self.leverage * equity_upper - gross_lower < 0.0
    }

    /// 큰 수량부터 검사하고 불가능함이 증명된 구간만 제거한다. 상쇄가 극심한 입력은
    /// 최악에 전수 검사가 필요하지만 상한/오차를 임의로 추가하지 않는다.
    pub(crate) fn maximum(&self, quantity: i64) -> i64 {
        let mut pending = vec![(1, quantity)];
        while let Some((low, high)) = pending.pop() {
            if low > high {
                continue;
            }
            if self.feasible(high) {
                return high;
            }
            let upper = high - 1;
            if low > upper || self.impossible(low, upper) {
                continue;
            }
            let middle = low + (upper - low) / 2;
            pending.push((low, middle));
            if middle < upper {
                pending.push((middle + 1, upper));
            }
        }
        0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn interval_pruning_preserves_exhaustive_float_oracle() {
        for cash in [-4.0, -1e-18, 0.0, 100.0] {
            for held in [-5, 0, 5, 30] {
                for buy in [false, true] {
                    for fee_rate in [0.0, 0.1, 1.0, 2.0] {
                        for sell_tax_rate in [0.0, f64::from_bits(0.9_f64.to_bits() + 1)] {
                            let state = (cash, vec![held as f64 * 2.0, -1.0], 0, false);
                            let capacity = Capacity {
                                buy,
                                price: 1.0,
                                held,
                                fee_rate,
                                sell_tax_rate,
                                leverage: 1.0,
                                equity: cash + held as f64 * 2.0 - 1.0,
                                gross: (held as f64 * 2.0).abs() + 1.0,
                                mark: 2.0,
                                state: Some(&state),
                            };
                            let expected =
                                (1..=40).rev().find(|q| capacity.feasible(*q)).unwrap_or(0);
                            assert_eq!(capacity.maximum(40), expected);
                            for (low, high) in [(1, 40), (2, 19), (7, 15), (20, 39)] {
                                if capacity.impossible(low, high) {
                                    assert!((low..=high).all(|q| !capacity.feasible(q)));
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
