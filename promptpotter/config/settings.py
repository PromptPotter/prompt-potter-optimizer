"""Application settings + global constants. The module-level constants are the single source for prompt field lists,
persistence versioning and service-level defaults."""

import math
import tomllib
from importlib.metadata import version

from pydantic_settings import BaseSettings

from promptpotter.config.paths import env_file_path, source_checkout_root


def _app_version() -> str:
    """One version owner: ``pyproject.toml``. The dev tree reads the file (editable-install metadata goes stale on a bump);
    an installed wheel reads its own. Whether a checkout sits beside the package is ``source_checkout_root``'s question."""
    checkout = source_checkout_root()
    if checkout is not None:
        with (checkout / "pyproject.toml").open("rb") as f:
            return str(tomllib.load(f)["project"]["version"])
    return version("promptpotter")


APP_VERSION: str = _app_version()

# The version the consent gate requires. Bumping it re-prompts every user, since the version
# recorded in ``user.json`` no longer matches. Keep in sync with the marketing site's
# /terms + /privacy.
TERMS_VERSION: str = "2026-06-21"

# Defaults for backend connection (not env-driven — override via CLI args)
DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
DEFAULT_BACKEND_ID = "local"

DATASET_NAME: str = "ground_truth"
NO_RESULT: str = "NO_RESULT"

# stores/measurement_archive — file lock parameters
LOCK_TIMEOUT: float = 5.0  # seconds before treating lock as stale


# The decomposition field SET; the render ORDER is per class (`PromptTemplate.RENDER_ORDER`).
PROMPT_STRING_FIELDS: list[str] = [
    "persona",
    "task_intent",
    "problem_description",
    "instruction",
    "thinking_style",
    "answer_format",
]

# Above this many distinct ground truths a task's answer space is "open" (free-text /
# ranking) — no enumerable label identity. Shared by the answer_distribution collapse
# detector and the earned-block library's task-fit signature, so both draw the same line.
ANSWER_SPACE_CAP: int = 10

# task_context sub-fields that L1 may emit alongside prompt/node overrides.
TASK_CONTEXT_OVERRIDES: frozenset[str] = frozenset({"upstream_context", "downstream_context"})

# Populates ``PipelineNode.param_types`` so a dataset overlay need not spell these out. An
# overlay may add backend-specific types via the node's ``optimizer.param_types`` block, which
# overrides these; inference from ``node.config`` Python types is the last-resort fallback.
WELL_KNOWN_PARAM_TYPES: dict[str, str] = {
    # Universal LLM-call params — same shape across every provider.
    "temperature": "number",
    "top_p": "number",
    "max_tokens": "integer",
    "max_completion_tokens": "integer",
    "thinking_budget": "integer",
    "seed": "integer",
    "model": "string",
    "provider": "string",
    "reasoning_effort": "string",
    # PromptTemplate scheme — six string fields render() assembles.
    "persona": "string",
    "task_intent": "string",
    "problem_description": "string",
    "instruction": "string",
    "thinking_style": "string",
    "answer_format": "string",
}


# Wall-clock ceiling on one optimizer round-trip. The provider SDK's own timeout is a
# per-read-gap timeout, not a total one, so a reasoning model streaming slowly never trips it
# and the call hangs indefinitely. Sized for the worst case — the initial round-trip PLUS one
# schema-repair retry — or a healthy-but-slow call false-halts. One transient timeout is
# retried; a second halts the loop with ``StopReason.OPTIMIZER_TIMEOUT``.
OPTIMIZER_CALL_DEADLINE_S: float = 180.0

# Per-node ceiling on a COMPOSED optimizer prompt. It ALARMS; it never cuts — length is bounded
# where each item is PRODUCED, so a prompt over its ceiling is the report that one of those
# bounds failed, and cutting here could only choose which half the model sees. Per node rather
# than one shared number, which fires on every call and teaches nothing. `l3_plan` has never
# fired and takes l2_context's; `checkin` runs around the loop and never composes here.
OPTIMIZER_PROMPT_BUDGET_CHARS: dict[str, int] = {
    "l1_generate": 19_000,
    "l1_critique": 14_500,
    "l2_context": 12_000,
    "l3_plan": 12_000,
}

