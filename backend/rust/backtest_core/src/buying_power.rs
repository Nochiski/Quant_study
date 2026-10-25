use crate::power_projection::{project_execution, summarize, ExecutionState};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::HashMap;

/// 후보 검증과 확정 소비가 같은 연산 순서로 장부를 갱신한다.
pub(crate) fn project_valuation(
    equity: f64,
    gross: f64,
    old: i64,
    new: i64,
    old_mark: f64,
    price: f64,
    cost: f64,
) -> (f64, f64) {
    (
        equity + (old as f64 * (price - old_mark) - cost),
        gross + (new.abs() as f64 * price - old.abs() as f64 * old_mark),
    )
}

/// 세션 안 매수 여력 = leverage × equity − 총노출 (core.PythonBuyingPower와 동일 연산 순서).
#[pyclass]
pub(crate) struct BuyingPower {
    leverage: f64,
    cash: f64,
    allow_margin: bool,
    values: Vec<(String, f64)>,
    equity: f64,
    gross: f64,
    quantities: HashMap<String, i64>,
    marks: HashMap<String, f64>,
}

type PowerCheckpoint = (
    f64,
    f64,
    Vec<(String, i64)>,
    Vec<(String, f64)>,
    f64,
    Vec<(String, f64)>,
);

#[pymethods]
impl BuyingPower {
    /// positions: `(key, quantity, market_price, market_value)` — 스냅샷 포지션.
    #[new]
    #[pyo3(signature = (equity, leverage, positions, cash=None, allow_margin=false))]
    pub(crate) fn new(
        equity: f64,
        leverage: f64,
        positions: Vec<(String, i64, f64, f64)>,
        cash: Option<f64>,
        allow_margin: bool,
    ) -> Self {
        let mut values = Vec::new();
        let mut quantities = HashMap::new();
        let mut marks = HashMap::new();
        for (key, quantity, mark, market_value) in positions {
            values.push((key.clone(), market_value));
            quantities.insert(key.clone(), quantity);
            marks.insert(key, mark);
        }
        let (total, gross) = summarize(0.0, values.iter().map(|row| row.1));
        Self {
            leverage,
            cash: cash.unwrap_or(equity - total),
            allow_margin,
            values,
            equity,
            gross,
            quantities,
            marks,
        }
    }

    #[getter]
    pub(crate) fn available(&self) -> f64 {
        self.leverage * self.equity - self.gross
    }

    #[getter]
    pub(crate) fn leverage(&self) -> f64 {
        self.leverage
    }

    #[getter]
    pub(crate) fn valuation(&self) -> (f64, f64) {
        (self.equity, self.gross)
    }

    pub(crate) fn mark_of(&self, key: &str) -> Option<f64> {
        self.marks.get(key).copied()
    }

    pub(crate) fn quantity_of(&self, key: &str) -> i64 {
        *self.quantities.get(key).unwrap_or(&0)
    }

    pub(crate) fn execution_state(&self, key: &str) -> ExecutionState {
        (
            self.cash,
            self.values.iter().map(|row| row.1).collect(),
            self.values
                .iter()
                .position(|row| row.0 == key)
                .unwrap_or(self.values.len()),
            self.allow_margin,
        )
    }

    #[pyo3(signature = (key, side, quantity, price, fee, tax=0.0))]
    pub(crate) fn consume(
        &mut self,
        key: &str,
        side: &str,
        quantity: i64,
        price: f64,
        fee: f64,
        tax: f64,
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
        let state = self.execution_state(key);
        (self.cash, self.equity, self.gross) =
            project_execution(&state, buy, quantity, price, fee, tax, new, price);
        if state.2 < self.values.len() {
            if new == 0 {
                self.values.remove(state.2);
            } else {
                self.values[state.2].1 = new as f64 * price;
            }
        } else if new != 0 {
            self.values.push((key.to_string(), new as f64 * price));
        }
        self.marks.insert(key.to_string(), price);
        self.quantities.insert(key.to_string(), new);
        Ok(())
    }

    pub(crate) fn checkpoint(&self) -> PowerCheckpoint {
        (
            self.equity,
            self.gross,
            self.quantities
                .iter()
                .map(|(k, v)| (k.clone(), *v))
                .collect(),
            self.marks.iter().map(|(k, v)| (k.clone(), *v)).collect(),
            self.cash,
            self.values.clone(),
        )
    }

    pub(crate) fn restore(&mut self, state: PowerCheckpoint) {
        let (equity, gross, quantities, marks, cash, values) = state;
        self.cash = cash;
        self.values = values;
        self.equity = equity;
        self.gross = gross;
        self.quantities = quantities.into_iter().collect();
        self.marks = marks.into_iter().collect();
    }
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<BuyingPower>()?;
    Ok(())
}
