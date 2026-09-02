use pyo3::exceptions::PyIndexError;
use pyo3::prelude::*;
use std::cmp::Reverse;
use std::collections::BinaryHeap;

/// Python datetime을 UTC 기준 정수 microsecond로 정규화한 키.
type TimestampKey = i64;
type QueueEntry = Reverse<(TimestampKey, u8, u64, u64)>;

/// Persistent 실행 경로의 `(ts, priority, seq)` 단일 진실 원천.
///
/// payload는 M4/M5 이전 단계에서 Python side table의 token으로 유지한다. 정렬과 FIFO
/// sequence는 지금부터 Rust가 소유하므로 다음 단계에서 callback frame으로 교체할 수 있다.
#[derive(Default)]
pub(crate) struct NativeEventQueue {
    heap: BinaryHeap<QueueEntry>,
    sequence: u64,
}

impl NativeEventQueue {
    pub(crate) fn push(&mut self, ts: TimestampKey, priority: u8, token: u64) -> PyResult<()> {
        self.sequence = self.sequence.checked_add(1).ok_or_else(|| {
            PyIndexError::new_err("persistent event queue sequence exhausted u64")
        })?;
        self.heap
            .push(Reverse((ts, priority, self.sequence, token)));
        Ok(())
    }

    pub(crate) fn pop(&mut self) -> PyResult<u64> {
        self.heap
            .pop()
            .map(|Reverse((_, _, _, token))| token)
            .ok_or_else(|| PyIndexError::new_err("pop from empty persistent event queue"))
    }

    pub(crate) fn len(&self) -> usize {
        self.heap.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn orders_by_timestamp_priority_then_fifo_sequence() {
        let mut queue = NativeEventQueue::default();
        queue.push(2, 10, 1).unwrap();
        queue.push(1, 30, 2).unwrap();
        queue.push(1, 20, 3).unwrap();
        queue.push(1, 20, 4).unwrap();
        assert_eq!(queue.len(), 4);
        assert_eq!(queue.pop().unwrap(), 3);
        assert_eq!(queue.pop().unwrap(), 4);
        assert_eq!(queue.pop().unwrap(), 2);
        assert_eq!(queue.pop().unwrap(), 1);
        assert!(queue.pop().is_err());
    }
}
