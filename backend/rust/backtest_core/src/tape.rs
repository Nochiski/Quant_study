//! 선언형 목표 tape: Rust가 전략 결정까지 만들어 Python 콜백 없이 완주한다.
//!
//! 규칙은 Python reference `backtest_engine/engine/tape.py::evaluate_tape`와 같아야 하며
//! `tests/test_core_parity.py`가 두 경로의 trace를 대조한다.
//!
//! - market 콜백: 세션 날짜에 프레임이 있으면 그 프레임의 `SetPortfolioTarget`, 없으면
//!   `NoAction(idle_reason)`.
//! - 프레임 목표 중 그 세션에 bar가 없는 종목은 보유 중이면 `QuantityTarget(보유 수량)`으로
//!   고정하고 미보유면 제외한다. 제외·고정된 심볼은 reason 뒤에 ` no_bar=(...)`로 남긴다.
//! - market이 아닌 콜백(FILL/ORDER_UPDATE/CORPORATE_ACTION): `NoAction(idle_reason)`.

use crate::callback::CallbackFrame;
use crate::persistent::PersistentEngine;
use crate::persistent_router::{DecisionWire, ExecutionWire, TargetWire};
use crate::records::NativeDecision;
use crate::session::py_tuple;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::HashMap;

/// 세션 하나에 배정된 프레임. `targets`는 `WeightTarget` wire만 허용한다.
#[derive(Clone, Debug)]
pub(crate) struct TapeFrameWire {
    pub(crate) targets: Vec<TargetWire>,
    pub(crate) scope: String,
    pub(crate) execution: ExecutionWire,
    pub(crate) reason: String,
}

#[derive(Clone, Debug)]
pub(crate) struct NativeTape {
    pub(crate) frames: HashMap<usize, TapeFrameWire>,
    pub(crate) idle_reason: String,
}

/// Rust가 만든 결정과 DECISION 레코드에 남길 재구성 정보.
pub(crate) struct NativeSubmission {
    pub(crate) decision: DecisionWire,
    pub(crate) native: NativeDecision,
}

impl PersistentEngine {
    /// 세션 index → 프레임 목록을 적재한다. 같은 세션에 두 프레임이면 오류.
    pub(crate) fn load_target_tape_internal(
        &mut self,
        frames: Vec<(usize, Vec<TargetWire>, String, ExecutionWire, String)>,
        idle_reason: String,
    ) -> PyResult<()> {
        let sessions = self
            .feed
            .as_ref()
            .ok_or_else(|| PyValueError::new_err("persistent feed is not loaded"))?
            .session_len();
        let mut by_session = HashMap::with_capacity(frames.len());
        for (session_index, targets, scope, execution, reason) in frames {
            if session_index >= sessions {
                return Err(PyValueError::new_err(format!(
                    "tape frame session index out of range — index={session_index} sessions={sessions}"
                )));
            }
            if let Some(target) = targets.iter().find(|target| target.0 != "weight") {
                return Err(PyValueError::new_err(format!(
                    "tape frame targets must be weight targets — session={session_index} kind={} symbol={}",
                    target.0, target.2
                )));
            }
            if by_session
                .insert(
                    session_index,
                    TapeFrameWire {
                        targets,
                        scope,
                        execution,
                        reason,
                    },
                )
                .is_some()
            {
                return Err(PyValueError::new_err(format!(
                    "duplicate tape frame for session — index={session_index}"
                )));
            }
        }
        self.tape = Some(NativeTape {
            frames: by_session,
            idle_reason,
        });
        Ok(())
    }

