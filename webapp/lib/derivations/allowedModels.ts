// Client twin of the Python `overlay_sets_model_outside_allowed`
// (`promptpotter/domain/opt_search_point.py`). True iff a fork's `pipeline_overlay`
// steers a node's model/provider to a value the origin has NOT sanctioned — the
// ADR-0005 babysit (grade-C) trigger. Keeps the client warning on the SAME predicate
// the server gate enforces at `fork-cycle` (`dispatcher.py`).
//
// `allowedModels` is the origin's declared allow-list (`CampaignConfig.allowed_models`,
// served on `GET /campaigns/{id}::config.allowed_models`; absent/empty = nothing
// sanctioned = the restrictive default, so ANY model steer counts). A `provider` edit
// has no allow-list to sanction it, so it always counts.
//
// Pure data→data (no React, no I/O) so it rides the lib Vitest scope.
export function overlaySetsModelOutsideAllowed(
  overlay: Record<string, unknown> | null | undefined,
  allowedModels: readonly string[] | null | undefined,
): boolean {
  const allowed = new Set(allowedModels ?? []);
  for (const cfg of Object.values(overlay ?? {})) {
    if (!cfg || typeof cfg !== "object" || Array.isArray(cfg)) continue;
    const c = cfg as Record<string, unknown>;
    if ("provider" in c) return true;
    const model = c.model;
    if (model != null && !allowed.has(String(model))) return true;
  }
  return false;
}
