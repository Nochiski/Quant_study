import { t, tOptional } from "../../../shared/config";

/**
 * 백테스트 시작 거절(`startBacktest` 404·409·422) 한 문장. 편집기 툴바와 결과 화면 재실행이 같은 규칙을 쓴다
 * (#260, #268 리뷰 P3-3).
 *
 * 코드의 번역 `backtest.error.<code>` 가 본문이고, 거절이 짚은 칸의 이름을 알면 `backtest.error.<code>.named`
 * 의 `{field}` 자리에 넣는다. 번역이 없으면 일반 문구다. 서버 원문은 여기서 쓰지 않는다 — 접힌 진단 상세로
 * 간다(`.claude/rules/frontend-api-state.md`). 날짜처럼 owner 가 backend 인 값은 번역에 적지 않고 `{이름}`
 * 자리표시자로 두어 detail 이 실은 `values` 로 채운다(검증 랩 V1-01, 연구 구간 잠금).
 */
export const backtestStartRejectionMessage = (
  code: string | null,
  fieldLabel: string | null,
  values: Readonly<Record<string, string>>,
): string => {
  const named =
    code === null || fieldLabel === null
      ? null
      : (tOptional(`backtest.error.${code}.named`)?.replace(
          "{field}",
          fieldLabel,
        ) ?? null);
  return Object.entries(values).reduce(
    (sentence, [name, value]) => sentence.replaceAll(`{${name}}`, value),
    named ??
      (code === null ? null : tOptional(`backtest.error.${code}`)) ??
      t("backtest.start.failedGeneric"),
  );
};
