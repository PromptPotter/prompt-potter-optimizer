// Class-name join. Standardises the codebase's pervasive
// `` `base${cond ? " mod" : ""}` `` pattern into one readable call:
//
//   cx("hs-cell", folded && "folded", isRank && "rank")
//   cx("connector", stateCls)
//
// Falsy parts (false / null / undefined / "") are dropped, so a guard like
// `cond && "mod"` contributes nothing when `cond` is false. This is the
// state-class convention for component modules — see webapp/CLAUDE.md.

type ClassValue = string | false | null | undefined;

export function cx(...parts: ClassValue[]): string {
  return parts.filter(Boolean).join(" ");
}
