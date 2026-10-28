import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const a11y = nextVitals.find((c) => c.plugins?.["jsx-a11y"]).plugins["jsx-a11y"].configs.recommended.rules;

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // A focusable `separator` is the ARIA window-splitter widget, so its tab stop is its own.
  {
    rules: {
      ...a11y,
      "jsx-a11y/label-has-associated-control": ["error", { controlComponents: ["CommitInput"] }],
      "jsx-a11y/no-noninteractive-tabindex": ["error", { tags: [], roles: ["tabpanel", "separator"], allowExpressionValues: true }],
    },
  },
  {
    files: ["**/*.ts", "**/*.tsx"],
    languageOptions: { parserOptions: { projectService: true } },
    rules: {
      "@typescript-eslint/no-unnecessary-condition": "error",
      // `||` on a string reads "" as unset (an env var, a served label); `??` would keep it.
      "@typescript-eslint/prefer-nullish-coalescing": ["error", { ignorePrimitives: { string: true } }],
      "@typescript-eslint/no-unnecessary-type-assertion": "error",
      "@typescript-eslint/no-deprecated": "error",
      "@typescript-eslint/no-misused-promises": "error",
    },
  },
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Playwright writes and removes it while the gate's lint walks the tree.
    "test-results/**",
  ]),
  // Siblings INSIDE a barrel dir use relative imports, which these patterns do not match.
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
