/**
 * 모델이 만든 링크를 화면에 걸어도 되는지 판정한다.
 *
 * 검색 결과는 이 기능의 유일한 비신뢰 입력이고(spec D8), 출처 URL은 그 결과에서 그대로 온다.
 * `http`·`https`만 링크가 되며 나머지(`javascript:`, `data:`, `file:`)는 텍스트로 남는다(spec D7).
 * 본문 텍스트 쪽은 평문 렌더라 별도 위생 처리가 필요 없다 — React가 문자 그대로 넣는다.
 */
export const safeExternalUrl = (url: string): string | null => {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    // 절대 URL이 아니면(상대 경로·빈 문자열) 출처로 쓰지 않는다.
    return null;
  }
  return parsed.protocol === "http:" || parsed.protocol === "https:"
    ? parsed.href
    : null;
};
