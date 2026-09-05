use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::HashMap;

// ---------------------------------------------------------------------------
// 포트폴리오 회계 (engine/portfolio.Portfolio와 동일)
// ---------------------------------------------------------------------------

/// 스냅샷 포지션 행: `(key, quantity, average_price, market_price, market_value, unrealized_pnl)`.
pub(crate) type PositionRow = (String, i64, f64, f64, f64, f64);
/// 스냅샷: `(cash, positions, equity, gross_exposure)`.
pub(crate) type SnapshotTuple = (f64, Vec<PositionRow>, f64, f64);

#[derive(Clone, Debug)]
struct Ledger {
    quantity: i64,
    average_price: f64,
}

/// 현금·보유 원장. Fill / 비용 / 자본변동 적용 시점에만 상태가 변한다.
#[pyclass]
pub(crate) struct Portfolio {
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
    pub(crate) fn new(initial_cash: f64, allow_short: bool, allow_margin: bool) -> Self {
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
    pub(crate) fn apply(
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
    pub(crate) fn charge(&mut self, amount: f64) -> PyResult<()> {
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
    pub(crate) fn apply_corporate_action(
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
    pub(crate) fn mark(&mut self, closes: Vec<(String, f64)>) {
        for (key, close) in closes {
            self.marks.insert(key, close);
        }
    }

    #[getter]
    pub(crate) fn cash(&self) -> f64 {
        self.cash
    }

    pub(crate) fn held_qty(&self, key: &str) -> i64 {
        self.ledger_index(key)
            .map(|i| self.ledgers[i].1.quantity)
            .unwrap_or(0)
    }

    pub(crate) fn average_price(&self, key: &str) -> Option<f64> {
        self.ledger_index(key)
            .map(|i| self.ledgers[i].1.average_price)
    }

    /// 스냅샷: `(cash, positions, equity, gross_exposure)`. 포지션은 원장 삽입 순서(Python dict와
    /// 동일)이고, equity·gross는 Python의 `cash + sum(mv)` / `sum(|mv|)`와 같은 결합 순서로
    /// 누산한다 (비트 동일성).
    pub(crate) fn snapshot(&self) -> PyResult<SnapshotTuple> {
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

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Portfolio>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn equity_identity_after_fills_and_mark() {
        let mut portfolio = Portfolio::new(100_000.0, false, false);
        portfolio
            .apply("XKRX:005930", "buy", 10, 100.0, 100.0)
            .unwrap();
        portfolio.mark(vec![("XKRX:005930".to_string(), 120.0)]);

        let (cash, positions, equity, gross) = portfolio.snapshot().unwrap();

        assert_eq!(cash, 98_900.0);
        assert_eq!(positions[0].4, 1_200.0);
        assert_eq!(equity, cash + 1_200.0);
        assert!((gross - 1_200.0 / equity).abs() < 1e-12);
    }

    #[test]
    fn long_to_short_flip_resets_average() {
        let mut portfolio = Portfolio::new(10_000.0, true, false);
        portfolio.apply("k", "buy", 5, 100.0, 0.0).unwrap();
        portfolio.apply("k", "sell", 8, 120.0, 0.0).unwrap();

        assert_eq!(portfolio.held_qty("k"), -3);
        assert_eq!(portfolio.average_price("k"), Some(120.0));
        assert_eq!(portfolio.cash(), 10_000.0 - 500.0 + 960.0);
    }
}
