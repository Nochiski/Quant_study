import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import type { StrategySpec } from "../../../entities/strategy";
import { strategyWorkbenchApi } from "../../../shared/api";
import { t } from "../../../shared/config";
import { StrategyDraftContext } from "./strategy-draft-context";

type SavedIdentity = {
  strategyId: string;
  revision: number;
};

const StrategyDraftSession = ({
  initial,
  children,
}: {
  initial: StrategySpec;
  children: ReactNode;
}) => {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(initial);
  const [dirty, setDirty] = useState(false);
  const [savedIdentity, setSavedIdentity] = useState<SavedIdentity | null>(
    null,
  );
  const [notice, setNotice] = useState<string | null>(null);

  const validation = useMutation({
    mutationFn: () => strategyWorkbenchApi.validate(draft),
  });
  const persistence = useMutation({
    mutationFn: () =>
      savedIdentity === null
        ? strategyWorkbenchApi.create(draft)
        : strategyWorkbenchApi.revise(savedIdentity, draft),
    onSuccess: (saved) => {
      const identity = saved.spec.identity;
      queryClient.setQueryData(
        ["strategy", identity.strategy_id, identity.revision],
        saved,
      );
      setDraft(saved.spec);
      setSavedIdentity({
        strategyId: identity.strategy_id,
        revision: identity.revision,
      });
      setDirty(false);
      setNotice(t("builder.saved"));
    },
  });

  const pending = validation.isPending || persistence.isPending;

  return (
    <StrategyDraftContext.Provider
      value={{
        draft,
        dirty,
        savedRevision: savedIdentity?.revision ?? null,
        validation: validation.data ?? null,
        pending,
        notice,
        update: (recipe) => {
          setDraft((current) => recipe(current));
          setDirty(true);
          setNotice(null);
        },
        validate: async () => {
          await validation.mutateAsync();
        },
        save: async () => {
          await persistence.mutateAsync();
        },
      }}
    >
      {children}
    </StrategyDraftContext.Provider>
  );
};

export const StrategyDraftProvider = ({
  children,
}: {
  children: ReactNode;
}) => {
  const template = useQuery({
    queryKey: ["strategy", "template"],
    queryFn: strategyWorkbenchApi.getTemplate,
    staleTime: Number.POSITIVE_INFINITY,
  });

  if (template.isPending) {
    return <p className="state-message">{t("builder.loading")}</p>;
  }
  if (template.isError) {
    return (
      <p className="state-message state-message--error">
        {t("builder.loadError")}
      </p>
    );
  }

  return (
    <StrategyDraftSession initial={template.data}>
      {children}
    </StrategyDraftSession>
  );
};
