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
    /// key → instrument id. 세션 루프가 주문·포지션 key를 wire의 정수 id로 바꿀 때 쓴다.
    key_index: HashMap<String, u32>,
    /// key → symbol. 라우팅이 결정마다 쓰는 심볼 폴백 표를 적재 시 한 번만 만든다.
    symbol_by_key: HashMap<String, String>,
    /// `session * keys.len() + instrument_id` → 행 번호. bar가 없으면 `u32::MAX`.
    /// 세션별 행 구간을 매번 훑던 `row_of`를 O(1)로 만든다. 4B × 세션 × 종목이 든다
    /// (300종목 1,231세션 = 1.5MiB).
    row_index: Vec<u32>,
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
        let key_index: HashMap<String, u32> = keys
            .iter()
            .enumerate()
            .map(|(index, key)| (key.clone(), index as u32))
            .collect();
        // 같은 key가 서로 다른 symbol로 두 번 등록되면 등록부 표(`symbol_by_key`)와 그날 bar가
        // 실어 나르는 symbol이 갈라진다. 라우팅은 이제 등록부 표만 보므로 여기서 막는다.
        let mut symbol_by_key: HashMap<String, String> = HashMap::with_capacity(keys.len());
        for (key, symbol) in keys.iter().zip(symbols.iter()) {
            if let Some(known) = symbol_by_key.get(key) {
                if known != symbol {
                    return Err(PyValueError::new_err(format!(
                        "feed registry maps one key to two symbols — key={key} symbols=({known}, {symbol}) registry={}",
                        keys.len()
                    )));
                }
                continue;
            }
            symbol_by_key.insert(key.clone(), symbol.clone());
        }
        // 행 번호를 u32로 담으므로 행 수가 u32 범위를 넘으면 인덱스를 만들 수 없다.
        // `u32::MAX`는 "bar 없음" 표식이라 행 번호로 쓸 수 없다.
        if rows >= u32::MAX as usize {
            return Err(PyValueError::new_err(format!(
                "feed has too many rows for the row index — rows={rows} limit={}",
                u32::MAX as usize - 1
            )));
        }
        let slots = sessions.len().checked_mul(keys.len()).ok_or_else(|| {
            PyValueError::new_err(format!(
                "feed row index size overflows usize — sessions={} registry={}",
                sessions.len(),
                keys.len()
            ))
        })?;
        let mut row_index = vec![u32::MAX; slots];
        for (session, window) in offsets.windows(2).enumerate() {
            let base = session * keys.len();
            for (offset, instrument_id) in instrument_ids[window[0]..window[1]].iter().enumerate() {
                let slot = base + *instrument_id as usize;
                // 한 세션에 같은 종목 행이 둘이면 앞선 행을 남긴다 — 선형 탐색이 `find`로
                // 첫 행을 고르던 동작과 같다.
                if row_index[slot] == u32::MAX {
                    row_index[slot] = (window[0] + offset) as u32;
                }
            }
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
            key_index,
            symbol_by_key,
            row_index,
        })
    }

    pub(crate) fn session_len(&self) -> usize {
        self.sessions.len()
    }

    pub(crate) fn instrument_id(&self, key: &str) -> Option<u32> {
        self.key_index.get(key).copied()
    }

    pub(crate) fn symbol_of(&self, instrument_id: u32) -> &str {
        &self.symbols[instrument_id as usize]
    }

    /// 세션에 해당 종목 bar가 있으면 그 행 번호.
    fn row_of(&self, session: usize, key: &str) -> Option<usize> {
        self.row_at(session, self.instrument_id(key)?)
    }

    /// `row_index` 조회. 세션 범위 밖이거나 그날 bar가 없으면 `None`.
    fn row_at(&self, session: usize, instrument_id: u32) -> Option<usize> {
        let slot = session
            .checked_mul(self.keys.len())?
            .checked_add(instrument_id as usize)?;
        match self.row_index.get(slot).copied() {
            Some(row) if row != u32::MAX => Some(row as usize),
            _ => None,
        }
    }

    pub(crate) fn has_bar(&self, session: usize, key: &str) -> bool {
        self.row_of(session, key).is_some()
    }

    pub(crate) fn open_at(&self, session: usize, key: &str) -> Option<f64> {
        self.row_of(session, key).map(|row| self.opens[row])
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

    /// 피드에 등록된 전 종목의 key → symbol. 그날 바가 없는 보유 종목(정지·상폐)을 청산하는
    /// 주문도 심볼을 찾을 수 있어야 한다 — Python 라우터는 포트폴리오 스냅샷에서 같은 정보를 본다.
    ///
    /// 그날 bar가 싣는 symbol도 같은 `symbols` 배열을 같은 instrument id로 읽으므로 이 표와
    /// 항상 같다 (key 중복 충돌은 `new`가 거른다). 라우팅은 병합 없이 이 표만 보면 된다.
    pub(crate) fn registry_symbols(&self) -> &HashMap<String, String> {
        &self.symbol_by_key
    }

    /// 세션 종가 마크. key는 등록부 문자열을 빌려준다 — 세션마다 종목 수만큼 String을
    /// 새로 만들지 않도록 `Portfolio::mark_refs`가 참조로 받는다.
    pub(crate) fn current_marks(&self) -> PyResult<Vec<(&str, f64)>> {
        let index = self.current_index()?;
        Ok(self
            .row_range(index)
            .map(|row| {
                let instrument = self.instrument_ids[row] as usize;
                (self.keys[instrument].as_str(), self.closes[row])
            })
            .collect())
    }

    pub(crate) fn current_session_count(&self) -> usize {
        self.current_session.map_or(0, |index| index + 1)
    }

    pub(crate) fn session_at(&self, index: usize) -> PyResult<&str> {
        self.sessions.get(index).map(String::as_str).ok_or_else(|| {
            PyIndexError::new_err(format!(
                "feed session index out of range — index={index} sessions={}",
                self.sessions.len()
            ))
        })
    }

    pub(crate) fn schedule_matches(&self, schedule: &str) -> PyResult<bool> {
        let index = self.current_index()?;
        match schedule {
            "every_session" => Ok(true),
            "month_end" => {
                let current_month = self.sessions[index].get(..7).ok_or_else(|| {
                    PyValueError::new_err(format!(
                        "session timestamp is not ISO-like — value={:?}",
                        self.sessions[index]
                    ))
                })?;
                let next_month = self
                    .sessions
                    .get(index + 1)
                    .and_then(|session| session.get(..7));
                Ok(next_month != Some(current_month))
            }
            other => Err(PyValueError::new_err(format!(
                "unsupported persistent schedule — schedule={other:?}"
            ))),
        }
    }

    pub(crate) fn settlement_session_index(&self, key: &str, event_ts: &str) -> Option<usize> {
        let instrument_id = self
            .keys
            .iter()
            .position(|known| known == key)
            .map(|index| index as u32)?;
        let first = self
            .sessions
            .partition_point(|session| session.as_str() < event_ts);
        (first..self.sessions.len()).find(|index| self.row_at(*index, instrument_id).is_some())
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
        let field = HistoryField::parse(field)?;
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
            for id in &ids {
                let value = id
                    .and_then(|id| self.row_at(session, id))
                    .map(|row| match field {
                        HistoryField::Open => self.opens[row],
                        HistoryField::High => self.highs[row],
                        HistoryField::Low => self.lows[row],
                        HistoryField::Close => self.closes[row],
                        HistoryField::Volume => self.volumes[row] as f64,
                    })
                    .unwrap_or(f64::NAN);
                values.push(value);
            }
        }
        Ok((timestamps, values))
    }
}

