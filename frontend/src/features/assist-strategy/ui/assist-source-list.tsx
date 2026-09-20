import type { SourceView } from "../../../entities/assistant";
import { safeExternalUrl } from "../model/render-safety";

/**
 * 검색 출처 목록.
 *
 * 제목은 언제나 평문이고 주소가 `http`·`https`일 때만 링크가 된다. 링크에는 `target="_blank"`와
 * `rel="noopener noreferrer"`를 함께 준다 — 모델이 고른 주소로 여는 창이 이 탭을 조작하지 못한다.
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
        const href = safeExternalUrl(source.url);
        return (
          <li className="assist-sources__item" key={`${index}-${source.url}`}>
            {href === null ? (
              <span className="assist-sources__plain">{source.title}</span>
            ) : (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {source.title}
              </a>
            )}
          </li>
        );
      })}
    </ul>
  );
};
