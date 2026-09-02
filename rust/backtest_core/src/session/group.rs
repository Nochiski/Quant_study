use super::{py_list, EntryIn, QuoteOut, Session};
use pyo3::prelude::*;

impl<'a> Session<'a> {
    fn cancel_all(
        &mut self,
        entries: &[EntryIn],
        order_ids: &[String],
        policy: &str,
        group_id: &str,
        reason: &str,
    ) {
        // Python은 group.order_ids(라우팅 순)로 취소한다 — 매도 우선 정렬과 다르다.
        let mut ordered: Vec<&EntryIn> = entries.iter().collect();
        ordered.sort_by_key(|e| order_ids.iter().position(|id| *id == e.order_id));
        for e in ordered {
            self.remove(&e.order_id);
            let d = format!(
                "basket {policy} — {reason} group_id={group_id} remaining={} ts={}",
                e.remaining, self.ts
            );
            self.update(&e.order_id, "cancelled", Some(d));
        }
        self.ops.push((
            "drop_group".into(),
            group_id.into(),
            0,
            0.0,
            0.0,
            0.0,
            String::new(),
        ));
    }

    pub(super) fn group(
        &mut self,
        group_id: &str,
        policy: &str,
        order_ids: &[String],
        entries: &mut [EntryIn],
    ) -> PyResult<()> {
        let mut legs: Vec<EntryIn> = entries
            .iter()
            .filter(|e| e.group_id.as_deref() == Some(group_id))
            .cloned()
            .collect();
        if legs.is_empty() {
            return Ok(());
        }
        // Python group_entries()는 order_ids(라우팅) 순이다 — missing 리스트 표기도 그 순서.
        legs.sort_by_key(|e| order_ids.iter().position(|id| *id == e.order_id));
        let all_leg_ids: Vec<String> = legs.iter().map(|e| e.order_id.clone()).collect();
        let missing: Vec<String> = legs
            .iter()
            .filter(|e| !self.bars.contains_key(&e.key))
            .map(|e| e.symbol.clone())
            .collect();
        if !missing.is_empty() && policy != "best_effort" {
            let reason = format!(
                "leg without bar in session instruments={}",
                py_list(&missing)
            );
            self.cancel_all(&legs, order_ids, policy, group_id, &reason);
            return Ok(());
        }
        if policy != "best_effort" && legs.len() < order_ids.len() {
            let open: Vec<&str> = legs.iter().map(|e| e.order_id.as_str()).collect();
            let mut gone: Vec<String> = order_ids
                .iter()
                .filter(|id| !open.contains(&id.as_str()))
                .cloned()
                .collect();
            gone.sort();
            let reason = format!(
                "leg(s) no longer open before group execution missing={}",
                py_list(&gone)
            );
            self.cancel_all(&legs, order_ids, policy, group_id, &reason);
            return Ok(());
        }
        // 견적 패스: 매도 먼저, 임시 소모 후 복원.
        legs.retain(|e| self.bars.contains_key(&e.key));
        legs.sort_by(|a, b| {
            (a.side != "sell", a.order_id.clone()).cmp(&(b.side != "sell", b.order_id.clone()))
        });
        let checkpoint = self.power.checkpoint();
        let mut quotes: Vec<QuoteOut> = Vec::with_capacity(legs.len());
        for e in &legs {
            let q = self.quote(e)?;
            if q.quantity > 0 {
                let notional = q.quantity as f64 * q.price;
                self.power.consume(
                    &e.key,
                    &e.side,
                    q.quantity,
                    q.price,
                    notional * self.fee_rate,
                )?;
            }
            quotes.push(q);
        }
        self.power.restore(checkpoint);

        let sync_back = |entries: &mut [EntryIn], legs: &[EntryIn]| {
            for leg in legs {
                if let Some(target) = entries.iter_mut().find(|x| x.order_id == leg.order_id) {
                    target.remaining = leg.remaining;
                    target.triggered = leg.triggered;
                }
            }
        };

        match policy {
            "best_effort" => {
                for (i, q) in quotes.iter().enumerate() {
                    if q.quantity > 0 {
                        let qty = q.quantity;
                        self.apply(&mut legs[i], q, qty)?;
                    }
                }
                sync_back(entries, &legs);
                // bar가 없어 이번 세션에 건너뛴 leg도 그룹에 남아 있다 — 전체 leg 기준으로만 버린다.
                let all_done = entries
                    .iter()
                    .filter(|e| all_leg_ids.contains(&e.order_id))
                    .all(|e| e.remaining == 0);
                if all_done {
                    self.ops.push((
                        "drop_group".into(),
                        group_id.into(),
                        0,
                        0.0,
                        0.0,
                        0.0,
                        String::new(),
                    ));
                }
            }
            "all_or_none" => {
                let short: Vec<String> = legs
                    .iter()
                    .zip(&quotes)
                    .filter(|(e, q)| q.quantity < e.remaining)
                    .map(|(e, q)| format!("{}:{}/{}", e.symbol, q.quantity, e.remaining))
                    .collect();
                if !short.is_empty() {
                    let reason = format!("not fillable in full legs={}", py_list(&short));
                    self.cancel_all(&legs, order_ids, policy, group_id, &reason);
                    return Ok(());
                }
                for (i, q) in quotes.iter().enumerate() {
                    let qty = q.quantity;
                    self.apply(&mut legs[i], q, qty)?;
                }
                sync_back(entries, &legs);
                self.ops.push((
                    "drop_group".into(),
                    group_id.into(),
                    0,
                    0.0,
                    0.0,
                    0.0,
                    String::new(),
                ));
            }
            _ => {
                // proportional: 최저 비율 leg j 기준 정수 교차곱.
                let (j, _) = legs
                    .iter()
                    .zip(&quotes)
                    .enumerate()
                    .min_by(|(_, (ea, qa)), (_, (eb, qb))| {
                        ((qa.quantity as i128) * (eb.remaining as i128))
                            .cmp(&((qb.quantity as i128) * (ea.remaining as i128)))
                    })
                    .map(|(i, _)| (i, ()))
                    .unwrap_or((0, ()));
                let tight_q = quotes[j].quantity;
                let tight_rem = legs[j].remaining;
                if tight_q <= 0 {
                    self.cancel_all(&legs, order_ids, policy, group_id, "no leg fillable");
                    return Ok(());
                }
                let scale = tight_q as f64 / tight_rem as f64;
                let planned: Vec<i64> = legs
                    .iter()
                    .map(|e| ((e.remaining as i128 * tight_q as i128) / tight_rem as i128) as i64)
                    .collect();
                for (i, qty) in planned.iter().enumerate() {
                    if *qty > 0 {
                        let q = quotes[i].clone();
                        self.apply(&mut legs[i], &q, *qty)?;
                    }
                }
                let mut leftovers: Vec<&EntryIn> =
                    legs.iter().filter(|e| e.remaining > 0).collect();
                leftovers.sort_by_key(|e| order_ids.iter().position(|id| *id == e.order_id));
                for e in leftovers {
                    {
                        self.remove(&e.order_id);
                        let d = format!(
                            "basket proportional remainder cancelled — scale={scale:.6} group_id={group_id} remaining={} ts={}",
                            e.remaining, self.ts
                        );
                        self.update(&e.order_id, "cancelled", Some(d));
                    }
                }
                for leg in legs.iter_mut() {
                    if leg.remaining > 0 {
                        leg.remaining = 0;
                    }
                }
                sync_back(entries, &legs);
                self.ops.push((
                    "drop_group".into(),
                    group_id.into(),
                    0,
                    0.0,
                    0.0,
                    0.0,
                    String::new(),
                ));
            }
        }
        Ok(())
    }
}
