import codecs
import locale
import tomllib
from importlib.metadata import version

from pydantic_settings import BaseSettings

from promptpotter.config.paths import env_file_path, source_checkout_root


def _app_version() -> str:
    """A checkout reads `pyproject.toml` itself: editable-install metadata goes stale on a bump."""
    checkout = source_checkout_root()
    if checkout is not None:
        with (checkout / "pyproject.toml").open("rb") as f:
            return str(tomllib.load(f)["project"]["version"])
    return version("promptpotter")


APP_VERSION: str = _app_version()

# Bumping it re-prompts every user for consent; keep in sync with the site's /terms + /privacy.
TERMS_VERSION: str = "2026-09-08"

DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
DEFAULT_BACKEND_ID = "local"

LOCK_TIMEOUT: float = 5.0  # seconds before treating lock as stale


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"

    # A fork edits `deploy-linux/deploy.config`'s brand block; `brand-env.sh` mirrors it here.
    BRAND_SHORT_NAME: str = "PromptPotter"
    BRAND_SERVICE_NAME: str = "PromptPotter Optimizer"
    BRAND_DOCS_URL: str = "https://github.com/PromptPotter/prompt-potter-optimizer"

    # Comma-separated; never `*`, since the app serves credentialed requests.
    ALLOWED_ORIGINS: str = ""

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""

    # provider -> [rpm, tpm] per rolling 60s; a missing provider or a null slot is unthrottled.
    RATE_LIMITS: dict[str, list[int | None]] = {}

    # Read ONLY by the termnorm connector's `auth_token` hook: a second backend declares its own.
    TERMNORM_TOKEN: str = ""

    # Empty is upstream's local compose stack; read only by `connectors/dbllmbench.py::harness_config`.
    DBLLMBENCH_DB_URL: str = ""
    DBLLMBENCH_DB_USERNAME: str = ""
    DBLLMBENCH_DB_PASSWORD: str = ""
    DBLLMBENCH_DB_TLS: bool = False

    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    LANGFUSE_ENABLED: bool = True

    # Rejects binary/Office uploads at ingest: xlsx is a macro / zip-bomb / XXE vector.
    HARDENED_MODE: bool = False

    # Unset: no browser sign-in can claim the box, and it has no admin identity.
    HOST_ADMIN_EMAIL: str = ""

    # Empty accepts any issuer: with two providers wired, the weakest one's email handling then grants the box.
    HOST_ADMIN_ISSUER: str = ""

    # Declared here, never read from `os.environ`: a bare environ read ignores `env_file_path()`.
    ADMIN_BOT_TELEGRAM_TOKEN: str = ""
    ADMIN_BOT_CHAT_ID: str = ""
    ADMIN_BOT_PASSPHRASE: str = ""
    N8N_SIGNUP_WEBHOOK_URL: str = ""

    # LIFETIME total over the account's whole ledger, not per day; the box's operator is exempt.
    FREE_TIER_SPEND_CAP_USD: float = 0.30

    # What ONE metered launch may declare, so no single run takes the whole grant.
    FREE_TIER_LAUNCH_STEP_USD: float = 0.03

    # Sized ABOVE what the USD cap buys on the cheapest model: re-derive it whenever that cap moves.
    FREE_TIER_TOKEN_CAP: int = 5_000_000

    # A ceiling on the remainder once the USD total is understated, never a bonus added to it.
    UNPRICED_GRACE_USD: float = 0.10

    # Per-user token bucket; process state, reset by a restart.
    USER_RATE_BURST: int = 5
    USER_RATE_PER_MIN: float = 1.0

    # The machine's ceiling, not an allowance: `jobs/capacity.py::resolve_run_capacity` may only lower it.
    MACHINE_RUN_CAPACITY: int = 3

    QUEUE_MAX_WAIT_S: float = 6 * 3600.0

    OBS_ENABLED: bool = True

    MLFLOW_ENABLED: bool = False

    model_config = {"env_file": env_file_path(), "case_sensitive": True, "extra": "ignore"}


settings = Settings()


def non_utf8_encoding() -> str | None:
    name = codecs.lookup(locale.getpreferredencoding(False)).name
    return None if "utf-8" in name else name


__all__ = [
    "APP_VERSION",
    "DEFAULT_BACKEND_ID",
    "DEFAULT_BACKEND_URL",
    "LOCK_TIMEOUT",
    "TERMS_VERSION",
    "Settings",
    "non_utf8_encoding",
    "settings",
]
