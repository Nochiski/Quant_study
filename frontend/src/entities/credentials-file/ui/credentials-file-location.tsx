import { useQuery } from "@tanstack/react-query";

import { t } from "../../../shared/config";
import { credentialsFileQuery } from "../model/credentials-file-query";
import "./credentials-file-location.css";

/**
 * 연결 정보가 저장되는 파일 경로와 그 파일의 읽기 문제. AI 공급자와 키움 모의 연결 섹션이 같이 쓴다.
 *
 * 경로는 사용자가 복사해 파일을 직접 열어 볼 수 있게 고정폭 글자로 보인다. 문서 폴더가 클라우드로
 * 동기화되면 평문 키도 올라간다는 고지를 함께 둔다(SoT 리뷰 S2). 파일을 못 읽거나 줄이 빠졌으면 서버가
 * 완성한 문장(값 없이 사유와 줄 이름)을 그대로 보인다.
 */
export const CredentialsFileLocation = () => {
  const file = useQuery(credentialsFileQuery());
  if (file.isPending) return null;
  if (file.isError) {
    return (
      <p className="credentials-file-location">
        {t("settings.credentialsFile.unavailable")}
      </p>
    );
  }
  const { path, problems } = file.data;
  if (path === null) {
    return (
      <p className="credentials-file-location">
        {t("settings.credentialsFile.legacy")}
      </p>
    );
  }
  return (
    <div className="credentials-file-location">
      <p data-testid="credentials-file-location">
        {t("settings.credentialsFile.label")}{" "}
        <code className="credentials-file-location__path">{path}</code>
      </p>
      <p className="credentials-file-location__notice">
        {t("settings.credentialsFile.syncNotice")}
      </p>
      {problems.length > 0 ? (
        <div
          className="credentials-file-location__problems"
          role="alert"
          data-testid="credentials-file-problems"
        >
          <p>{t("settings.credentialsFile.problemsTitle")}</p>
          <ul>
            {problems.map((problem) => (
              <li key={`${problem.section}:${problem.message}`}>
                {problem.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
};
