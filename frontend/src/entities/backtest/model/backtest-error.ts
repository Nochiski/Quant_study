import { ApiRequestError } from "../../../shared/api";
import { t, tOptional, type MessageKey } from "../../../shared/config";

/**
 * 실행 API가 코드로 답한 거절·실패의 번역 `backtest.error.<code>`. 시작 거절(`startBacktest` 404·409·422)과
 * 결과 조회 실패(`getBacktestResult` 404·409·410)가 같은 번역을 쓰고, 추적 실패도 추적 고유 코드가 아니면 이
 * 번역으로 간다 — 실행 설정 거절은 두 경로가 같은 코드다(#351). 코드 전수는 `backtest-error-contract.test.ts`·
 * `trace-error-message.test.ts`가 `openapi.json`과 대조한다(#260, #330). 거절이 짚은 칸의 이름을 알면
 * `backtest.error.<code>.named`의 `{field}` 자리에 넣는다. 날짜처럼 owner 가 backend 인 값은 번역에 적지 않고
 * `{이름}` 자리표시자로 두어 detail 이 실은 `values` 로 채운다(검증 랩 V1-01, 연구 구간 잠금). 번역이 없으면
 * null이고, 일반 문장은 부르는 쪽이 고른다. 서버 원문은 여기서 쓰지 않는다 — 접힌 진단 상세로 간다
 * (`.claude/rules/frontend-api-state.md`).
 */
export const backtestErrorSentence = (
  code: string | null,
  fieldLabel: string | null = null,
  values: Readonly<Record<string, string>> = {},
): string | null => {
  if (code === null) return null;
  const named =
    fieldLabel === null
      ? null
      : (tOptional(`backtest.error.${code}.named`)?.replace(
          "{field}",
          fieldLabel,
        ) ?? null);
  const sentence = named ?? tOptional(`backtest.error.${code}`);
  return sentence === null
    ? null
    : Object.entries(values).reduce(
        (filled, [name, value]) => filled.replaceAll(`{${name}}`, value),
        sentence,
      );
};

/**
 * 코드로 답한 요청 거절(`ApiRequestError`)의 번역 문장. 코드가 없거나 번역이 없으면 `fallback` 문구다 — 실험
 * 미리 계산·만들기·조작처럼 실행 접수와 같은 코드 체계를 쓰는 화면이 같은 규칙으로 말한다.
 */
export const requestRejectionMessage = (
  error: unknown,
  fallback: MessageKey,
): string =>
  (error instanceof ApiRequestError
    ? backtestErrorSentence(error.code ?? null, null, error.values)
    : null) ?? t(fallback);

/**
 * 백테스트 시작 거절 한 문장. 편집기 툴바와 결과 화면 재실행이 같은 규칙을 쓴다(#260, #268 리뷰 P3-3). 번역이
 * 없으면 일반 문구다.
 */
export const backtestStartRejectionMessage = (
  code: string | null,
  fieldLabel: string | null,
  values: Readonly<Record<string, string>>,
): string =>
  backtestErrorSentence(code, fieldLabel, values) ??
  t("backtest.start.failedGeneric");
