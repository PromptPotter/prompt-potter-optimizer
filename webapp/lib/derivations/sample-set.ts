export function sameSampleSet(a: number[] | null, b: number[]): boolean {
  if (!a || a.length !== b.length) return false;
  const sa = new Set(a);
  return b.every((x) => sa.has(x));
}

export function toggleInSet(set: number[], sid: number): number[] {
  const next = new Set(set);
  if (next.has(sid)) next.delete(sid);
  else next.add(sid);
  return [...next].sort((a, b) => a - b);
}
