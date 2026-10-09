import { AiProviderSettings } from "../../../features/configure-ai-providers";
import { t } from "../../../shared/config";
import "./settings-page.css";

/**
 * `/settings` 화면. 지금은 AI 어시스턴트 공급자 섹션 하나이고, 설정 항목이 늘어나면 섹션을 덧붙인다.
 */
export const SettingsPage = () => (
  <div className="settings-page">
    <header className="settings-page__header">
      <h1 className="settings-page__title">{t("page.settings.title")}</h1>
      <p className="settings-page__description">
        {t("page.settings.description")}
      </p>
    </header>
    <AiProviderSettings />
  </div>
);
