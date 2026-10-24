import { useQuery } from "@tanstack/react-query";

import { t, tFill } from "../../../shared/config";
import { credentialsFileQuery } from "../model/credentials-file-query";
import "./credentials-file-location.css";

/**
 * 연결 정보가 저장되는 파일 경로 한 줄. AI 공급자와 키움 모의 연결 섹션이 같이 쓴다.
 * 경로는 사용자가 복사해 파일을 직접 열어 볼 수 있게 고정폭 글자로 보인다.
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
  const path = file.data.path;
  return (
    <p
      className="credentials-file-location"
      data-testid="credentials-file-location"
    >
      {path === null || path === undefined ? (
        t("settings.credentialsFile.legacy")
      ) : (
        <>
          {tFill("settings.credentialsFile.location", { path: "" })}
          <code className="credentials-file-location__path">{path}</code>
        </>
      )}
    </p>
  );
};
