import path from "node:path";
import { defineConfig } from "vitest/config";

// Scoped to lib/ + components/* derivations — pure data → data helpers
// that have no React rendering and earn unit tests. See
// docs/developer/cycle-fixtures.md for the testing posture and how to
// freeze a new cycle fixture under ../tests/fixtures/cycles/.
export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname),
    },
  },
  test: {
    include: ["lib/**/__tests__/**/*.test.ts", "components/**/__tests__/**/*.test.ts"],
    environment: "node",
  },
});
