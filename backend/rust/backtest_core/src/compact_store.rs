use pyo3::exceptions::PyIndexError;
use pyo3::prelude::*;

pub(crate) type RecordWire = (u64, i64, u8, u64);
pub(crate) type AppendWire = (i64, u8, u64);

/// Persistent 실행의 append-only record index.
///
/// payload는 전환 단계의 Python side table token이다. fill/order/snapshot payload 자체는
/// primitive compact wire로 유지되고 공개 Event 객체는 결과 조회 시 lazy materialize된다.
#[derive(Default)]
pub(crate) struct CompactRecordStore {
    records: Vec<RecordWire>,
    finished: bool,
}

impl CompactRecordStore {
    pub(crate) fn append(
        &mut self,
        timestamp_micros: i64,
        kind: u8,
        payload_token: u64,
    ) -> PyResult<()> {
        if self.finished {
            return Err(PyIndexError::new_err(
                "cannot append to finished persistent record store",
            ));
        }
        let sequence = u64::try_from(self.records.len())
            .map_err(|_| PyIndexError::new_err("persistent record sequence exhausted u64"))?;
        self.records
            .push((sequence, timestamp_micros, kind, payload_token));
        Ok(())
    }

    pub(crate) fn batch(&self) -> Vec<RecordWire> {
        self.records.clone()
    }

    pub(crate) fn extend(&mut self, records: Vec<AppendWire>) -> PyResult<()> {
        for (timestamp_micros, kind, payload_token) in records {
            self.append(timestamp_micros, kind, payload_token)?;
        }
        Ok(())
    }

    pub(crate) fn finish(&mut self) -> PyResult<Vec<RecordWire>> {
        if self.finished {
            return Err(PyIndexError::new_err(
                "persistent record store finish called more than once",
            ));
        }
        self.finished = true;
        Ok(self.batch())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn append_only_batch_keeps_sequence_and_rejects_after_finish() {
        let mut store = CompactRecordStore::default();
        store.append(10, 0, 7).unwrap();
        store.append(10, 5, 8).unwrap();
        assert_eq!(store.finish().unwrap(), vec![(0, 10, 0, 7), (1, 10, 5, 8)]);
        assert!(store.append(11, 0, 9).is_err());
    }
}
