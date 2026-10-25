import type { ReactNode } from "react";

import { t } from "../../../shared/config";
import { safeExternalLink } from "../model/render-safety";

/**
 * 모델이 고른 주소로 가는 링크. 검색 출처 목록과 본문 마크다운 링크가 함께 쓴다.
 *
 * 주소가 `http`·`https`일 때만 링크가 되고 나머지는 이름만 글자로 남는다. 링크에는 `target="_blank"`와
 * `rel="noopener noreferrer"`를 함께 준다 — 모델이 고른 주소로 여는 창이 이 탭을 조작하지 못한다.
 *
 * 이름 옆에 실제 도착지 호스트를 늘 보인다. 이름은 모델이 고른 비신뢰 문자열이라 신뢰할 만한 이름을
 * 달고 다른 곳으로 보낼 수 있고, 스킴 화이트리스트는 그것을 막지 못한다(B-03 리뷰 P2).
 */
export const AssistExternalLink = ({
  url,
  children,
  plainClassName,
}: {
  url: string;
  children: ReactNode;
  /** 링크가 되지 못한 이름에 줄 class. */
  plainClassName?: string;
}) => {
  const link = safeExternalLink(url);
  if (link === null) {
    return <span className={plainClassName}>{children}</span>;
  }
  return (
    <>
      <a href={link.href} target="_blank" rel="noopener noreferrer">
        {children}
      </a>{" "}
      <span className="assist-sources__host">
        {/* 함수 replacer라 호스트 안의 `$&`가 치환 패턴으로 해석되지 않는다. */}
        {t("assistant.chat.source.host").replace("{host}", () => link.host)}
      </span>
    </>
  );
};
