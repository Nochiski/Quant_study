import { t, tOptional } from "../../../shared/config";

/**
 * 백테스트 API가 코드로 답한 거절·실패의 번역 `backtest.error.<code>`. 시작 거절(`startBacktest` 404·409·422)과
 * 결과 조회 실패(`getBacktestResult` 404·409·410)가 같은 번역을 쓴다 — 두 경로의 코드 전수는
 * `backtest-error-contract.test.ts`가 `openapi.json`과 대조한다(#260, #330). 거절이 짚은 칸의 이름을 알면
 * `backtest.error.<code>.named`의 `{field}` 자리에 넣는다. 번역이 없으면 null이고, 일반 문장은 부르는 쪽이
 * 고른다. 서버 원문은 여기서 쓰지 않는다 — 접힌 진단 상세로 간다(`.claude/rules/frontend-api-state.md`).
 */
export const backtestErrorSentence = (
  code: string | null,
  fieldLabel: string | null = null,
): string | null => {
  if (code === null) return null;
  const named =
    fieldLabel === null
      ? null
      : (tOptional(`backtest.error.${code}.named`)?.replace(
          "{field}",
          fieldLabel,
        ) ?? null);
  return named ?? tOptional(`backtest.error.${code}`);
};

/**
 * 백테스트 시작 거절 한 문장. 편집기 툴바와 결과 화면 재실행이 같은 규칙을 쓴다(#260, #268 리뷰 P3-3). 번역이
 * 없으면 일반 문구다. 날짜처럼 owner 가 backend 인 값은 번역에 적지 않고 `{이름}` 자리표시자로 두어 detail 이
 * 실은 `values` 로 채운다(검증 랩 V1-01, 연구 구간 잠금).
 */
export const backtestStartRejectionMessage = (
  code: string | null,
  fieldLabel: string | null,
  values: Readonly<Record<string, string>>,
): string =>
  Object.entries(values).reduce(
    (sentence, [name, value]) => sentence.replaceAll(`{${name}}`, value),
    backtestErrorSentence(code, fieldLabel) ??
      t("backtest.start.failedGeneric"),
  );
