use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::HashMap;

/// 세션 안 매수 여력 = leverage × equity − 총노출 (core.PythonBuyingPower와 동일 연산 순서).
#[pyclass]
pub(crate) struct BuyingPower {
    leverage: f64,
    equity: f64,
    gross: f64,
    quantities: HashMap<String, i64>,
    marks: HashMap<String, f64>,
}

type PowerCheckpoint = (f64, f64, Vec<(String, i64)>, Vec<(String, f64)>);

#[pymethods]
impl BuyingPower {
    /// positions: `(key, quantity, market_price, market_value)` — 스냅샷 포지션.
    #[new]
    fn new(equity: f64, leverage: f64, positions: Vec<(String, i64, f64, f64)>) -> Self {
        let mut gross = 0.0_f64;
        let mut quantities = HashMap::new();
        let mut marks = HashMap::new();
        for (key, quantity, mark, market_value) in positions {
            gross += market_value.abs();
            quantities.insert(key.clone(), quantity);
            marks.insert(key, mark);
        }
        Self {
            leverage,
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

    pub(crate) fn quantity_of(&self, key: &str) -> i64 {
        *self.quantities.get(key).unwrap_or(&0)
    }

    pub(crate) fn consume(
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
        let old = self.quantity_of(key);
        let new = if buy { old + quantity } else { old - quantity };
        let old_mark = *self.marks.get(key).unwrap_or(&price);
        self.gross += (new.abs() as f64) * price - (old.abs() as f64) * old_mark;
        self.equity += (old as f64) * (price - old_mark) - fee;
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
        )
    }

    pub(crate) fn restore(&mut self, state: PowerCheckpoint) {
        let (equity, gross, quantities, marks) = state;
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