# PoBB elimination — a candidate stops when its P(best) drops below ε. The runtime value is
# ``CampaignConfig.pobb_epsilon``; this is the single default every entry point references so
# the number cannot drift. Set at the most aggressive threshold before false-cuts of true
# winners climb, measured on the archived round corpus.
POBB_DEFAULT_EPSILON: float = 0.15

# How many bank samples the origin is scored on when nothing declares otherwise. The single
# default BOTH loops reference — ``CampaignConfig.sp_budget_origin`` and
# ``InnerBenchmarkConfig.n_samples_origin`` — so the outer recursion and the inner instrument
# cannot drift onto different rulers. Deliberately ABOVE the ``sp_budget_ttest`` default:
# θ_origin is the term every candidate delta subtracts, so its variance lands in EVERY
# comparison, and its rows are content-addressed cache paid for once per config. Candidate
# breadth is paid per candidate per round for no shared-cache payback. A bank smaller than this
# scores whole, which is the right answer there rather than an error.
DEFAULT_ORIGIN_BUDGET: int = 40

# UCB1 exploration weight for the lineage rewind pick. sqrt(2) is UCB1's regret-optimal constant
# for rewards in [0, 1], which the θ values are min-max normalized into, so it means what the
# literature says. Deliberately NOT a campaign knob: one rewind costs a whole cycle, so this is
# a property of the search rather than a per-run dial.
UCB_EXPLORATION_C: float = math.sqrt(2)


