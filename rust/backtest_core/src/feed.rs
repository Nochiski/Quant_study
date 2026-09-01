use crate::persistent_router::CloseWire;
use crate::session::BarTuple;
use pyo3::exceptions::{PyIndexError, PyValueError};
use pyo3::prelude::*;
use std::collections::HashMap;

/// 실행 시작 시 한 번 적재되는 columnar feed와 u32 instrument registry.
pub(crate) struct PersistentFeed {
    keys: Vec<String>,
    symbols: Vec<String>,
    sessions: Vec<String>,
    offsets: Vec<usize>,
    instrument_ids: Vec<u32>,
    opens: Vec<f64>,
    highs: Vec<f64>,
    lows: Vec<f64>,
    closes: Vec<f64>,
    volumes: Vec<i64>,
    current_session: Option<usize>,
}

impl PersistentFeed {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new(
        keys: Vec<String>,
        symbols: Vec<String>,
        sessions: Vec<String>,
        offsets: Vec<usize>,
        instrument_ids: Vec<u32>,
        opens: Vec<f64>,
        highs: Vec<f64>,
        lows: Vec<f64>,
        closes: Vec<f64>,
        volumes: Vec<i64>,
    ) -> PyResult<Self> {
        if keys.len() != symbols.len() {
            return Err(PyValueError::new_err(format!(
                "feed registry length mismatch — keys={} symbols={}",
                keys.len(),
                symbols.len()
            )));
        }
        let rows = instrument_ids.len();
        for (name, length) in [
            ("opens", opens.len()),
            ("highs", highs.len()),
            ("lows", lows.len()),
            ("closes", closes.len()),
            ("volumes", volumes.len()),
        ] {
            if length != rows {
                return Err(PyValueError::new_err(format!(
                    "feed column length mismatch — instrument_ids={rows} {name}={length}"
                )));
            }
        }
        if offsets.len() != sessions.len() + 1
            || offsets.first().copied() != Some(0)
            || offsets.last().copied() != Some(rows)
            || offsets.windows(2).any(|pair| pair[0] > pair[1])
        {
            return Err(PyValueError::new_err(format!(
                "invalid feed session offsets — sessions={} offsets={offsets:?} rows={rows}",
                sessions.len()
            )));
        }
        if let Some(id) = instrument_ids
            .iter()
            .copied()
            .find(|id| *id as usize >= keys.len())
        {
            return Err(PyValueError::new_err(format!(
                "feed instrument id out of range — id={id} registry={}",
                keys.len()
            )));
        }
        Ok(Self {
            keys,
            symbols,
            sessions,
            offsets,
            instrument_ids,
            opens,
            highs,
            lows,
            closes,
            volumes,
            current_session: None,
        })
    }

    pub(crate) fn set_current(&mut self, index: usize) -> PyResult<()> {
        if index >= self.sessions.len() {
            return Err(PyIndexError::new_err(format!(
                "feed session index out of range — index={index} sessions={}",
                self.sessions.len()
            )));
        }
        self.current_session = Some(index);
        Ok(())
    }

    fn current_index(&self) -> PyResult<usize> {
        self.current_session.ok_or_else(|| {
            PyValueError::new_err("persistent feed has no current session — process market first")
        })
    }

    fn row_range(&self, index: usize) -> std::ops::Range<usize> {
        self.offsets[index]..self.offsets[index + 1]
    }

    pub(crate) fn session_market(&self, index: usize) -> (String, HashMap<String, BarTuple>) {
        let bars = self
            .row_range(index)
            .map(|row| {
                let instrument = self.instrument_ids[row] as usize;
                (
                    self.keys[instrument].clone(),
                    (
                        self.opens[row],
                        self.highs[row],
                        self.lows[row],
                        self.volumes[row],
                    ),
                )
            })
            .collect();
        (self.sessions[index].clone(), bars)
    }

    pub(crate) fn current_closes(&self) -> PyResult<HashMap<String, CloseWire>> {
        let index = self.current_index()?;
        Ok(self
            .row_range(index)
            .map(|row| {
                let instrument = self.instrument_ids[row] as usize;
                (
                    self.keys[instrument].clone(),
                    (self.symbols[instrument].clone(), self.closes[row]),
                )
            })
            .collect())
    }

    pub(crate) fn current_marks(&self) -> PyResult<Vec<(String, f64)>> {
        let index = self.current_index()?;
        Ok(self
            .row_range(index)
            .map(|row| {
                let instrument = self.instrument_ids[row] as usize;
                (self.keys[instrument].clone(), self.closes[row])
            })
            .collect())
    }

    pub(crate) fn current_session_count(&self) -> usize {
        self.current_session.map_or(0, |index| index + 1)
    }

    pub(crate) fn history_window(
        &self,
        keys: &[String],
        field: &str,
        lookback: usize,
        end: &str,
    ) -> PyResult<(Vec<String>, Vec<f64>)> {
        let available = self
            .sessions
            .partition_point(|session| session.as_str() <= end);
        if available < lookback {
            return Err(PyValueError::new_err(format!(
                "insufficient_history: requested lookback={lookback} available={available} end={end}"
            )));
        }
        let ids: Vec<Option<u32>> = keys
            .iter()
            .map(|key| {
                self.keys
                    .iter()
                    .position(|known| known == key)
                    .map(|id| id as u32)
            })
            .collect();
        let selected = (available - lookback)..available;
        let timestamps = self.sessions[selected.clone()].to_vec();
        let mut values = Vec::with_capacity(lookback * keys.len());
        for session in selected {
            let rows: HashMap<u32, usize> = self
                .row_range(session)
                .map(|row| (self.instrument_ids[row], row))
                .collect();
            for id in &ids {
                let value = id
                    .and_then(|id| rows.get(&id).copied())
                    .map(|row| match field {
                        "open" => self.opens[row],
                        "high" => self.highs[row],
                        "low" => self.lows[row],
                        "close" => self.closes[row],
                        "volume" => self.volumes[row] as f64,
                        _ => f64::NAN,
                    })
                    .unwrap_or(f64::NAN);
                values.push(value);
            }
        }
        if !matches!(field, "open" | "high" | "low" | "close" | "volume") {
            return Err(PyValueError::new_err(format!(
                "unknown history field — field={field:?}"
            )));
        }
        Ok((timestamps, values))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn columnar_feed_uses_registry_and_offsets() {
        let mut feed = PersistentFeed::new(
            vec!["A".into(), "B".into()],
            vec!["AAA".into(), "BBB".into()],
            vec!["D1".into(), "D2".into()],
            vec![0, 2, 3],
            vec![0, 1, 1],
            vec![10.0, 20.0, 21.0],
            vec![11.0, 21.0, 22.0],
            vec![9.0, 19.0, 20.0],
            vec![10.5, 20.5, 21.5],
            vec![100, 200, 300],
        )
        .unwrap();
        feed.set_current(1).unwrap();
        assert_eq!(feed.current_closes().unwrap()["B"], ("BBB".into(), 21.5));
        assert_eq!(feed.session_market(0).1.len(), 2);
    }
}
