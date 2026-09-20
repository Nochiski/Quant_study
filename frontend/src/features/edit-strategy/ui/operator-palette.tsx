import { useId, useState } from "react";

import { t } from "../../../shared/config";
import { Badge } from "../../../shared/ui";
import {
  entrySignature,
  filterPalette,
  type PaletteEntry,
  type PaletteGroup,
} from "../model/operator-palette";

type OperatorPaletteProps = {
  groups: readonly PaletteGroup[];
  /**
   * 지금 항목을 고를 수 없는 이유(잠긴 편집기·직전 편집 반영 중). null이 아니면 버튼이 비활성이고
   * 그 사유가 버튼의 `aria-describedby`로 붙는다 — 눌리지 않는 버튼이 이유를 말하지 않으면 조용한
   * 실패다(WORKFLOW P1-04).
   */
  disabledReason: string | null;
  /** 카탈로그를 아직 못 받았을 때의 안내. 팔레트는 노드 kind만으로 계속 그린다. */
  catalogNote: string | null;
  onPick: (entry: PaletteEntry) => void;
};

/**
 * 연산자 먼저 고르기(WORKFLOW P1-04, spec D8). kind 드롭다운 대신 "무엇을 계산할지"를 검색 가능한
 * 목록으로 보이고, 고른 항목의 `(kind, operator)`를 `addNode`에 넘겨 kind와 파라미터 기본값이
 * 따라오게 한다. 목록·이름·설명·계산식은 전부 카탈로그와 runtime schema가 낸 것이고 여기서 손으로
 * 적지 않는다(`operator-palette.ts`).
 */
export const OperatorPalette = ({
  groups,
  disabledReason,
  catalogNote,
  onPick,
}: OperatorPaletteProps) => {
  const [query, setQuery] = useState("");
  const id = useId();
  const noteId = `${id}-note`;
  const reasonId = `${id}-reason`;
  const filtered = filterPalette(groups, query);
  const describedBy = [
    catalogNote === null ? null : noteId,
    disabledReason === null ? null : reasonId,
  ].filter((value): value is string => value !== null);
  return (
    <div
      className="factor-graph__palette"
      role="group"
      aria-label={t("graph.palette.label")}
    >
      <input
        type="search"
        className="factor-graph__palette-search"
        aria-label={t("graph.palette.search")}
        placeholder={t("graph.palette.searchPlaceholder")}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      {catalogNote === null ? null : (
        <p id={noteId} className="factor-graph__palette-note">
          {catalogNote}
        </p>
      )}
      {disabledReason === null ? null : (
        <p id={reasonId} className="factor-graph__palette-note">
          {disabledReason}
        </p>
      )}
      {filtered.length === 0 ? (
        <p className="factor-graph__editor-state" role="status">
          {t("graph.palette.empty")}
        </p>
      ) : (
        <div className="factor-graph__palette-groups">
          {filtered.map((group) => (
            <section
              key={group.kind}
              className="factor-graph__palette-group"
              aria-labelledby={`${id}-${group.kind}`}
            >
              <h4 id={`${id}-${group.kind}`}>{group.name}</h4>
              <ul>
                {group.entries.map((entry) => (
                  <PaletteItem
                    key={entry.id}
                    entry={entry}
                    describedBy={describedBy}
                    disabled={disabledReason !== null}
                    onPick={onPick}
                  />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  );
};

const PaletteItem = ({
  entry,
  describedBy,
  disabled,
  onPick,
}: {
  entry: PaletteEntry;
  describedBy: readonly string[];
  disabled: boolean;
  onPick: (entry: PaletteEntry) => void;
}) => {
  const id = useId();
  const bodyId = `${id}-body`;
  const signature = entrySignature(entry);
  return (
    <li className="factor-graph__palette-item">
      <button
        type="button"
        disabled={disabled}
        aria-describedby={[bodyId, ...describedBy].join(" ")}
        aria-label={t("graph.palette.addNode").replace("{operator}", entry.name)}
        onClick={() => onPick(entry)}
      >
        <strong>{entry.name}</strong>
        {entry.formula === null ? null : <code>{entry.formula}</code>}
        {entry.unsupported ? (
          <Badge tone="warn">{t("graph.palette.unsupportedBadge")}</Badge>
        ) : null}
      </button>
      {/* 설명·입력 개수·미지원 사유는 본문이다 — `title`로 감추면 키보드·터치에서 읽히지 않는다(P1-04). */}
      <p id={bodyId} className="factor-graph__palette-body">
        {entry.description === null ? null : <span>{entry.description}</span>}
        {signature === null ? null : (
          <span className="factor-graph__palette-signature">{signature}</span>
        )}
        {entry.unsupported ? (
          <span className="factor-graph__palette-warning">
            {t("graph.palette.unsupported")}
          </span>
        ) : null}
      </p>
    </li>
  );
};
