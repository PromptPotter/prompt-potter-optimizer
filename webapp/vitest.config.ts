import path from "node:path";
import { defineConfig } from "vitest/config";

// Two test classes share this scope:
//   .test.ts  — pure data → data derivations (node env, the default).
//   .test.tsx — UI-primitive render/interaction tests. Each opts into jsdom
//               with a `// @vitest-environment jsdom` docblock at its top, so
//               the fast node default stays for the derivation suite.
// See webapp/CLAUDE.md § Testing posture for how to freeze a new cycle
// fixture under ../tests/fixtures/cycles/.
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
    // `scripts/gate.py` runs four checks at once, so this suite must not size its pool to
    // the whole machine: unbounded it forks `availableParallelism() - 1` workers, each
    // paying jsdom setup, and one starved its own startup mid-gate. Four is the gate's own
    // concurrency. Costs ~8s standalone and nothing in the gate, where eslint is the
    // critical path and this check finishes inside its shadow.
    maxWorkers: 4,
  },
});
