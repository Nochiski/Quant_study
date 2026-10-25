import { useMemo, type ReactNode } from "react";

import { t } from "../../../shared/config";
import {
  parseMarkdown,
  type MarkdownBlock,
  type MarkdownInline,
} from "../model/assist-markdown";
import { safeExternalLink } from "../model/render-safety";

const renderInlines = (inlines: readonly MarkdownInline[]): ReactNode[] =>
  inlines.map((inline, index) => {
    switch (inline.kind) {
      case "text":
        return inline.text;
      case "break":
        return <br key={index} />;
      case "code":
        return (
          <code className="assist-md__code" key={index}>
            {inline.text}
          </code>
        );
      case "strong":
        return <strong key={index}>{renderInlines(inline.children)}</strong>;
      case "em":
        return <em key={index}>{renderInlines(inline.children)}</em>;
      case "del":
        return <del key={index}>{renderInlines(inline.children)}</del>;
      case "link": {
        // 출처 목록과 같은 규칙: `http`·`https`만 링크이고, 이름 옆에 실제 도착지 호스트를 늘 보인다.
        // 모델이 믿음직한 이름으로 다른 곳을 가리킬 수 있기 때문이다(B-03 리뷰 P2).
        const link = safeExternalLink(inline.url);
        if (link === null) {
          return <span key={index}>{renderInlines(inline.children)}</span>;
        }
        return (
          <span key={index}>
            <a href={link.href} target="_blank" rel="noopener noreferrer">
              {renderInlines(inline.children)}
            </a>{" "}
            <span className="assist-sources__host">
              {/* 함수 replacer라 호스트 안의 `$&`가 치환 패턴으로 해석되지 않는다. */}
              {t("assistant.chat.source.host").replace(
                "{host}",
                () => link.host,
              )}
            </span>
          </span>
        );
      }
    }
  });

const renderBlocks = (blocks: readonly MarkdownBlock[]): ReactNode[] =>
  blocks.map((block, index) => {
    switch (block.kind) {
      case "paragraph":
        return <p key={index}>{renderInlines(block.inlines)}</p>;
      case "heading":
        // 대화 안의 제목은 페이지 문서 구조에 끼지 않게 heading 요소 대신 굵은 문단으로 그린다.
        return (
          <p
            className={`assist-md__heading assist-md__heading--${Math.min(block.level, 3)}`}
            key={index}
          >
            {renderInlines(block.inlines)}
          </p>
        );
      case "list": {
        const items = block.items.map((item, itemIndex) => (
          <li key={itemIndex}>{renderBlocks(item)}</li>
        ));
        return block.ordered ? (
          <ol key={index} start={block.start === 1 ? undefined : block.start}>
            {items}
          </ol>
        ) : (
          <ul key={index}>{items}</ul>
        );
      }
      case "code":
        return (
          <pre className="assist-md__pre" key={index}>
            <code>{block.text}</code>
          </pre>
        );
      case "quote":
        return (
          <blockquote className="assist-md__quote" key={index}>
            {renderBlocks(block.blocks)}
          </blockquote>
        );
      case "table":
        return (
          <div className="assist-md__table" key={index}>
            <table>
              <thead>
                <tr>
                  {block.header.map((cell, column) => (
                    <th
                      key={column}
                      style={{ textAlign: block.align[column] ?? undefined }}
                    >
                      {renderInlines(cell)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((cell, column) => (
                      <td
                        key={column}
                        style={{ textAlign: block.align[column] ?? undefined }}
                      >
                        {renderInlines(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      case "rule":
        return <hr className="assist-md__rule" key={index} />;
    }
  });

/**
 * 모델이 쓴 마크다운 본문.
 *
 * 파서가 만든 값 트리를 React 요소로만 그린다 — `dangerouslySetInnerHTML`을 쓰지 않으므로 모델 문자열이
 * HTML로 해석될 자리가 없다(spec D7 렌더 안전).
 */
export const AssistMarkdown = ({
  text,
  className,
}: {
  text: string;
  className?: string;
}) => {
  const blocks = useMemo(() => parseMarkdown(text), [text]);
  return (
    <div
      className={
        className === undefined ? "assist-md" : `assist-md ${className}`
      }
      aria-live="off"
    >
      {renderBlocks(blocks)}
    </div>
  );
};
