import { CredentialsFileLocation } from "../../../entities/credentials-file";
import { AiProviderSettings } from "../../../features/configure-ai-providers";
import { KiwoomMockSettings } from "../../../features/configure-kiwoom-mock";
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

/**
 * AI 공급자와 키움 모의 연결이 함께 쓰는 저장 파일. 두 섹션이 같은 파일이라 화면에 한 번만 둔다
 * (마지막 리뷰 8). 각 섹션 설명은 이 자리를 가리킨다.
 */
const CredentialsFileSettings = () => (
  <section className="settings-page__section" aria-labelledby="settings-credentials-file-title">
    <h2 id="settings-credentials-file-title">{t("settings.credentialsFile.title")}</h2>
    <p className="settings-page__description">{t("settings.credentialsFile.description")}</p>
    <CredentialsFileLocation />
  </section>
);

/** `/settings` 화면. 화면 테마, 연결 정보 저장 위치, AI 공급자, 키움 모의투자 연결 섹션이다. */
export const SettingsPage = () => (
  <div className="page settings-page">
    <header className="settings-page__header">
      <h1 className="settings-page__title">{t("page.settings.title")}</h1>
      <p className="settings-page__description">
        {t("page.settings.description")}
      </p>
    </header>
    <ThemeSettings />
    <CredentialsFileSettings />
    <AiProviderSettings />
    <KiwoomMockSettings />
  </div>
);