class Settings(BaseSettings):
    # Environment
    ENVIRONMENT: str = "development"

    # Display identity. `deploy-linux/deploy.config`'s brand block is the ONE declaration a
    # fork edits; `brand-env.sh` mirrors it here and into the webapp's `NEXT_PUBLIC_*` twins.
    # The package, the CLI verb and the state dir are NOT brand — that is a different tier.
    BRAND_SHORT_NAME: str = "PromptPotter"
    BRAND_SERVICE_NAME: str = "PromptPotter Optimizer"
    BRAND_DOCS_URL: str = "https://github.com/PromptPotter/prompt-potter-optimizer"

    # Comma-separated CORS allowlist. Empty by default because the webapp is same-origin, so
    # cross-origin access is opt-in — and never `*`, since the app serves credentialed requests.
    ALLOWED_ORIGINS: str = ""

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    # Keys only. The optimizer's provider + model are per node in
    # ``promptpotter/assets/optimizer/pipeline.yaml``; there is no env-var default for either.
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""

    # Per-provider tier caps (rolling 60s window), keyed by provider → [rpm, tpm].
    # Omit a provider, or use null for a slot → no client-side throttle (the limiter
    # then self-tunes from response headers). Example (Groq free tier, 5 req/min +
    # 8000 tokens/min for gpt-oss-120b): RATE_LIMITS='{"groq": [5, 8000]}'
    RATE_LIMITS: dict[str, list[int | None]] = {}

    # TermNorm's credential, nobody else's: read ONLY by the termnorm connector's
    # ``auth_token`` hook, so a second remote backend declares its own or gets no auth header.
    # TermNorm gates it behind its own flag, so unset is the normal local posture.
    TERMNORM_TOKEN: str = ""

    # Langfuse Observability (cloud.langfuse.com)
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    LANGFUSE_ENABLED: bool = True

    # When True, binary/Office uploads are rejected at ingest rather than parsed — xlsx is a
    # macro / zip-bomb / XXE vector. The hook for upload-surface hardening generally.
    HARDENED_MODE: bool = False

    # WHO may claim this box from the browser. Signing up entitles, so entitlement can no longer
    # stand in for this: unset means no browser sign-in writes the claim marker, and the box has
    # no admin identity until it is set. A hosted deployment must declare it.
    HOST_ADMIN_EMAIL: str = ""

    # ...and WHICH ISSUER must have vouched for that address. An email is a claim a provider makes,
    # so the box's most privileged decision should not rest on one provider's word alone: with two
    # providers wired, matching on the address by itself lets whichever of them has the weakest
    # email handling grant the box. Pinning the issuer means a provider added later cannot
    # re-open that by default.
    #
    # Empty accepts any issuer, which is the behaviour every existing box already has — so this
    # never locks an operator out on deploy. Set it to the `iss` of the provider you actually sign
    # in with (Google: "https://accounts.google.com").
    HOST_ADMIN_ISSUER: str = ""

    # The ADR-0004 operator-admin channel: the bot's own Telegram credentials, plus the n8n door a
    # new account is announced to. Declared here rather than read from `os.environ` so there is ONE
    # answer to "where does a key live" — pydantic consults the process environment AND
    # `env_file_path()`, while a bare environ read sees only what systemd's `EnvironmentFile`
    # exported, and silently ignores the same key written to the install's own file.
    ADMIN_BOT_TELEGRAM_TOKEN: str = ""
    ADMIN_BOT_CHAT_ID: str = ""
    ADMIN_BOT_PASSPHRASE: str = ""
    N8N_SIGNUP_WEBHOOK_URL: str = ""

    # The lifetime USD ceiling a free-tier account spends against — TOTAL, not per day, and summed
    # over the account's whole ledger. It is the only thing bounding a stranger who signs up, since
    # signing up is now the grant. A per-user override lives on `user.json::spend_budget_usd_total`;
    # the operator of the box is exempt (`quota.py::spends_the_hosts_own_key`).
    FREE_TIER_SPEND_CAP_USD: float = 0.30

    # The same ceiling in the unit a missing rate cannot blind (ADR-0003 D1). Sized ABOVE what the
    # USD one buys on the cheapest configured model, so it binds only once that one has; re-derive
    # it whenever the USD ceiling moves. Per-user override: `user.json::token_budget_total`.
    FREE_TIER_TOKEN_CAP: int = 5_000_000

    # What a metered account may still spend once its USD total is known to be understated. A
    # CEILING on the remainder, never a bonus added to it: an exhausted account gets nothing.
    UNPRICED_GRACE_USD: float = 0.10

    # The per-user token bucket every metered gesture draws on — a campaign launch and a check-in
    # resolver turn keep separate buckets of this shape. Abuse-bound, not audit-bound: process
    # state, reset by a restart. The burst is what a CONVERSATION spends in one sitting, so it is
    # the arm to move if the check-in chat starts refusing a real operator.
    USER_RATE_BURST: int = 5
    USER_RATE_PER_MIN: float = 1.0

    # How many campaigns the server admits at once; 1 = strictly sequential, and a launch
    # while a run is in flight gets 409 `machine_busy`. This is the concurrent-serving lever,
    # but DO NOT raise it above 1 until BYO per-user keys and a per-tenant RateLimiter land —
    # N parallel runs on the shared key/rate-bucket would cross-bill and throttle.
    MACHINE_RUN_CAPACITY: int = 1

    # File-based observability (traces, events.jsonl)
    OBS_ENABLED: bool = True

    # Opt-in: MLflowSink writes per-round MLflow runs to ``traces/mlruns/``.
    MLFLOW_ENABLED: bool = False

    # Resolved, never CWD-relative — see ``config/paths.py::env_file_path``.
    model_config = {"env_file": env_file_path(), "case_sensitive": True, "extra": "ignore"}


settings = Settings()


__all__ = [
    "ANSWER_SPACE_CAP",
    "APP_VERSION",
    "DATASET_NAME",
    "DEFAULT_BACKEND_ID",
    "DEFAULT_BACKEND_URL",
    "DEFAULT_ORIGIN_BUDGET",
    "LOCK_TIMEOUT",
    "NO_RESULT",
    "OPTIMIZER_CALL_DEADLINE_S",
    "OPTIMIZER_PROMPT_BUDGET_CHARS",
    "POBB_DEFAULT_EPSILON",
    "PROMPT_STRING_FIELDS",
    "TASK_CONTEXT_OVERRIDES",
    "TERMS_VERSION",
    "WELL_KNOWN_PARAM_TYPES",
    "Settings",
    "settings",
]