    /// 콜백 프레임에 대한 tape 결정 (`evaluate_tape`와 같은 규칙).
    pub(crate) fn native_decision(&self, frame: &CallbackFrame) -> PyResult<NativeSubmission> {
        let tape = self
            .tape
            .as_ref()
            .ok_or_else(|| PyValueError::new_err("target tape is not loaded"))?;
        let idle = |reason: &str| NativeSubmission {
            decision: (
                1,
                frame.ts.clone(),
                Some(reason.to_string()),
                vec![("no_action".to_string(), vec![], None, None, None, vec![])],
            ),
            native: NativeDecision {
                frame_session: None,
                kept: Vec::new(),
                no_bar: Vec::new(),
                reason: reason.to_string(),
            },
        };
        if frame.event.is_some() {
            return Ok(idle(&tape.idle_reason));
        }
        let Some(tape_frame) = tape.frames.get(&frame.session_index) else {
            return Ok(idle(&tape.idle_reason));
        };
        let feed = self
            .feed
            .as_ref()
            .ok_or_else(|| PyValueError::new_err("persistent feed is not loaded"))?;
        let mut kept_wires = Vec::with_capacity(tape_frame.targets.len());
        let mut kept = Vec::with_capacity(tape_frame.targets.len());
        let mut no_bar = Vec::new();
        for target in &tape_frame.targets {
            // feed에 한 번도 나오지 않은 종목은 bar가 없고 보유도 불가능하다 — Python 규칙과 같이
            // 미보유 no_bar로 건너뛴다.
            let Some(instrument_id) = feed.instrument_id(&target.1) else {
                no_bar.push(target.2.clone());
                continue;
            };
            if feed.has_bar(frame.session_index, &target.1) {
                kept_wires.push(target.clone());
                kept.push((
                    instrument_id,
                    "weight".to_string(),
                    target.5.unwrap_or(f64::NAN),
                    0,
                ));
                continue;
            }
            // bar 없는 종목: 보유 중이면 수량 유지, 미보유면 제외 (예산은 현금에 남는다).
            no_bar.push(target.2.clone());
            let held = self.portfolio.held_qty(&target.1);
            if held != 0 {
                kept_wires.push((
                    "quantity".to_string(),
                    target.1.clone(),
                    target.2.clone(),
                    target.3.clone(),
                    None,
                    None,
                    Some(held.to_string()),
                ));
                kept.push((instrument_id, "quantity".to_string(), 0.0, held));
            }
        }
        let reason = if no_bar.is_empty() {
            tape_frame.reason.clone()
        } else {
            format!("{} no_bar={}", tape_frame.reason, py_tuple(&no_bar))
        };
        Ok(NativeSubmission {
            decision: (
                1,
                frame.ts.clone(),
                Some(reason.clone()),
                vec![(
                    "set_portfolio_target".to_string(),
                    kept_wires,
                    Some(tape_frame.scope.clone()),
                    Some(tape_frame.execution.clone()),
                    None,
                    vec![],
                )],
            ),
            native: NativeDecision {
                frame_session: Some(frame.session_index),
                kept,
                no_bar,
                reason,
            },
        })
    }

