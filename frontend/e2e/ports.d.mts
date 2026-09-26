export declare const BACKEND_PORT_ENV: "PW_BACKEND_PORT";
export declare const PREVIEW_PORT_ENV: "PW_PREVIEW_PORT";
export declare const DEFAULT_BACKEND_PORT: 8000;
export declare const DEFAULT_PREVIEW_PORT: 5173;

export declare const readPort: (
  name: string,
  fallback: number,
  env?: NodeJS.ProcessEnv,
) => number;
export declare const backendPort: (env?: NodeJS.ProcessEnv) => number;
export declare const previewPort: (env?: NodeJS.ProcessEnv) => number;
export declare const backendOrigin: (env?: NodeJS.ProcessEnv) => string;
export declare const previewOrigin: (env?: NodeJS.ProcessEnv) => string;
