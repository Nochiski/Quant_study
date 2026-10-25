use crate::buying_power::project_valuation;
use crate::power_projection::{project_execution, ExecutionState};

// IEEE 이진 배정밀도 반올림 단위 u와 최소 subnormal(언더플로 절대 오차 상한 η 이상).
const UNIT: f64 = 1.0 / 9_007_199_254_740_992.0;
const TINY: f64 = 5e-324;
// 현금 원장은 연산 5회다. 끝점 원장값과 실수 모형의 양방향 차이(2·5u)를 덮는 배수.
const CASH_ERROR: f64 = 16.0;
// 위험 평가의 포지션 합산 외 연산 수(대금·비용·현금·평가액·레버리지·총노출) 상한.
const RISK_OPERATIONS: usize = 12;
// 탐색 반복 예산(Python `SEARCH_BUDGET`과 같다). 증명으로 지울 수 없는 후보는 매도 비용률 합이
// 100%를 반올림 오차만큼 넘는 상쇄 입력에서만 남는다.
pub(crate) const SEARCH_BUDGET: usize = 4096;

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

    fn ledger(&self, state: &ExecutionState, quantity: i64) -> (f64, f64, f64) {
        let (fee, tax) = self.fees(quantity);
        project_execution(
            state,
            self.buy,
            quantity,
            self.price,
            fee,
            tax,
            self.new_quantity(quantity),
            self.price,
        )
    }

    fn feasible(&self, quantity: i64) -> bool {
        let (fee, tax) = self.fees(quantity);
        let new = self.new_quantity(quantity);
        let (equity, gross) = if let Some(state) = self.state {
            let (cash, equity, gross) = self.ledger(state, quantity);
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

    /// 같은 대금 n에서 나온 대금·비용의 상관을 쓰는 구간 불가능 증명(Python `_Budget`과 같은 연산 순서).
    /// 실수 모형의 현금 c±n−f·n−t·n은 n에 1차, 위험 L·평가액−총노출은 q에 오목이라 구간 최댓값은
    /// 끝점(위험은 보유 0 꺾임점 포함)에서 나온다. 실제 원장값과 실수 모형의 차이는 연산마다
    /// u·|결과|+η 이하라, 끝점 원장값에 그 합의 두 배를 더해도 음수이면 구간 전체가 불가능하다.
    fn correlated_impossible(&self, state: &ExecutionState, low: i64, high: i64) -> bool {
        let cash = state.0;
        let notional_high = high as f64 * self.price;
        if !notional_high.is_finite() || !cash.is_finite() {
            return false;
        }
        let lows = self.ledger(state, low);
        let highs = self.ledger(state, high);
        if !(lows.0.is_finite() && highs.0.is_finite()) {
            return false;
        }
        let costs = 1.0 + self.fee_rate + if self.buy { 0.0 } else { self.sell_tax_rate };
        if !state.3 {
            let scale = cash.abs() + notional_high * costs;
            let bound = lows.0.max(highs.0) + (CASH_ERROR * UNIT * scale + CASH_ERROR * TINY);
            if bound < 0.0 {
                return true;
            }
        }
        if low <= self.closing() || self.leverage < 0.0 {
            return false;
        }
        let kink = if self.buy { -self.held } else { self.held };
        let mut risk = f64::NEG_INFINITY;
        for (_, equity, gross) in [lows, highs]
            .into_iter()
            .chain((low < kink && kink < high).then(|| self.ledger(state, kink)))
        {
            let value = self.leverage * equity - gross;
            if !value.is_finite() {
                return false;
            }
            risk = risk.max(value);
        }
        let held_value = (self.new_quantity(low) as f64)
            .abs()
            .max((self.new_quantity(high) as f64).abs());
        let previous: f64 = state.1.iter().map(|value| value.abs()).sum();
        let scale = (self.leverage + 1.0)
            * (cash.abs() + 2.0 * notional_high * costs + previous + 2.0 * held_value * self.price);
        if !scale.is_finite() {
            return false;
        }
        let terms = (2 * (state.1.len() + RISK_OPERATIONS)) as f64;
        risk + (terms * UNIT * scale + terms * (self.leverage + 1.0) * TINY) < 0.0
    }

    /// 구간 안의 어떤 수량도 실제 원장을 통과할 수 없음이 증명될 때만 구간을 버린다.
    fn impossible(&self, low: i64, high: i64) -> bool {
        if let Some(state) = self.state {
            if self.correlated_impossible(state, low, high) {
                return true;
            }
        }
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

    /// 큰 수량부터 검사하고 불가능함이 증명된 구간만 제거한다. 처음 허용된 수량이 정답이며,
    /// 반복이 `SEARCH_BUDGET`을 넘으면 그때까지 허용된 수량이 없으므로 0(체결 없음)이다.
    pub(crate) fn maximum(&self, quantity: i64) -> i64 {
        let mut pending = vec![(1, quantity)];
        let mut steps = 0;
        while let Some((low, high)) = pending.pop() {
            steps += 1;
            if steps > SEARCH_BUDGET {
                return 0;
            }
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

    fn cancelling_sell(quantity: i64, sell_tax_rate: f64, state: &ExecutionState) -> Capacity<'_> {
        Capacity {
            buy: false,
            price: 1.0,
            held: quantity,
            fee_rate: 0.25,
            sell_tax_rate,
            leverage: 1.0,
            equity: 0.0,
            gross: 0.0,
            mark: 1.0,
            state: Some(state),
        }
    }

    #[test]
    fn correlated_bound_prunes_cost_above_notional() {
        let quantity = 1_000_000_000_000_i64;
        let state = (-1e-18, vec![quantity as f64], 0, false);
        for excess in [1e-12, 1e-9, 1e-6, 1e-3] {
            let capacity = cancelling_sell(quantity, 0.75 + excess, &state);
            assert_eq!(capacity.maximum(quantity), 0);
        }
    }

    #[test]
    fn rounding_level_cancellation_stops_within_search_budget() {
        let quantity = 1_i64 << 62;
        let state = (-1e-18, vec![quantity as f64], 0, false);
        let capacity = cancelling_sell(quantity, f64::from_bits(0.75_f64.to_bits() + 1), &state);
        // 반올림이 유리한 수량은 실제 원장을 통과한다. 예산 안에서 찾은 값은 원장이 허용한다.
        let found = capacity.maximum(quantity);
        assert!(found == 0 || capacity.feasible(found));
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
