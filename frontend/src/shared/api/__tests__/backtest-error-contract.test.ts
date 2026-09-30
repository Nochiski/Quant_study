import { describe, expect, it } from "vitest";

import { messages, tOptional } from "../../config";
import { readOpenApi, rejectionCodes } from "../../testing/openapi-codes";
import type {
  BacktestRunState,
  StartBacktestErrors,
  TrialAttempt,
  WindowPick,
} from "../generated/types.gen";

type StartBacktest422 = StartBacktestErrors[422];

const classify = (response: StartBacktest422): string => {
  const { detail } = response;
  switch (detail.code) {
    case "backtest.run.invalid":
      return `run:${detail.message}`;
    // 본문 검증 실패도 코드화된 detail 로 온다(이슈 #260). FastAPI 기본 배열 형식은 이 계약에 없다.
    case "backtest.run.field_invalid":
      return `field:${detail.field ?? "-"}`;
    case "portfolio.strategy.invalid":
      return `strategy:${detail.validation.valid}`;
    // `portfolio.data.unavailable` · `portfolio.raw_observation.invalid` 는 시작 요청이 데이터를
    // 읽지 않게 되면서(이슈 #158) 이 경로의 계약에서 빠졌다. 그 실패는 run 상태 `error` 로 온다.
    case "backtest.strategy.requires_upgrade":
      return `upgrade:${detail.message}`;
    // schema 1.2 문서는 실행 설정을 담지 않으므로 시작 요청이 반드시 실어야 한다(P2-03).
    case "backtest.run.environment_required":
      return `environment:${detail.message}`;
    // 봉인 구간을 측정하는 요청은 서버가 거절한다(검증 랩 spec D1).
    case "backtest.run.research_window_violation":
      return `research_window:${detail.message}`;
    // 문서에 없는 파라미터·허용 밖 값은 어느 파라미터인지 싣는다(검증 랩 spec D4).
    case "backtest.run.parameter_invalid":
      return `parameter:${detail.parameter_id}`;
    default: {
      const exhaustive: never = detail;
      return exhaustive;
    }
  }
};

type RunFailureCode = NonNullable<BacktestRunState["error_code"]>;

// backend `RunFailureCode` 어휘(OpenAPI enum → 생성 타입)와 run 페이지 번역 키의 동기화. 코드가 늘면 이 표가
// 타입 오류로 먼저 깨지고, 번역이 빠지면 아래 단언이 깨진다(이슈 #158).
const RUN_FAILURE_CODES: Record<RunFailureCode, true> = {
  "portfolio.strategy.invalid": true,
  "portfolio.data.unavailable": true,
  "portfolio.raw_observation.invalid": true,
  "backtest.run.invalid": true,
  "backtest.run.equity_wiped_out": true,
  "backtest.run.data_not_ready": true,
  "backtest.run.no_positions": true,
  "backtest.run.benchmark_unknown": true,
  "backtest.run.internal": true,
  "backtest.run.interrupted": true,
};

type TrialRejectionCode = NonNullable<
  TrialAttempt["error_code"] | WindowPick["error_code"]
>;

// 실험 trial·창 검증 제출의 접수 거절 코드(backend `AdmissionRejectionCode` → OpenAPI enum). 화면은
// `backtest.error.<code>` 로 번역한다. 코드가 늘면 이 표가 타입 오류로 먼저 깨진다(#382 DEFECT-V3D-08).
const TRIAL_REJECTION_CODES: Record<TrialRejectionCode, true> = {
  "backtest.run.environment_required": true,
  "backtest.run.research_window_violation": true,
  "backtest.run.parameter_invalid": true,
  "backtest.run.invalid": true,
  "backtest.strategy.not_found": true,
  "backtest.strategy.stale": true,
  "backtest.strategy.requires_upgrade": true,
  "portfolio.strategy.invalid": true,
};

const untranslated = (codes: Iterable<string>): string[] =>
  [...codes].flatMap((code) => {
    const key = `backtest.error.${code}` as keyof (typeof messages)["en"];
    return [
      messages.ko[key] === undefined ? `${code} ko` : null,
      messages.en[key] === undefined ? `${code} en` : null,
    ].filter((item): item is string => item !== null);
  });

