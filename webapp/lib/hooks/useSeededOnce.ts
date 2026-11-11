import { useState } from "react";

// Runs `seed` once per new non-empty key, in the render phase (webapp/CLAUDE.md § State reset on
// prop change), so the seeded state paints with the key that seeded it. A key equal to the last
// seeded one never re-seeds, so an operator's edits after it stand.
export function useSeededOnce(key: string | null, seed: () => void): void {
  const [seeded, setSeeded] = useState<string | null>(null);
  if (key && key !== seeded) {
    setSeeded(key);
    seed();
  }
}
