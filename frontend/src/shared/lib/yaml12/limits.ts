/**
 * Document limits the backend codec enforces (`CodecLimits` in
 * backend/src/strategy_workbench/application/strategy_authoring/ports/outgoing/document_codec.py).
 * The backend owns the values; the frontend mirrors them so the editor rejects — with the same
 * reason codes — what the server would reject, instead of parsing a document the server will
 * refuse (and freezing the tab doing so).
 */
export const CODEC_LIMITS = {
  maxBytes: 512 * 1024,
  maxDepth: 32,
  maxNodes: 20_000,
} as const;