describe("backtest run failure code vocabulary", () => {
  it("has a translated recovery message for every run failure code", () => {
    for (const code of Object.keys(RUN_FAILURE_CODES)) {
      expect(tOptional(`backtest.run.error.${code}`), code).not.toBeNull();
    }
  });

  // 이슈 #260: 툴바는 시작 거절을 `backtest.error.<code>` 번역으로 보인다. 키가 빠지면 일반 문구로 떨어져
  // 무엇을 고칠지 말하지 못한다. 실행 시 계약 파일(backend `openapi.json`)과 대조한다.
  it("translates every coded startBacktest rejection in both locales", () => {
    const codes = rejectionCodes(readOpenApi(), "/api/v1/backtests");

    expect([...codes].sort()).toEqual([
      "backtest.run.environment_required",
      "backtest.run.field_invalid",
      "backtest.run.invalid",
      "backtest.run.parameter_invalid",
      "backtest.run.research_window_violation",
      "backtest.strategy.not_found",
      "backtest.strategy.requires_upgrade",
      "backtest.strategy.stale",
      "portfolio.strategy.invalid",
    ]);
    expect(untranslated(codes)).toEqual([]);
  });

  // 이슈 #330: 결과 화면은 결과 조회 실패(`getBacktestResult` 404·409·410)도 같은 번역 키 체계로 보인다. 결과
  // 경로 코드가 늘면 번역 누락을 여기서 먼저 잡는다.
  it("translates every coded getBacktestResult failure in both locales", () => {
    const codes = rejectionCodes(
      readOpenApi(),
      "/api/v1/backtests/{run_id}/result",
    );

    expect([...codes].sort()).toEqual([
      "backtest.result.not_ready",
      "backtest.result.unreadable",
      "backtest.run.not_found",
    ]);
    expect(untranslated(codes)).toEqual([]);
  });

  // 계열 합치기 거절(검증 랩 V5-03)도 같은 번역 키 체계다. 전략 이력의 합치기 확인 창이 보인다.
  it("translates every coded lineage merge refusal in both locales", () => {
    const codes = rejectionCodes(
      readOpenApi(),
      "/api/v1/strategies/{strategy_id}/trials/merge",
    );

    expect([...codes].sort()).toEqual([
      "backtest.lineage.already_merged",
      "strategy.not_found",
    ]);
    expect(untranslated(codes)).toEqual([]);
  });

  it("translates every experiment trial submission rejection in both locales", () => {
    expect(untranslated(Object.keys(TRIAL_REJECTION_CODES))).toEqual([]);
  });

  // 실험 경로의 거절(검증 랩 V3-03)도 같은 번역 키 체계다. 코드 목록은 backend 가 스키마 enum 으로 싣는다.
  it("translates every coded experiment rejection in both locales", () => {
    const openapi = readOpenApi();
    const codes = Object.keys(openapi.paths)
      .filter((path) => path.startsWith("/api/v1/experiments"))
      .flatMap((path) => [...rejectionCodes(openapi, path)]);

    expect(codes).toContain("experiment.base.unsaved");
    expect(codes).toContain("experiment.trial.not_retryable");
    expect(untranslated(new Set(codes))).toEqual([]);
  });
});

describe("generated startBacktest error contract", () => {
  it("narrows every coded 422 response without a handwritten DTO", () => {
    expect(
      classify({
        detail: { code: "backtest.run.invalid", message: "missing strategy" },
      }),
    ).toBe("run:missing strategy");
    expect(
      classify({
        detail: {
          code: "backtest.run.field_invalid",
          field: "initial_cash",
          message: "initial_cash must be positive",
        },
      }),
    ).toBe("field:initial_cash");
    expect(
      classify({
        detail: {
          code: "portfolio.strategy.invalid",
          validation: { valid: false, issues: [] },
        },
      }),
    ).toBe("strategy:false");
    expect(
      classify({
        detail: {
          code: "backtest.strategy.requires_upgrade",
          message: "schema 1.0 revision",
        },
      }),
    ).toBe("upgrade:schema 1.0 revision");
  });
});
