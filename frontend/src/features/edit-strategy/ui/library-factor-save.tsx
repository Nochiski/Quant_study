/**
 * 팩터 카드의 "내 팩터로 저장"(US-CS-08). 문서의 팩터 항목과 그 그래프가 읽는 파라미터 정의를 그대로 보내고,
 * 검증·버전 신원은 backend 가 compile 로 정한다. 같은 키가 이미 있으면 그 항목의 새 버전으로 저장할지
 * 묻는다. 저장은 문서를 바꾸지 않는다.
 */
import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import {
  libraryFactorsQuery,
  useAddLibraryFactorVersion,
  useSaveLibraryFactor,
} from "../../../entities/factor-library";
import { ApiRequestError } from "../../../shared/api";
import { t, tFill, tOptional } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import { factorForLibrary } from "../model/library-factors";
import "./library-factor.css";

const failureText = (error: unknown): string =>
  (error instanceof ApiRequestError && error.code !== undefined
    ? tOptional(`library.error.${error.code}`)
    : null) ?? t("library.error.unknown");

export const LibraryFactorSave = ({
  tree,
  itemPointer,
  label,
}: {
  tree: unknown;
  itemPointer: string;
  /** 카드 이름(기본 저장 이름). */
  label: string;
}) => {
  const inputId = useId();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(label);
  const save = useSaveLibraryFactor();
  const addVersion = useAddLibraryFactorVersion();
  const existingId =
    save.error instanceof ApiRequestError &&
    save.error.code === "library.factor.key_exists"
      ? (save.error.values.existing_library_id ?? null)
      : null;
  const library = useQuery({
    ...libraryFactorsQuery(true),
    enabled: existingId !== null,
  });
  const existing = library.data?.items.find(
    (item) => item.library_id === existingId,
  );
  const copy = factorForLibrary(tree, itemPointer);
  const saved = addVersion.data ?? save.data;
  const busy = save.isPending || addVersion.isPending;
  if (!open)
    return (
      <Button
        size="small"
        tone="ghost"
        onClick={() => {
          setName(label);
          save.reset();
          addVersion.reset();
          setOpen(true);
        }}
        aria-label={`${label} · ${t("library.save.open")}`}
      >
        {t("library.save.open")}
      </Button>
    );
  return (
    <div
      className="library-save"
      role="group"
      aria-label={`${label} · ${t("library.save.open")}`}
    >
      <label htmlFor={inputId}>{t("library.save.name")}</label>
      <input
        id={inputId}
        className="ui-input"
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <Button
        size="small"
        tone="primary"
        disabled={copy === null || name.trim() === "" || busy}
        onClick={() => {
          if (copy !== null)
            save.mutate({
              name,
              factor: copy.factor,
              parameters: [...copy.parameters],
            });
        }}
      >
        {t("library.save.submit")}
      </Button>
      <Button size="small" tone="ghost" onClick={() => setOpen(false)}>
        {t("library.save.close")}
      </Button>
      <p role="status" className="library-save__status">
        {saved !== undefined
          ? tFill("library.save.done", {
              name: saved.name,
              version: saved.latest_version,
            })
          : busy
            ? t("library.save.saving")
            : null}
      </p>
      {save.error !== null && saved === undefined ? (
        <p role="alert" className="library-save__error">
          {failureText(save.error)}
        </p>
      ) : null}
      {existing !== undefined && saved === undefined ? (
        <Button
          size="small"
          disabled={copy === null || busy}
          onClick={() => {
            if (copy !== null)
              addVersion.mutate({
                libraryId: existing.library_id,
                expectedLatest: existing.latest_version,
                factor: copy.factor,
                parameters: [...copy.parameters],
              });
          }}
        >
          {tFill("library.save.asVersion", {
            name: existing.name,
            version: existing.latest_version + 1,
          })}
        </Button>
      ) : null}
      {addVersion.error !== null ? (
        <p role="alert" className="library-save__error">
          {failureText(addVersion.error)}
        </p>
      ) : null}
    </div>
  );
};