/// `history_window`가 읽는 OHLCV 열. 문자열 판정을 루프 밖에서 한 번만 해 도달 불가 분기를 없앤다.
#[derive(Clone, Copy)]
enum HistoryField {
    Open,
    High,
    Low,
    Close,
    Volume,
}

impl HistoryField {
    fn parse(field: &str) -> PyResult<Self> {
        match field {
            "open" => Ok(Self::Open),
            "high" => Ok(Self::High),
            "low" => Ok(Self::Low),
            "close" => Ok(Self::Close),
            "volume" => Ok(Self::Volume),
            other => Err(PyValueError::new_err(format!(
                "unknown history field — field={other:?}"
            ))),
        }
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
        assert_eq!(feed.settlement_session_index("A", "D1"), Some(0));
        assert_eq!(feed.settlement_session_index("A", "D2"), None);
        assert_eq!(feed.settlement_session_index("B", "D1.5"), Some(1));
        assert_eq!(feed.instrument_id("B"), Some(1));
        assert_eq!(feed.instrument_id("Z"), None);
        assert_eq!(feed.open_at(1, "A"), None);
        assert!(feed.has_bar(0, "B"));
        assert!(!feed.has_bar(1, "A"));
        assert_eq!(feed.open_at(1, "B"), Some(21.0));
        assert_eq!(feed.symbol_of(0), "AAA");
        assert_eq!(feed.session_len(), 2);
    }

