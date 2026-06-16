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
  ]),
  // Anti-rot: where a barrel exists, import the barrel — not a deep path.
  // Keeps the public surface declared (a deep import re-leaks internals).
  // Sibling modules INSIDE these dirs use relative imports (./x), unaffected.
  {
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            { group: ["@/lib/derivations/*"], message: "Import from the @/lib/derivations barrel, not a deep path." },
            { group: ["@/lib/types/*"], message: "Import from the @/lib/types barrel, not a deep path." },
            { group: ["@/components/ui/*", "!@/components/ui/*.css"], message: "Import from the @/components/ui barrel, not a deep path (CSS modules excepted)." },
            { group: ["@/components/workflow/*"], message: "Import from the @/components/workflow barrel, not a deep path." },
          ],
        },
      ],
    },
  },
]);

export default eslintConfig;
