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
            if !state.3
                && !self.buy
                && state.0 <= 0.0
                && notional_low >= f64::MIN_POSITIVE
                && ((self.fee_rate > 1.0 && self.sell_tax_rate >= 0.0)
                    || (self.sell_tax_rate > 1.0 && self.fee_rate >= 0.0))
            {
                // 유한 normal n은 최소 r-1=2^-52의 증가가 위쪽 ULP 이상이므로
                // r>1에서 fl(n*r)>n이다. fl(cash+n)<=n이어서 개별 비용만으로 음수다.
                // +inf 대금은 실제 순차 계산의 inf-inf/0*inf가 NaN을 만들어 불허다.
                // subnormal은 곱이 n에 남을 수 있어 이 증명에서 제외한다.
                return true;
            }
            if !state.3
                && !self.buy
                && state.0 <= 0.0
                && notional_low >= 2.0 * f64::MIN_POSITIVE
                && ((self.fee_rate >= 0.5 && self.sell_tax_rate > 0.5)
                    || (self.fee_rate > 0.5 && self.sell_tax_rate >= 0.5))
            {
                // h=n/2는 정확한 normal이다. fee>=h/tax>h는 잔액<=h 뒤 음수다.
                // fee>h/tax>=h도 fee<=n에서 Sterbenz로 n-fee<h가 정확하다.
                return true;
            }
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
    fn dominated_cost_prunes_a_trillion_share_interval() {
        let rate = f64::from_bits(1.0_f64.to_bits() + 1);
        let quantity = 1_000_000_000_000_i64;
        for cash in [-1e-18, 0.0] {
            for price in [f64::MIN_POSITIVE, 1.0, 1e308] {
                for (fee_rate, sell_tax_rate) in [
                    (rate, 0.0),
                    (0.0, rate),
                    (0.5, f64::from_bits(0.5_f64.to_bits() + 1)),
                    (f64::from_bits(0.5_f64.to_bits() + 1), 0.5),
                ] {
                    if price < 2.0 * f64::MIN_POSITIVE && fee_rate < 1.0 && sell_tax_rate < 1.0 {
                        continue;
                    }
                    let state = (cash, vec![quantity as f64 * price], 0, false);
                    let capacity = Capacity {
                        buy: false,
                        price,
                        held: quantity,
                        fee_rate,
                        sell_tax_rate,
                        leverage: 1.0,
                        equity: 0.0,
                        gross: 0.0,
                        mark: price,
                        state: Some(&state),
                    };
                    assert!(capacity.impossible(1, quantity - 1));
                    assert_eq!(capacity.maximum(quantity), 0);
                }
            }
        }
    }

    #[test]
    fn strict_product_proof_excludes_subnormal_rounding() {
        let rate = f64::from_bits(1.0_f64.to_bits() + 1);
        for exponent in 1_u64..=2046 {
            let power = exponent << 52;
            for bits in [power - 1, power, power + 1] {
                let n = f64::from_bits(bits);
                if n >= f64::MIN_POSITIVE {
                    assert!(n * rate > n);
                }
                if n >= 2.0 * f64::MIN_POSITIVE {
                    let half = n * 0.5;
                    let higher_half = n * f64::from_bits(0.5_f64.to_bits() + 1);
                    assert!(higher_half > half);
                    assert!(n - higher_half < half);
                }
            }
        }
        assert_eq!(f64::MAX * rate, f64::INFINITY);
        let price = f64::MIN_POSITIVE / 2.0;
        assert_eq!(price * rate, price);
        let state = (0.0, vec![2.0 * price], 0, false);
        let capacity = Capacity {
            buy: false,
            price,
            held: 2,
            fee_rate: rate,
            sell_tax_rate: 0.0,
            leverage: 1.0,
            equity: 0.0,
            gross: 0.0,
            mark: price,
            state: Some(&state),
        };
        assert!(!capacity.impossible(1, 1));
        assert_eq!(capacity.maximum(2), 1);
    }

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
