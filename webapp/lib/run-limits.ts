// Blank and out-of-range both DROP the key (`null`): the account allowance still binds.
export function parseCap(
  raw: string,
  { int = false, min = 0, max = Number.POSITIVE_INFINITY } = {},
): number | null {
  if (raw.trim() === "") return null;
  const n = int ? Number.parseInt(raw, 10) : Number.parseFloat(raw);
  if (!Number.isFinite(n) || (int && !Number.isInteger(n))) return null;
  return n >= min && n <= max ? n : null;
}
