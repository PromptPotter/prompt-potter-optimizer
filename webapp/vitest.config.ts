import path from "node:path";
import { defineConfig } from "vitest/config";

// Neither project isolates: files share module state, so a test that writes a module-level store or a global resets it itself.
export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname),
    },
  },
  test: {
    isolate: false,
    pool: "threads",
    fileParallelism: false,
    projects: [
      {
        extends: true,
        test: {
          name: "logic",
          include: ["{lib,components}/**/__tests__/**/*.test.ts"],
          environment: "node",
        },
      },
      {
        extends: true,
        test: {
          name: "dom",
          // `lib/**` holds no `.test.tsx` on purpose: a render test belongs to a component.
          include: ["components/**/__tests__/**/*.test.tsx"],
          environment: "jsdom",
          setupFiles: ["lib/test-utils/dom-setup.ts"],
        },
      },
    ],
  },
});
