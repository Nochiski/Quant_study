/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  /** "true" registers the operations placeholder pages; anything else hides them. */
  readonly VITE_ENABLE_OPERATIONS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
