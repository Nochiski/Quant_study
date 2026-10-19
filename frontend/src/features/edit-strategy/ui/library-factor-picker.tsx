/**
 * 내 팩터에서 꺼내 쓰기(US-CS-08). 최신 버전의 팩터 항목과 그 그래프가 읽는 파라미터 정의를 문서에 복사한다
 * (`libraryInsertion`). 복사는 source 트랜잭션 한 번이라 되돌리기 한 번에 취소된다.
 */
import { useQuery } from "@tanstack/react-query";
import { useId } from "react";

import {
  libraryFactorsQuery,
  type LibraryFactorBody,
} from "../../../entities/factor-library";
import { t, tFill } from "../../../shared/config";
import { Badge, Button } from "../../../shared/ui";
import type { FormProjection } from "../model/form-projection";
import { libraryInsertion } from "../model/library-factors";
import { NO_FOCUS } from "../model/use-field-editing";
import type { CardContext } from "./pipeline-panel";

/** 꺼내 쓰기의 feedback owner. 안내의 다른 담기와 슬롯을 나눈다. */
const LIBRARY_OWNER = "library";

export const LibraryFactorPicker = ({
  context,
  projection,
}: {
  context: CardContext;
  projection: FormProjection | null;
}) => {
  const library = useQuery(libraryFactorsQuery());
  if (library.isPending)
    return (
      <p className="strategy-guide__hint" role="status">
        {t("library.mine.loading")}
      </p>
    );
  if (library.isError)
    return (
      <p className="strategy-guide__hint" role="alert">
        {t("library.mine.failed")}
      </p>
    );
  if (library.data.items.length === 0)
    return <p className="strategy-guide__hint">{t("library.mine.empty")}</p>;
  return (
    <ul className="strategy-guide__factor-list" aria-label={t("library.tab.mine")}>
      {library.data.items.map((entry) => (
        <LibraryRow
          key={entry.library_id}
          entry={entry}
          context={context}
          projection={projection}
        />
      ))}
    </ul>
  );
};

const LibraryRow = ({
  entry,
  context,
  projection,
}: {
  entry: LibraryFactorBody;
  context: CardContext;
  projection: FormProjection | null;
}) => {
  const reasonId = useId();
  const latest = entry.versions.at(-1)!;
  const insertion = libraryInsertion(
    { factor: latest.factor, parameters: latest.parameters },
    entry.availability.available,
    context.tree,
    projection,
  );
  return (
    <li className="strategy-guide__factor">
      <div>
        <strong>{entry.name}</strong>{" "}
        <Badge tone="neutral">{tFill("library.mine.version", { version: entry.latest_version })}</Badge>{" "}
        {entry.availability.available ? null : (
          <Badge tone="warn">{t("library.mine.unavailable")}</Badge>
        )}
        <p>
          <code>{entry.key}</code>
          {entry.description === "" ? null : ` · ${entry.description}`}
        </p>
      </div>
      <div className="strategy-guide__factor-action">
        <Button
          size="small"
          disabled={insertion.status !== "ready" || context.transactions.settling}
          aria-describedby={insertion.status === "ready" ? undefined : reasonId}
          aria-label={`${entry.name} · ${t("library.mine.add")}`}
          onClick={() => {
            if (insertion.status === "ready")
              context.transactions.apply(
                insertion.operations,
                entry.name,
                LIBRARY_OWNER,
                NO_FOCUS,
              );
          }}
        >
          {t("library.mine.add")}
        </Button>
        {insertion.status === "ready" ? null : (
          <span id={reasonId} className="strategy-guide__hint">
            {t(`library.blocked.${insertion.reason}`)}
          </span>
        )}
      </div>
    </li>
  );
};
