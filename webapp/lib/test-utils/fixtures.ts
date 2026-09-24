// Loads the checked-in cycle fixtures at the repo's `tests/fixtures/cycles/`, shared with pytest.

import fs from "node:fs";
import path from "node:path";
import type { DashboardSnapshot } from "@/lib/poll";

const FIXTURE_ROOT = path.resolve(__dirname, "..", "..", "..", "tests", "fixtures", "cycles");

export function loadCycleFixture(name: string): DashboardSnapshot {
  const filePath = path.join(FIXTURE_ROOT, name, "dashboard.json");
  const raw = fs.readFileSync(filePath, "utf-8");
  return JSON.parse(raw) as DashboardSnapshot;
}
