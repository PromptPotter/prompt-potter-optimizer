import os from "node:os";
import path from "node:path";

// The throwaway PROMPTPOTTER_HOME the cold and spend servers bind, which a spec may stock before it asks.
export const COLD_HOME = process.env.PP_E2E_COLD_HOME || path.join(os.tmpdir(), "promptpotter-e2e");
