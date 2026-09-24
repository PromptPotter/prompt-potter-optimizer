import path from "node:path";
import { defineConfig } from "vitest/config";

// A `.test.tsx` opts into jsdom with a `// @vitest-environment jsdom` docblock; node is the default.
export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname),
    },
  },
  test: {
    include: [
      // `lib/**` collects `.test.ts` ONLY — a jsdom `.test.tsx` filed there runs nowhere and
      // passes by never running. Put one under `components/**/__tests__/` instead.
      "lib/**/__tests__/**/*.test.ts",
      "components/**/__tests__/**/*.test.{ts,tsx}",
    ],
    environment: "node",
    // `scripts/gate.py` runs four checks at once; an unbounded pool starves its own startup.
    maxWorkers: 4,
  },
});
