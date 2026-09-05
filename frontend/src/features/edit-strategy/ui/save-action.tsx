import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";

type SaveActionProps = {
  canSave: boolean;
  saving: boolean;
  onSave: () => void;
};

/** The editor-header save button; the full toolbar (format, validate) arrives with P3-05. */
export const SaveAction = ({ canSave, saving, onSave }: SaveActionProps) => (
  <Button
    tone="primary"
    size="small"
    disabled={!canSave}
    aria-busy={saving || undefined}
    onClick={onSave}
  >
    {t("save.action")}
  </Button>
);
