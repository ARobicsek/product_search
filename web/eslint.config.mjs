import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Local-only tooling scripts (not deployed). Mix of Node CJS (sync-prompt)
    // and standalone TS harness (test-delete) with looser conventions than
    // the Next.js app code.
    "scripts/**",
  ]),
]);

export default eslintConfig;
