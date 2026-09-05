import js from "@eslint/js";
import boundaries from "@boundaries/eslint-plugin";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

const layerRules = [
  ["app", ["pages", "widgets", "features", "entities", "shared"]],
  ["pages", ["widgets", "features", "entities", "shared"]],
  ["widgets", ["features", "entities", "shared"]],
  ["features", ["entities", "shared"]],
  ["entities", ["shared"]],
  ["shared", ["shared"]],
].map(([from, allowed]) => ({
  from: { element: { type: from } },
  allow: { to: { element: { types: { anyOf: allowed } } } },
}));

export default tseslint.config(
  {
    ignores: [
      "dist/**",
      "coverage/**",
      "node_modules/**",
      "openapi-ts-error-*.log",
    ],
  },
  { ...js.configs.recommended, files: ["**/*.js"] },
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: globals.browser,
    },
    plugins: {
      boundaries,
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    settings: {
      "import/resolver": {
        typescript: {
          project: "./tsconfig.json",
        },
      },
      "boundaries/include": ["src/**/*"],
      "boundaries/elements": [
        { type: "app", pattern: "src/app", partialMatch: false },
        {
          type: "pages",
          pattern: "src/pages/*",
          partialMatch: false,
          capture: ["slice"],
        },
        {
          type: "widgets",
          pattern: "src/widgets/*",
          partialMatch: false,
          capture: ["slice"],
        },
        {
          type: "features",
          pattern: "src/features/*",
          partialMatch: false,
          capture: ["slice"],
        },
        {
          type: "entities",
          pattern: "src/entities/*",
          partialMatch: false,
          capture: ["slice"],
        },
        {
          type: "shared",
          pattern: "src/shared/*",
          partialMatch: false,
          capture: ["slice"],
        },
      ],
    },
    rules: {
      ...reactHooks.configs.flat.recommended.rules,
      ...reactRefresh.configs.vite.rules,
      "boundaries/dependencies": [
        "error",
        {
          default: "disallow",
          policies: layerRules,
        },
      ],
      "boundaries/no-unknown-dependencies": "error",
    },
  },
  {
    files: ["src/shared/api/generated/**/*.{ts,tsx}"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "boundaries/no-unknown-dependencies": "off",
    },
  },
);