    /// bar가 빠진 세션이 섞인 피드에서 `row_index`가 "그날 행 없음"을 정확히 표시하는지.
    /// A는 D2에, C는 D1·D2에 bar가 없다.
    #[test]
    fn row_index_reports_sessions_without_a_bar() {
        let mut feed = PersistentFeed::new(
            vec!["A".into(), "B".into(), "C".into()],
            vec!["AAA".into(), "BBB".into(), "CCC".into()],
            vec!["D1".into(), "D2".into(), "D3".into()],
            vec![0, 2, 3, 6],
            vec![0, 1, 1, 0, 1, 2],
            vec![10.0, 20.0, 21.0, 12.0, 22.0, 30.0],
            vec![11.0, 21.0, 22.0, 13.0, 23.0, 31.0],
            vec![9.0, 19.0, 20.0, 11.0, 21.0, 29.0],
            vec![10.5, 20.5, 21.5, 12.5, 22.5, 30.5],
            vec![100, 200, 300, 400, 500, 600],
        )
        .unwrap();

        // has_bar: 결측 세션만 false.
        let present = [
            (0, "A", true),
            (0, "B", true),
            (0, "C", false),
            (1, "A", false),
            (1, "B", true),
            (1, "C", false),
            (2, "A", true),
            (2, "B", true),
            (2, "C", true),
        ];
        for (session, key, expected) in present {
            assert_eq!(
                feed.has_bar(session, key),
                expected,
                "session={session} key={key}"
            );
        }
        // 등록부에 없는 key는 어느 세션에서도 bar가 없다.
        assert!(!feed.has_bar(0, "Z"));

        // open_at: 결측 세션은 None, 있는 세션은 그 행의 시가.
        assert_eq!(feed.open_at(0, "A"), Some(10.0));
        assert_eq!(feed.open_at(1, "A"), None);
        assert_eq!(feed.open_at(1, "B"), Some(21.0));
        assert_eq!(feed.open_at(0, "C"), None);
        assert_eq!(feed.open_at(2, "C"), Some(30.0));

        // 정산 세션은 사건 시각 이후 그 종목이 실제로 거래된 첫 세션이다.
        assert_eq!(feed.settlement_session_index("C", "D1"), Some(2));
        assert_eq!(feed.settlement_session_index("A", "D2"), Some(2));

        // history_window: 결측 세션 값은 NaN으로 채운다 (세션당 요청 종목 순서).
        feed.set_current(2).unwrap();
        let (timestamps, values) = feed
            .history_window(&["A".to_string(), "C".to_string()], "close", 3, "D3")
            .unwrap();
        assert_eq!(timestamps, vec!["D1", "D2", "D3"]);
        let expected = [Some(10.5), None, None, None, Some(12.5), Some(30.5)];
        assert_eq!(values.len(), expected.len());
        for (index, (value, want)) in values.iter().zip(expected).enumerate() {
            match want {
                Some(want) => assert_eq!(*value, want, "index={index}"),
                None => assert!(value.is_nan(), "index={index} value={value}"),
            }
        }
    }

    #[test]
    fn registry_rejects_one_key_with_two_symbols() {
        let error = PersistentFeed::new(
            vec!["A".into(), "A".into()],
            vec!["AAA".into(), "BBB".into()],
            vec!["D1".into()],
            vec![0, 1],
            vec![0],
            vec![10.0],
            vec![11.0],
            vec![9.0],
            vec![10.5],
            vec![100],
        );
        let error = match error {
            Ok(_) => panic!("duplicate key with two symbols must be rejected"),
            Err(error) => error.to_string(),
        };
        assert!(error.contains("one key to two symbols"), "{error}");
    }

    #[test]
    fn schedule_uses_the_next_trading_session_for_month_end() {
        let mut feed = PersistentFeed::new(
            vec!["A".into()],
            vec!["AAA".into()],
            vec!["2026-01-30 00:00:00".into(), "2026-02-02 00:00:00".into()],
            vec![0, 1, 2],
            vec![0, 0],
            vec![10.0, 10.0],
            vec![11.0, 11.0],
            vec![9.0, 9.0],
            vec![10.5, 10.5],
            vec![100, 100],
        )
        .unwrap();
        feed.set_current(0).unwrap();
        assert!(feed.schedule_matches("every_session").unwrap());
        assert!(feed.schedule_matches("month_end").unwrap());
        feed.set_current(1).unwrap();
        assert!(feed.schedule_matches("month_end").unwrap());
    }
}
