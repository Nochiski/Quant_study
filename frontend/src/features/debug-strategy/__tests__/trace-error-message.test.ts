import { describe, expect, it } from "vitest";

import { backtestStartRejectionMessage } from "../../../entities/backtest";
import { ApiRequestError } from "../../../shared/api";
import { tOptional } from "../../../shared/config";
import {
  readOpenApi,
  rejectionCodes,
} from "../../../shared/testing/openapi-codes";
import { traceErrorMessage } from "../model/use-strategy-trace";

const GENERIC =
  "서버가 추적 요청을 처리하지 못했습니다. 잠시 뒤 다시 추적하세요.";

describe("traceErrorMessage (Phase 1 감사 위험 5b)", () => {
  it("translates a known backend 422 code instead of exposing the raw detail", () => {
    const error = new ApiRequestError(
      "trace",
      422,
      "trace.strategy.requires_upgrade",
      "strategy revision 1 requires upgrade",
    );
    expect(traceErrorMessage(error)).toBe(
      "저장된 이전 schema revision은 추적할 수 없습니다. 업그레이드 후 새 revision으로 저장하세요.",
    );
    expect(
      traceErrorMessage(
        new ApiRequestError(
          "trace",
          409,
          "trace.strategy.stale",
          "stale source",
        ),
      ),
    ).toBe(
      "편집 중인 문서가 저장본과 달라져 추적할 수 없습니다. 저장하거나 저장본을 다시 여세요.",
    );
  });

  it("keeps the backend detail out of the body even for codes that name a field (#270)", () => {
    // 어느 칸인지는 접힌 서버 사유(`failureReason`)가 보인다 — 본문에 원문을 넣던 `{detail}` 자리는 없다.
    const invalid = traceErrorMessage(
      new ApiRequestError(
        "trace",
        422,
        "trace.request.invalid",
        "as_of must be a session",
      ),
    );
    expect(invalid).toBe(
      "추적 요청이 올바르지 않습니다. 어느 칸이 틀렸는지는 서버 사유를 보세요.",
    );
    expect(invalid).not.toContain("as_of");
    const incompatible = traceErrorMessage(
      new ApiRequestError("traceStrategy", 422, "trace.engine.incompatible"),
    );
    expect(incompatible).toBe(
      "선택한 실행 엔진이 이 전략을 추적할 수 없습니다. 다른 실행 core를 고르세요.",
    );
  });

  it("falls back to a generic sentence, never the detail or the developer message", () => {
    expect(
      traceErrorMessage(
        new ApiRequestError("trace", 418, "trace.unknown", "teapot"),
      ),
    ).toBe(GENERIC);
    const serverError = traceErrorMessage(new ApiRequestError("trace", 500));
    expect(serverError).toBe(GENERIC);
    expect(serverError).not.toContain("API request failed");
    expect(traceErrorMessage(new Error("boom"))).toBe(GENERIC);
    expect(traceErrorMessage("plain")).toBe(GENERIC);
  });
});

// #351: 봉인 구간·칸 규칙 같은 영구 조건이 "잠시 뒤 다시 추적하세요"로 보이면 안 된다. 실행 시 계약 파일
// (backend `openapi.json`)과 대조한다 — 추적 거절 코드가 늘면 목록 단언이 먼저 깨진다.
describe("trace rejection code vocabulary (#351)", () => {
  it("gives every coded trace rejection a sentence of its own", () => {
    const codes = [
      ...rejectionCodes(readOpenApi(), "/api/v1/strategies/debug/trace"),
    ].sort();

    expect(codes).toEqual([
      "backtest.run.environment_required",
      "backtest.run.field_invalid",
      "backtest.run.research_window_violation",
      "portfolio.data.unavailable",
      "portfolio.raw_observation.invalid",
      "portfolio.strategy.invalid",
      "trace.cancelled",
      "trace.capability.unsupported",
      "trace.engine.incompatible",
      "trace.request.invalid",
      "trace.strategy.not_found",
      "trace.strategy.requires_upgrade",
      "trace.strategy.stale",
    ]);
    expect(
      codes.filter(
        (code) =>
          traceErrorMessage(new ApiRequestError("traceStrategy", 422, code)) ===
          GENERIC,
      ),
    ).toEqual([]);
    // 같은 거절을 두 벌 번역하지 않는다: 백테스트 API 와 같은 코드는 `backtest.error.<code>` 만 문장을 갖는다
    // (#351 리뷰 P3-4).
    expect(
      codes.filter(
        (code) =>
          tOptional(`trace.error.${code}`) !== null &&
          tOptional(`backtest.error.${code}`) !== null,
      ),
    ).toEqual([]);
  });

  it("says a run settings rejection with the backtest start sentence and its dates", () => {
    const values = {
      code: "backtest.run.research_window_violation",
      sealed_start: "2016-01-01",
      sealed_end: "2019-12-31",
      research_start: "2020-01-02",
    };
    const rejection = new ApiRequestError(
      "traceStrategy",
      422,
      values.code,
      "측정 시작일이 연구 구간 밖이라 실행할 수 없다",
      null,
      null,
      undefined,
      undefined,
      values,
    );

    expect(traceErrorMessage(rejection)).toBe(
      backtestStartRejectionMessage(values.code, null, values),
    );
    expect(traceErrorMessage(rejection)).toContain(
      "2016-01-01~2019-12-31은 홀드아웃으로 봉인돼 있고",
    );
    expect(traceErrorMessage(rejection)).not.toContain("{research_start}");
    // 백테스트 시작과 같은 문장이라 두 동작에 맞는 동사로 끝난다(#351 리뷰 P3-1).
    expect(traceErrorMessage(rejection)).toMatch(/다시 실행하세요\.$/u);
  });

  it("does not point a trace input rejection at the run settings (#351 리뷰 P3-2)", () => {
    // 추적 전용 칸(`as_of`·`security_ids`)의 본문 검증 실패도 `backtest.run.field_invalid` 로 온다.
    const rejection = new ApiRequestError(
      "traceStrategy",
      422,
      "backtest.run.field_invalid",
      "as_of must be an ISO date",
      null,
      null,
      "as_of",
    );

    expect(traceErrorMessage(rejection)).toBe(
      "서버가 요청의 값 하나를 받지 않았습니다. 서버 사유가 짚은 칸을 고친 뒤 다시 실행하세요.",
    );
  });
});
