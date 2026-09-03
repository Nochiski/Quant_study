const ko = {
  "builder.eyebrow": "STRATEGY WORKBENCH",
  "builder.title": "전략의 의미는 하나, 편집 방식은 자유롭게",
  "builder.subtitle":
    "Quick Builder와 Advanced Graph가 동일한 StrategySpec을 편집합니다.",
  "builder.mode.quick": "Quick Builder",
  "builder.mode.advanced": "Advanced Graph",
  "builder.save": "전략 저장",
  "builder.saveRevision": "새 리비전 저장",
  "builder.validate": "검증",
  "builder.loading": "전략 계약을 불러오는 중입니다.",
  "builder.loadError": "백엔드에 연결할 수 없습니다. API 서버를 확인하세요.",
  "builder.clean": "저장된 상태",
  "builder.dirty": "저장하지 않은 변경",
  "builder.valid": "실행 가능한 전략",
  "builder.invalid": "수정이 필요한 전략",
  "builder.saved": "리비전이 저장되었습니다.",
  "strategy.title": "전략 이름",
  "strategy.selectionCount": "선택 종목 수",
  "strategy.maxNameWeight": "종목당 최대 비중",
  "strategy.factorWeight": "팩터 비중",
  "strategy.graphEmpty": "아직 표현식 노드가 없습니다.",
  "strategy.pipeline.data": "1 데이터",
  "strategy.pipeline.factor": "2 팩터 · 신호",
  "strategy.pipeline.portfolio": "3 포트폴리오",
  "strategy.pipeline.risk": "4 리스크",
  "strategy.pipeline.execution": "5 실행",
} as const;

export type MessageKey = keyof typeof ko;

export const t = (key: MessageKey): string => ko[key];
