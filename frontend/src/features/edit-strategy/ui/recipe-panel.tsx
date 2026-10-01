import { useEffect, useRef, useState } from "react";
import type { NodeValueType } from "../../../shared/api";
import { t, tCode, tName } from "../../../shared/config";
import { valueAtPointer } from "../../../shared/lib/yaml12";
import { Button } from "../../../shared/ui";
import type { DocumentDiagnostic } from "../model/document-state";
import { projectFactorGraphs } from "../model/factor-graph-projection";
import { projectObjectSection } from "../model/form-projection";
import { nodeSlotsByKind } from "../model/graph-transactions";
import {
  operatorPalette,
  catalogNote,
  type OperatorCatalogState,
  type PaletteEntry,
} from "../model/operator-palette";
import { projectRecipe } from "../model/recipe-projection";
import {
  recipeTransaction,
  type RecipeOperation,
  type RecipeStepSeed,
  type RecipeFailure,
} from "../model/recipe-transactions";
import type { JsonSchema } from "../model/schema-navigator";
import type { ExecutionPlansState } from "../model/use-execution-plans";
import { NO_FOCUS } from "../model/use-field-editing";
import { useRevealSelection } from "../model/use-reveal-selection";
import type { SourceTransactions } from "../model/use-source-transactions";
import { OperatorPalette } from "./operator-palette";
import { FormFieldsEditor, type FormCatalogs } from "./strategy-form-panel";
import { TransactionFeedbackNote } from "./transaction-feedback";
import "./recipe-panel.css";

