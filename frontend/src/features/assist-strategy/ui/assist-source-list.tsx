import type { SourceView } from "../../../entities/assistant";
import { t } from "../../../shared/config";
import { safeExternalLink } from "../model/render-safety";

/**
 * 검색 출처 목록.
 *
 * 제목은 언제나 평문이고 주소가 `http`·`https`일 때만 링크가 된다. 링크에는 `target="_blank"`와
 * `rel="noopener noreferrer"`를 함께 준다 — 모델이 고른 주소로 여는 창이 이 탭을 조작하지 못한다.
 *
 * 제목 옆에 실제 도착지 호스트를 늘 보인다. 제목은 모델이 고른 비신뢰 문자열이라 신뢰할 만한
 * 이름을 달고 다른 곳으로 보낼 수 있고, 스킴 화이트리스트는 그것을 막지 못한다(B-03 리뷰 P2).
 */
export const AssistSourceList = ({
  sources,
}: {
  sources: readonly SourceView[];
}) => {
  if (sources.length === 0) return null;
  return (
    <ul className="assist-sources">
      {sources.map((source, index) => {
        const link = safeExternalLink(source.url);
        return (
          <li className="assist-sources__item" key={`${index}-${source.url}`}>
            {link === null ? (
              <span className="assist-sources__plain">{source.title}</span>
            ) : (
              <>
                <a href={link.href} target="_blank" rel="noopener noreferrer">
                  {source.title}
                </a>{" "}
                <span className="assist-sources__host">
                  {t("assistant.chat.source.host").replace("{host}", link.host)}
                </span>
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
};
