import type { SourceView } from "../../../entities/assistant";
import { AssistExternalLink } from "./assist-external-link";

/**
 * 검색 출처 목록.
 *
 * 제목은 언제나 평문이다. 링크로 만들지와 도착지 호스트 표시는 `AssistExternalLink`가 정한다.
 */
export const AssistSourceList = ({
  sources,
}: {
  sources: readonly SourceView[];
}) => {
  if (sources.length === 0) return null;
  return (
    <ul className="assist-sources">
      {sources.map((source, index) => (
        <li className="assist-sources__item" key={`${index}-${source.url}`}>
          <AssistExternalLink
            url={source.url}
            plainClassName="assist-sources__plain"
          >
            {source.title}
          </AssistExternalLink>
        </li>
      ))}
    </ul>
  );
};
