import type { BacktestRunSpec } from "../../../shared/api";

/** 전달되는 입력은 생성 SDK 타입 그대로이며 별도 실행 DTO나 캐시를 만들지 않는다. */
export type IdeExperimentSeed = BacktestRunSpec;

declare module "@tanstack/history" {
  interface HistoryState {
    /** IDE의 명시적 이동 입력. 서버 데이터나 저장 문서의 정본이 아니다. */
    experimentSeed?: IdeExperimentSeed;
  }
}
