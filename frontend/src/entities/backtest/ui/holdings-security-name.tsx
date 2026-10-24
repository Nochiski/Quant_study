import { securityCode, securityName, type HoldingRowView } from "./holdings-format";

/** 종목 이름과 코드. 디렉터리가 이름을 모르는 종목은 id 를 이름 자리에 둔다. */
export const SecurityName = ({ security }: { security: HoldingRowView["security"] }) => {
  const code = securityCode(security);
  return (
    <span className="holdings__security">
      <span className="holdings__security-name">{securityName(security)}</span>
      {code === null ? null : <code>{code}</code>}
    </span>
  );
};
