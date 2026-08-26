// Test-only — loads checked-in cycle fixtures from tests/fixtures/cycles/
// for vitest to assert against. See webapp/CLAUDE.md § Testing posture for
// the freezing recipe.
//
// The fixture tree lives outside the webapp/ subtree intentionally:
// fixtures are shared between Python and TypeScript tests, so they live
// at the repo's canonical tests/ location. This helper hides the
// relative-path arithmetic from individual test files.

import fs from "node:fs";
import path from "node:path";
import type { DashboardSnapshot } from "@/lib/poll";

const FIXTURE_ROOT = path.resolve(__dirname, "..", "..", "..", "tests", "fixtures", "cycles");

export function loadCycleFixture(name: string): DashboardSnapshot {
  const filePath = path.join(FIXTURE_ROOT, name, "dashboard.json");
  const raw = fs.readFileSync(filePath, "utf-8");
  return JSON.parse(raw) as DashboardSnapshot;
}
