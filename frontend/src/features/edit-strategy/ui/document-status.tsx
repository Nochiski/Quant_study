import { t } from "../../../shared/config";
import { Badge } from "../../../shared/ui";
import { isSpecStale, type DocumentState } from "../model/document-state";

const PHASE_TONE = {
  editing: "neutral",
  parsing: "neutral",
  "syntax-invalid": "error",
  "structure-invalid": "error",
  "semantic-invalid": "error",
  "structurally-valid": "info",
  "semantically-valid": "ok",
  saved: "ok",
} as const;

/**
 * 문서 상태 배지(검증 통과·구조 오류·STALE·한글 조합). IDE 편집 영역 헤더에 살며 탭과 무관하게
 * 보인다(WORKFLOW P1-01) — Graph·Form에서 편집 결과를 보려고 YAML 탭으로 돌아갈 일이 없다.
 */
export const DocumentStatus = ({ state }: { state: DocumentState }) => (
  <div role="status" aria-label={t("document.status")}>
    <Badge tone={PHASE_TONE[state.phase]}>
      {t(`document.phase.${state.phase}`)}
    </Badge>
    {isSpecStale(state) ? <Badge tone="warn">{t("document.stale")}</Badge> : null}
    {state.composing ? (
      <Badge tone="neutral">{t("document.composing")}</Badge>
    ) : null}
  </div>
);
