import os from "node:os";
import path from "node:path";

// The throwaway PROMPTPOTTER_HOME the cold and spend servers bind, which a spec may stock before it asks.
export const COLD_HOME = process.env.PP_E2E_COLD_HOME || path.join(os.tmpdir(), "promptpotter-e2e");

// The throwaway home the walk server binds when `PP_E2E_WALK_WORLD` names a run to walk a copy of.
export const WALK_HOME = path.join(os.tmpdir(), "promptpotter-e2e-walk");
