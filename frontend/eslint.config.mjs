import { FlatCompat } from "@eslint/eslintrc";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import js from "@eslint/js";

/**
 * eslint-config-next 15.5 still ships eslintrc-style configs, so they are
 * bridged into ESLint 9's flat format here rather than spread as if they were
 * already flat — which is what the generated config assumed, and why linting
 * crashed on `nextVitals is not iterable`.
 */
const compat = new FlatCompat({
  baseDirectory: dirname(fileURLToPath(import.meta.url)),
});

export default [
  {
    ignores: [".next/**", "out/**", "build/**", "next-env.d.ts", "node_modules/**"],
  },
  js.configs.recommended,
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      // Unused args are meaningful here: a spread `...rest` keeps a prop out of
      // the DOM on purpose, and stripping it silently would change behaviour.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
];
