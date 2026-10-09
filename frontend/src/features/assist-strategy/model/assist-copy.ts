import { tOptional } from "../../../shared/config";

/**
 * 도구 이름 → 화면 문구.
 *
 * 이름 자체는 backend `domain/assistant`가 소유하는 프로토콜 식별자라 번역하지 않고 키로만 쓴다.
 * 서버가 도구를 늘리면 번역이 없을 뿐이므로 그때는 이름을 그대로 보여 준다.
 */
export const assistToolLabel = (name: string): string =>
  tOptional(`assistant.chat.toolName.${name}`) ?? name;
