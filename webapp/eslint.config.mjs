import { defineConfig, globalIgnores } from "eslint/config";
import jsxA11y from "eslint-plugin-jsx-a11y";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

const eslintConfig = defineConfig([
  {
    files: ["**/*.{js,jsx,mjs,ts,tsx,mts}"],
    extends: [reactHooks.configs.flat.recommended, jsxA11y.flatConfigs.recommended],
    plugins: { react },
    settings: { react: { version: "detect" } },
    rules: {
      "react/jsx-key": "error",
      "react/no-unescaped-entities": "error",
      "react/display-name": "error",
      "react/jsx-no-target-blank": "error",
    },
  },
  // A focusable `separator` is the ARIA window-splitter widget, so its tab stop is its own.
  {
    rules: {
      "jsx-a11y/label-has-associated-control": ["error", { controlComponents: ["CommitInput"] }],
      "jsx-a11y/no-noninteractive-tabindex": ["error", { tags: [], roles: ["tabpanel", "separator"], allowExpressionValues: true }],
    },
  },
  {
    files: ["**/*.ts", "**/*.tsx"],
    extends: [tseslint.configs.recommended],
    languageOptions: { parserOptions: { projectService: true } },
    rules: {
      "@typescript-eslint/no-unused-vars": "warn",
      "@typescript-eslint/no-unused-expressions": "warn",
      "@typescript-eslint/no-unnecessary-condition": "error",
      // `||` on a string reads "" as unset (an env var, a served label); `??` would keep it.
      "@typescript-eslint/prefer-nullish-coalescing": ["error", { ignorePrimitives: { string: true } }],
      "@typescript-eslint/no-unnecessary-type-assertion": "error",
      "@typescript-eslint/no-deprecated": "error",
      "@typescript-eslint/no-misused-promises": "error",
    },
  },
  globalIgnores([
    "out/**",
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
