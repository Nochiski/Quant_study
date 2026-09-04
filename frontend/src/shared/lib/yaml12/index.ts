export {
  diagnosticCode,
  loadYaml12Mapping,
  locateRange,
  parseSource,
  type ParseDiagnostic,
  type ParsedSource,
  type SourceFormat,
  type SourcePosition,
  type SourceRange,
} from "./parse";
export {
  describeYamlCursor,
  templatePointer,
  type YamlCursorContext,
} from "./cursor";
export { CODEC_LIMITS } from "./limits";
export {
  decodePointerSegment,
  escapePointerSegment,
  pointerSegments,
} from "./pointer";
export { locatePointer } from "./source-map";
