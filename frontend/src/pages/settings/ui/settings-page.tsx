import { AiProviderSettings } from "../../../features/configure-ai-providers";
import { t } from "../../../shared/config";
import { useThemePreference, type ThemePreference } from "../../../shared/lib/theme";
import "./settings-page.css";

const THEMES = ["light", "dark", "system"] as const satisfies readonly ThemePreference[];

/**
 * 화면 테마(RD-11-A). 명령 팔레트와 같은 선호값(`ThemePreferenceProvider`)을 고르고, 고르면 바로 바뀌며
 * 저장돼 새로고침 뒤에도 유지된다. 카드의 견본은 그 테마의 표면색으로 칠한 장식이다.
 */
const ThemeSettings = () => {
  const { preference, setPreference } = useThemePreference();
  return (
    <section className="settings-page__section" aria-labelledby="settings-theme-title">
      <h2 id="settings-theme-title">{t("settings.theme.title")}</h2>
      <p className="settings-page__description">{t("settings.theme.description")}</p>
      <div className="settings-theme" role="radiogroup" aria-labelledby="settings-theme-title">
        {THEMES.map((theme) => (
          <label key={theme} className="settings-theme__card" data-selected={preference === theme}>
            <input
              type="radio"
              name="theme"
              value={theme}
              checked={preference === theme}
              onChange={() => setPreference(theme)}
            />
            <span className={`settings-theme__swatch settings-theme__swatch--${theme}`} aria-hidden="true" />
            <span>{t(`settings.theme.${theme}`)}</span>
          </label>
        ))}
      </div>
    </section>
  );
};

/** `/settings` 화면. 화면 테마와 AI 어시스턴트 공급자 섹션이다. */
export const SettingsPage = () => (
  <div className="page settings-page">
    <header className="settings-page__header">
      <h1 className="settings-page__title">{t("page.settings.title")}</h1>
      <p className="settings-page__description">
        {t("page.settings.description")}
      </p>
    </header>
    <ThemeSettings />
    <AiProviderSettings />
  </div>
);
