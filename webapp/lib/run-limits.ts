// What bounds a run, read off what the operator typed. Beside `run-phase.ts`, and named for the
// wire's own word (`run_limits`, `RunLimitOverrides`) rather than a fourth one.
//
// **On SCREEN the word is CAP** — every control that arms one reads "Spend cap" / "Token cap", so
// a label saying "ceiling" is a second word for a thing the surface already names. Prose and
// comments say ceiling freely and that costs nothing; a LABEL does. The ACCOUNT's standing bound
// is the ALLOWANCE, a different thing keeping its own word: a cap composes against it and is
// clamped down to it, never past it.

// A cap draft → the number to SEND, or `null` for "none of mine". Blank is a real answer at every
// surface that offers one — the account's allowance still binds under a launch, a fork inherits
// its parent — so a blank and an out-of-range value both DROP the key rather than arming a bound
// nobody asked for. Each caller states its own bounds, because they are what the field's own
// `min`/`max` already promise on screen.
export function parseCap(
  raw: string,
  { int = false, min = 0, max = Number.POSITIVE_INFINITY } = {},
): number | null {
  if (raw.trim() === "") return null;
  const n = int ? Number.parseInt(raw, 10) : Number.parseFloat(raw);
  if (!Number.isFinite(n) || (int && !Number.isInteger(n))) return null;
  return n >= min && n <= max ? n : null;
}