    /// tape가 있을 때 프레임을 Python에 넘기지 않고 바로 결정을 제출한다.
    pub(crate) fn submit_native(&mut self, frame: &CallbackFrame) -> PyResult<()> {
        let submission = self.native_decision(frame)?;
        let (_, error) =
            self.submit_internal(frame.token, submission.decision, Some(submission.native))?;
        if let Some((code, message)) = error {
            return Err(PyValueError::new_err(format!(
                "route_error:{code}:{message}"
            )));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::driver::RunSettings;
    use crate::records::{RecordPayload, KIND_DECISION};

    const A: &str = "XKRX:000660:equity:KRW";
    const B: &str = "XKRX:005930:equity:KRW";
    const C: &str = "XKRX:000030:equity:KRW";

    fn weight(key: &str, symbol: &str, weight: f64) -> TargetWire {
        (
            "weight".into(),
            key.into(),
            symbol.into(),
            "KRW".into(),
            None,
            Some(weight),
            None,
        )
    }

    fn execution() -> ExecutionWire {
        ("market".into(), "next_open".into(), "day".into(), None)
    }

    /// 세션 0: A·B·C 모두 bar. 세션 1: A만 bar. B는 세션 0 시가에 12주 매수돼 있다.
    fn runtime() -> PersistentEngine {
        let mut runtime = PersistentEngine::new(100_000.0, false, false, 1.0).unwrap();
        runtime
            .load_feed(
                vec![A.into(), B.into(), C.into()],
                vec!["000660".into(), "005930".into(), "000030".into()],
                vec!["2018-04-27 15:30:00".into(), "2018-04-30 15:30:00".into()],
                vec![0, 3, 4],
                vec![0, 1, 2, 0],
                vec![100.0, 100.0, 100.0, 100.0],
                vec![101.0, 101.0, 101.0, 101.0],
                vec![100.0, 100.0, 100.0, 100.0],
                vec![101.0, 101.0, 101.0, 101.0],
                vec![1_000, 1_000, 1_000, 1_000],
            )
            .unwrap();
        runtime.configure_router(
            vec!["no_action".into(), "set_portfolio_target".into()],
            vec![],
        );
        runtime.run = Some(RunSettings {
            fee_rate: 0.0,
            default_participation: None,
            slippage: ("none".into(), 0.0, 0.0),
            schedule: "every_session".into(),
            short_borrow_bps_annual: 0.0,
            margin_interest_bps_annual: 0.0,
            annualization_days: 252,
            warmup_sessions: 0,
            notify_fill: false,
            notify_order_update: false,
            notify_corporate_action: false,
        });
        runtime
    }

    #[test]
    fn missing_bar_targets_are_held_when_owned_and_skipped_otherwise() {
        let mut runtime = runtime();
        runtime.portfolio.apply(B, "buy", 12, 100.0, 0.0).unwrap();
        runtime
            .load_target_tape_internal(
                vec![(
                    1,
                    vec![
                        weight(A, "000660", 0.4),
                        weight(B, "005930", 0.4),
                        weight(C, "000030", 0.2),
                    ],
                    "replace".into(),
                    execution(),
                    "target_tape:2018-04-27".into(),
                )],
                "target_tape_idle".into(),
            )
            .unwrap();
        let first = runtime.drive_internal().unwrap();
        assert!(first.is_none(), "tape 경로는 Python 콜백을 만들지 않는다");
        let decisions: Vec<_> = runtime
            .records
            .records()
            .iter()
            .filter(|record| record.payload.kind() == KIND_DECISION)
            .collect();
        assert_eq!(decisions.len(), 2);
        let RecordPayload::Decision { native, .. } = &decisions[0].payload else {
            panic!("decision payload expected");
        };
        let idle = native.as_ref().unwrap();
        assert_eq!(
            (idle.frame_session, idle.reason.as_str()),
            (None, "target_tape_idle")
        );
        let RecordPayload::Decision { native, .. } = &decisions[1].payload else {
            panic!("decision payload expected");
        };
        let framed = native.as_ref().unwrap();
        assert_eq!(framed.frame_session, Some(1));
        assert_eq!(
            framed.reason,
            "target_tape:2018-04-27 no_bar=('005930', '000030')"
        );
        assert_eq!(
            framed.kept,
            vec![
                (0, "weight".to_string(), 0.4, 0),
                (1, "quantity".to_string(), 0.0, 12),
            ]
        );
        assert_eq!(
            framed.no_bar,
            vec!["005930".to_string(), "000030".to_string()]
        );
    }

    #[test]
    fn frames_with_every_instrument_priced_pass_through_unchanged() {
        let mut runtime = runtime();
        runtime
            .load_target_tape_internal(
                vec![(
                    0,
                    vec![weight(A, "000660", 0.7)],
                    "replace".into(),
                    execution(),
                    "target_tape:2018-04-27".into(),
                )],
                "target_tape_idle".into(),
            )
            .unwrap();
        assert!(runtime.drive_internal().unwrap().is_none());
        let RecordPayload::Decision { native, .. } = &runtime
            .records
            .records()
            .iter()
            .find(|record| record.payload.kind() == KIND_DECISION)
            .unwrap()
            .payload
        else {
            panic!("decision payload expected");
        };
        let framed = native.as_ref().unwrap();
        assert_eq!(framed.reason, "target_tape:2018-04-27");
        assert_eq!(framed.kept, vec![(0, "weight".to_string(), 0.7, 0)]);
        assert!(framed.no_bar.is_empty());
        // 0.7 × 100,000 / 101 = 693주 주문이 세션 1 시가에 체결된다.
        assert_eq!(runtime.portfolio.held_qty(A), 693);
    }

    #[test]
    fn tape_rejects_non_weight_targets_and_duplicate_sessions() {
        let mut runtime = runtime();
        let quantity: TargetWire = (
            "quantity".into(),
            A.into(),
            "000660".into(),
            "KRW".into(),
            None,
            None,
            Some("1".into()),
        );
        assert!(runtime
            .load_target_tape_internal(
                vec![(0, vec![quantity], "replace".into(), execution(), "r".into())],
                "idle".into(),
            )
            .is_err());
        assert!(runtime
            .load_target_tape_internal(
                vec![
                    (0, vec![], "replace".into(), execution(), "r".into()),
                    (0, vec![], "replace".into(), execution(), "r".into()),
                ],
                "idle".into(),
            )
            .is_err());
    }
}