const OWNER = "recipe";
type Pending = {
  tree: unknown;
  factorPointer: string;
  index: number;
  replace: boolean;
  entry: PaletteEntry;
  previousInput: string;
  fields: string[];
};
export const RecipePanel = ({
  tree,
  schema,
  factorIndex,
  transactions,
  catalogs,
  operators,
  diagnostics,
  plans,
  selectedPointer,
  revealSignal,
  onSelectPointer,
  onAdvanced,
  onBack,
}: {
  tree: unknown;
  schema: JsonSchema;
  factorIndex: number;
  transactions: SourceTransactions;
  catalogs: FormCatalogs;
  operators: OperatorCatalogState;
  diagnostics: DocumentDiagnostic[];
  plans: ExecutionPlansState;
  selectedPointer?: string;
  revealSignal?: number;
  onSelectPointer: (pointer: string) => void;
  onAdvanced: () => void;
  onBack: () => void;
}) => {
  const factorPointer = `/factors/${factorIndex}`;
  const graphPointer = `${factorPointer}/graph`;
  const recipe = projectRecipe(schema, tree, factorPointer);
  const slots = nodeSlotsByKind(schema);
  const groups = operatorPalette(
    schema,
    tree,
    factorPointer,
    operators.status === "ready" ? operators.definitions : null,
  );
  const entries = groups.flatMap((group) => group.entries);
  const projection = projectFactorGraphs(plans, schema);
  const compiled =
    projection.status === "ready"
      ? projection.factors.find((factor) => factor.factorIndex === factorIndex)
      : undefined;
  const [pending, setPending] = useState<Pending | null>(null);
  const [failure, setFailure] = useState<{
    tree: unknown;
    reason: RecipeFailure;
    factorPointer: string;
  } | null>(null);
  const [replacing, setReplacing] = useState<{
    tree: unknown;
    index: number;
    factorPointer: string;
  } | null>(null);
  const [identifiers, setIdentifiers] = useState(false);
  const root = useRevealSelection<HTMLElement>(selectedPointer, revealSignal);

  const busy = transactions.disabled !== null || transactions.settling;
  const names = {
    catalog: (_catalog: string, value: string) =>
      catalogs.equityFields?.find((field) => field.field_id === value)?.label ??
      null,
    reference: () => null,
  };
  const factorSection = projectObjectSection(
    schema,
    tree,
    diagnostics,
    factorPointer,
    "",
  );
  const fields = (pointer: string) => {
    const section = projectObjectSection(
      schema,
      tree,
      diagnostics,
      pointer,
      "",
    );
    const kind = valueAtPointer(tree, `${pointer}/kind`).value;
    const settings = slots.get(String(kind))?.settings ?? [];
    return section === null
      ? null
      : {
          ...section,
          fields: section.fields.filter((field) =>
            settings.some(({ key }) => key === field.key),
          ),
        };
  };
  const entryOf = (pointer: string) => {
    const node = valueAtPointer(tree, pointer).value as Record<string, unknown>;
    const operator = slots.get(String(node.kind))?.operator;
    return entries.find(
      (entry) =>
        entry.kind === node.kind &&
        entry.operator ===
          (operator === null || operator === undefined ? null : node[operator]),
    );
  };
  const commit = (operation: RecipeOperation) => {
    if (busy) return;
    const result = recipeTransaction(tree, schema, factorPointer, operation);
    if ("error" in result) {
      setFailure({ tree, factorPointer, reason: result.error });
      return;
    }
    if (
      result.ops.length === 0 ||
      transactions.apply(result.ops, t("recipe.edit"), OWNER, NO_FOCUS)
    ) {
      setFailure(null);
      setPending(null);
      setReplacing(null);
      // 위치가 바뀐 뒤 옛 node index를 다시 선택하지 않는다. 선택은 새 parse에서 사용자가 고른다.
      onSelectPointer(graphPointer);
    }
  };
  const links = recipe.kind === "chain" ? recipe.links : [];
  const selectedIndex = links.findIndex(
    (link) =>
      selectedPointer === link.pointer ||
      selectedPointer?.startsWith(`${link.pointer}/`) ||
      link.operands.some(
        (pointer) =>
          pointer !== null &&
          (selectedPointer === pointer ||
            selectedPointer?.startsWith(`${pointer}/`)),
      ),
  );
  const replacement =
    replacing !== null &&
    replacing.tree === tree &&
    replacing.factorPointer === factorPointer
      ? replacing.index
      : null;
  const choose = (entry: PaletteEntry) => {
    const own = slots.get(entry.kind)!;
    const index =
      replacement ?? (selectedIndex < 0 ? links.length : selectedIndex + 1);
    const fieldSetting = own.settings.find(
      ({ facts }) => facts.catalog !== null,
    );
    const seed: RecipeStepSeed = {
      kind: entry.kind,
      ...(entry.operator === null
        ? {}
        : { chosen: { operator: entry.operator, params: entry.params } }),
    };
    if (
      own.inputs.length > 1 ||
      (own.inputs.length === 0 && fieldSetting !== undefined)
    ) {
      setPending({
        tree,
        factorPointer,
        index,
        replace: replacement !== null,
        entry,
        previousInput: own.inputs[0]?.key ?? "",
        fields: Array(Math.max(1, own.inputs.length - 1)).fill(""),
      });
    } else
      commit({
        kind: replacement === null ? "insert" : "replace",
        index,
        seed,
      });
  };
  const draft =
    pending !== null &&
    pending.tree === tree &&
    pending.factorPointer === factorPointer
      ? pending
      : null;
  const pickerRef = useRef<HTMLFieldSetElement>(null);
  const pickerIdentity =
    draft === null ? null : `${factorPointer}:${draft.index}:${draft.entry.id}`;
  useEffect(() => {
    if (pickerIdentity !== null)
      pickerRef.current?.querySelector("select")?.focus();
  }, [pickerIdentity]);
  const ownInputs = draft === null ? [] : slots.get(draft.entry.kind)!.inputs;
  const picker =
    draft === null ? null : (
      <fieldset ref={pickerRef} className="recipe__picker" disabled={busy}>
        <legend>
          {draft.entry.name} · {t("recipe.inputs")}
        </legend>
        {ownInputs.length < 2 ? null : (
          <label>
            {t("recipe.previousInput")}
            <select
              value={draft.previousInput}
              onChange={(event) =>
                setPending({ ...draft, previousInput: event.target.value })
              }
            >
              {ownInputs.map(({ key, facts }) => (
                <option key={key} value={key}>
                  {tName(facts.descriptionKey) ?? t("recipe.input")}
                </option>
              ))}
            </select>
          </label>
        )}
        {catalogs.equityFields === null ? (
          <p role="status">{t("contract.catalog.loading")}</p>
        ) : null}
        {draft.fields.map((value, index) => (
          <label key={index}>
            {t("recipe.field")} {index + 1}
            <select
              value={value}
              onChange={(event) =>
                setPending({
                  ...draft,
                  fields: draft.fields.map((previous, i) =>
                    i === index ? event.target.value : previous,
                  ),
                })
              }
            >
              <option value="">{t("form.field.unselected")}</option>
              {catalogs.equityFields?.map((field) => (
                <option key={field.field_id} value={field.field_id}>
                  {field.label}
                </option>
              ))}
            </select>
          </label>
        ))}
        <Button
          disabled={draft.fields.some((value) => value === "")}
          onClick={() => {
            const fieldKind = [...slots].find(
              ([, value]) =>
                value.inputs.length === 0 &&
                value.settings.some(
                  ({ facts }) => facts.catalog === "equity-field",
                ),
            );
            // 카탈로그 마커가 source leaf를 정한다. backend kind 목록을 UI에 복제하지 않는다.
            const sourceKind = fieldKind;
            if (sourceKind === undefined) {
              setFailure({ tree, factorPointer, reason: "unsupported-schema" });
              return;
            }
            const fieldKey = sourceKind[1].settings.find(
              ({ facts }) => facts.catalog !== null,
            )!.key;
            const seed: RecipeStepSeed = {
              kind: draft.entry.kind,
              ...(draft.entry.operator === null
                ? {}
                : {
                    chosen: {
                      operator: draft.entry.operator,
                      params: draft.entry.params,
                    },
                  }),
              ...(ownInputs.length === 0
                ? { settings: { [fieldKey]: draft.fields[0]! } }
                : {
                    previousInput: draft.previousInput,
                    operands: draft.fields.map((field) => ({
                      kind: sourceKind[0],
                      settings: { [fieldKey]: field },
                    })),
                  }),
            };
            commit({
              kind: draft.replace ? "replace" : "insert",
              index: draft.index,
              seed,
            });
          }}
        >
          {t("recipe.confirm")}
        </Button>
        <Button tone="ghost" onClick={() => setPending(null)}>
          {t("recipe.cancel")}
        </Button>
      </fieldset>
    );
  const palette = groups
    .map((group) => ({
      ...group,
      entries: group.entries.filter((entry) => {
        const inputs = slots.get(entry.kind)!.inputs.length;
        return (
          !entry.unsupported &&
          (slots.get(entry.kind)!.operator === null ||
            entry.operator !== null) &&
          (replacement === 0 || links.length === 0 ? inputs === 0 : inputs > 0)
        );
      }),
    }))
    .filter((group) => group.entries.length > 0);
  return (
    <section ref={root} className="recipe" aria-label={t("recipe.title")}>
      <header
        className="recipe__toolbar"
        aria-current={selectedPointer === graphPointer ? "true" : undefined}
      >
        <h2>{t("recipe.title")}</h2>
        <Button tone="ghost" onClick={onBack}>
          {t("recipe.back")}
        </Button>
        <Button tone="ghost" onClick={onAdvanced}>
          {t("recipe.advanced")}
        </Button>
      </header>
      {recipe.kind === "advanced" ? (
        <p>{t("recipe.nonChain")}</p>
      ) : (
        <>
          {factorSection === null ? null : (
            <fieldset disabled={busy} className="recipe__header">
              <FormFieldsEditor
                section={{
                  ...factorSection,
                  fields: factorSection.fields.filter((field) =>
                    ["label", "direction", "weight"].includes(field.key),
                  ),
                }}
                transactions={transactions}
                catalogs={catalogs}
                owner={OWNER}
                names={names}
                showIdentifiers={false}
              />
            </fieldset>
          )}
          {links.length === 0 ? <p>{t("recipe.empty")}</p> : null}
          <ol className="recipe__steps">
            {links.map((link, index) => {
              const entry = entryOf(link.pointer);
              const section = fields(link.pointer);
              const output = compiled?.nodes.find(
                (node) =>
                  node.pointer === link.pointer && node.origin === "document",
              );
              return (
                <li
                  key={String(
                    valueAtPointer(tree, `${link.pointer}/node_id`).value,
                  )}
                >
                  {index === 0 ? null : (
                    <span className="recipe__arrow" aria-hidden="true">
                      ↓
                    </span>
                  )}
                  <article
                    className="recipe__step"
                    aria-label={`${index + 1}. ${entry?.name ?? t("recipe.step")}`}
                    aria-current={index === selectedIndex ? "true" : undefined}
                  >
                    <Button
                      tone="ghost"
                      onClick={() => onSelectPointer(link.pointer)}
                    >
                      {index + 1}. {entry?.name ?? t("recipe.step")}
                    </Button>
                    <p>
                      {groups.find((group) => group.kind === entry?.kind)?.name}
                    </p>
                    <p>{entry?.description}</p>
                    <fieldset disabled={busy}>
                      {section === null ? null : (
                        <FormFieldsEditor
                          section={section}
                          transactions={transactions}
                          catalogs={catalogs}
                          owner={OWNER}
                          names={names}
                          showIdentifiers={false}
                          selectedPointer={selectedPointer}
                        />
                      )}
                      {link.operands
                        .filter(
                          (pointer): pointer is string => pointer !== null,
                        )
                        .map((pointer) => {
                          const operand = fields(pointer);
                          return operand === null ? null : (
                            <div key={pointer}>
                              <h4>{t("recipe.operand")}</h4>
                              <FormFieldsEditor
                                section={operand}
                                transactions={transactions}
                                catalogs={catalogs}
                                owner={OWNER}
                                names={names}
                                showIdentifiers={false}
                                selectedPointer={selectedPointer}
                              />
                            </div>
                          );
                        })}
                    </fieldset>
                    <p>
                      {t("recipe.result")}:{" "}
                      {output?.outputType == null
                        ? t("recipe.awaitCompile")
                        : tCode(
                            `recipe.type.${output.outputType as NodeValueType}`,
                            output.outputType,
                          )}
                      {output?.outputUnit == null
                        ? ""
                        : ` · ${output.outputUnit}`}
                    </p>
                    <div className="recipe__actions">
                      <Button
                        disabled={busy || index < 2}
                        onClick={() =>
                          commit({ kind: "move", index, to: index - 1 })
                        }
                      >
                        {t("recipe.up")}
                      </Button>
                      <Button
                        disabled={
                          busy || index === 0 || index === links.length - 1
                        }
                        onClick={() =>
                          commit({ kind: "move", index, to: index + 1 })
                        }
                      >
                        {t("recipe.down")}
                      </Button>
                      <Button
                        disabled={busy || (index === 0 && links.length > 1)}
                        onClick={() => commit({ kind: "remove", index })}
                      >
                        {t("recipe.remove")}
                      </Button>
                      <Button
                        disabled={busy}
                        onClick={() => {
                          setReplacing({ tree, factorPointer, index });
                          setPending(null);
                        }}
                      >
                        {t("recipe.replace")}
                      </Button>
                    </div>
                  </article>
                </li>
              );
            })}
          </ol>
          {replacement === null ? null : (
            <p role="status">
              {t("recipe.replacing").replace("{step}", String(replacement + 1))}
              <Button
                tone="ghost"
                onClick={() => {
                  setReplacing(null);
                  setPending(null);
                }}
              >
                {t("recipe.cancel")}
              </Button>
            </p>
          )}
          {picker}
          <OperatorPalette
            groups={palette}
            parameterLabels={(entry) =>
              entry.params.map((key) => {
                const name = tName(
                  slots
                    .get(entry.kind)
                    ?.settings.find((slot) => slot.key === key)?.facts
                    .descriptionKey,
                );
                return name ?? key;
              })
            }
            disabledReason={busy ? t("graph.palette.settling") : null}
            catalogNote={catalogNote(operators)}
            onPick={choose}
          />
          {failure === null ||
          failure.tree !== tree ||
          failure.factorPointer !== factorPointer ? null : (
            <p role="alert">
              {tCode(`recipe.error.${failure.reason}`, failure.reason)}
            </p>
          )}
          <TransactionFeedbackNote
            feedback={transactions.feedbackFor(OWNER)}
            owner={OWNER}
          />
          <Button
            tone="ghost"
            aria-expanded={identifiers}
            onClick={() => setIdentifiers(!identifiers)}
          >
            {t("recipe.identifiers")}
          </Button>
          {identifiers ? (
            <pre>
              {JSON.stringify(
                valueAtPointer(tree, graphPointer).value,
                null,
                2,
              )}
            </pre>
          ) : null}
        </>
      )}
    </section>
  );
};
