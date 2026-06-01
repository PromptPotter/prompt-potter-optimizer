import path from "node:path";
import { defineConfig } from "vitest/config";

// Two test classes share this scope:
//   .test.ts  — pure data → data derivations (node env, the default).
//   .test.tsx — UI-primitive render/interaction tests. Each opts into jsdom
//               with a `// @vitest-environment jsdom` docblock at its top, so
//               the fast node default stays for the derivation suite.
// See docs/developer/cycle-fixtures.md for the testing posture and how to
// freeze a new cycle fixture under ../tests/fixtures/cycles/.
export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname),
    },
  },
  test: {
    include: [
      "lib/**/__tests__/**/*.test.ts",
      "components/**/__tests__/**/*.test.{ts,tsx}",
    ],
    environment: "node",
  },
});
