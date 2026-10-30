import codecs
import locale
import re
import subprocess
import tomllib
from importlib.metadata import version
from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator
from pydantic_settings import BaseSettings

from promptpotter.config.paths import env_file_path, source_checkout_root


class HostCoupon(BaseModel):
    """What each account may send on THIS box's keys over its whole life, never per day; the box's
    operator is exempt, and an account's `grant.json` replaces the face."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # `null` leaves a unit unbounded.
    usd: float | None = 0.30
    # Sized ABOVE what the USD face buys on the cheapest model: re-derive it whenever that moves.
    tokens: int | None = 5_000_000
    # Set: an account holding ANY key of its own has no coupon left on any provider.
    void_on_byo: bool = False


def _app_version() -> str:
    """A checkout reads `pyproject.toml` itself: editable-install metadata goes stale on a bump."""
    checkout = source_checkout_root()
    if checkout is not None:
        with (checkout / "pyproject.toml").open("rb") as f:
            return str(tomllib.load(f)["project"]["version"])
    return version("promptpotter")


APP_VERSION: str = _app_version()


def _code_revision() -> str:
    """A checkout's commit, else the installed release: a version is bumped per release, not per landing."""
    checkout = source_checkout_root()
    if checkout is None:
        return f"v{APP_VERSION}"
    return subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()


# Read at import: a sync of the checkout after it never relabels a process still running the old
# code, which is what a landing compares every live producer by (`jobs/machine_status.py`).
CODE_REVISION: str = _code_revision()

# Bumping it re-prompts every user for consent; keep in sync with the site's /terms + /privacy.
TERMS_VERSION: str = "2026-10-11"

# Seconds a writer waits on a held file lock before raising; an OS lock dies with its holder.
LOCK_TIMEOUT: float = 5.0

_ORIGIN = re.compile(r"https?://[A-Za-z0-9.-]+(:\d{1,5})?")


class Settings(BaseSettings):
    # `off` runs every request as the CLI's on-disk identity: a local harness, never a served box.
    PROMPTPOTTER_AUTH: Literal["on", "off"] = "on"

    # A fork edits `deploy-linux/deploy.config`'s brand block; `brand-env.sh` mirrors it here.
    BRAND_SHORT_NAME: str = "PromptPotter"
    BRAND_SERVICE_NAME: str = "PromptPotter Optimizer"
    BRAND_DOCS_URL: str = "https://github.com/PromptPotter/prompt-potter-optimizer"

    # Comma-separated exact origins: a credentialed app answers no `*`, path or pattern.
    ALLOWED_ORIGINS: str = ""

    @field_validator("ALLOWED_ORIGINS")
    @classmethod
    def _exact_origins(cls, value: str) -> str:
        loose = [o for o in map(str.strip, value.split(",")) if o and not _ORIGIN.fullmatch(o)]
        if loose:
            raise ValueError(
                f"ALLOWED_ORIGINS takes scheme://host[:port] origins only, not {loose}"
            )
        return value

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    # `SecretStr` IS the secrecy: the log redactor scrubs every such field whatever its name, and no repr shows one.
    OPENAI_API_KEY: SecretStr = SecretStr("")
    ANTHROPIC_API_KEY: SecretStr = SecretStr("")
    GROQ_API_KEY: SecretStr = SecretStr("")
    OPENROUTER_API_KEY: SecretStr = SecretStr("")

    # provider -> [rpm, tpm] per rolling 60s; a missing provider or a null slot is unthrottled.
    RATE_LIMITS: dict[str, list[int | None]] = {}

    # Read ONLY by the termnorm connector's `endpoint` and `auth_token` hooks: a second backend
    # declares its own. The URL is the operator's alone, so the token never travels elsewhere.
    TERMNORM_URL: str = "http://127.0.0.1:8000"
    TERMNORM_TOKEN: SecretStr = SecretStr("")

    # Empty is upstream's local compose stack; read only by `connectors/dbllmbench.py::harness_config`.
    DBLLMBENCH_DB_URL: str = ""
    DBLLMBENCH_DB_USERNAME: str = ""
    DBLLMBENCH_DB_PASSWORD: SecretStr = SecretStr("")
    DBLLMBENCH_DB_TLS: bool = False

    LANGFUSE_PUBLIC_KEY: SecretStr = SecretStr("")
    LANGFUSE_SECRET_KEY: SecretStr = SecretStr("")
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    LANGFUSE_ENABLED: bool = True

    # Rejects binary/Office uploads at ingest: xlsx is a macro / zip-bomb / XXE vector.
    HARDENED_MODE: bool = False

    # Comma-separated install datasets a non-host account sees; unset, all of them (a developer's install).
    DATASET_OFFER: str = ""

    @property
    def dataset_offer(self) -> frozenset[str] | None:
        names = frozenset(n.strip() for n in self.DATASET_OFFER.split(",") if n.strip())
        return names or None

    # Empty: each account's archive is its own. A name: one install-owned archive home every account
    # files into and replays from, disclosing what each ran to the others (docs/decisions.md § 0008).
    SHARED_ARCHIVE: str = ""

    # `own`: an account sees another's slots only as a count — a box anyone can sign up to. The host sees all.
    MACHINE_STATUS_SCOPE: Literal["own", "all"] = "own"

    # Unset: no browser sign-in can claim the box, and it has no admin identity.
    HOST_ADMIN_EMAIL: str = ""

    # Empty accepts any issuer: with two providers wired, the weakest one's email handling then grants the box.
    HOST_ADMIN_ISSUER: str = ""

    # Accounts on disk, the host's included, past which no new sign-in opens one; 0 closes sign-up.
    # The host's worst-case exposure to strangers is this times the coupon's face.
    MAX_ACCOUNTS: int = 100

    # Declared here, never read from `os.environ`: a bare environ read ignores `env_file_path()`.
    ADMIN_BOT_TELEGRAM_TOKEN: SecretStr = SecretStr("")
    ADMIN_BOT_CHAT_ID: str = ""
    ADMIN_BOT_PASSPHRASE: SecretStr = SecretStr("")
    # A webhook URL is the credential: whoever holds it fires the workflow.
    N8N_SIGNUP_WEBHOOK_URL: SecretStr = SecretStr("")
    N8N_TRAIL_WEBHOOK_URL: SecretStr = SecretStr("")
    N8N_TRAIL_WEBHOOK_SECRET: SecretStr = SecretStr("")

    # Unset: `/site-records` does not exist. Set: whoever bears it reads and writes those rows.
    SITE_RECORDS_TOKEN: SecretStr = SecretStr("")

    # A JSON object, like RATE_LIMITS; a unit left out keeps its default.
    HOST_COUPON: HostCoupon = HostCoupon()

    # A ceiling on the remainder once the USD total is understated, never a bonus added to it.
    UNPRICED_GRACE_USD: float = 0.10

    # Per-user token bucket; process state, reset by a restart.
    USER_RATE_BURST: int = 5
    USER_RATE_PER_MIN: float = 1.0

    # The machine's ceiling, not an allowance: `jobs/capacity.py::resolve_run_capacity` may only lower it.
    MACHINE_RUN_CAPACITY: int = 3

    QUEUE_MAX_WAIT_S: float = 6 * 3600.0

    OBS_ENABLED: bool = True

    # Unset: MLflow receives nothing. Set: that server takes each round as a run and each winner as a prompt version.
    MLFLOW_TRACKING_URI: str = ""

    model_config = {"env_file": env_file_path(), "case_sensitive": True, "extra": "ignore"}


settings = Settings()


def non_utf8_encoding() -> str | None:
    name = codecs.lookup(locale.getpreferredencoding(False)).name
    return None if "utf-8" in name else name


__all__ = [
    "APP_VERSION",
    "LOCK_TIMEOUT",
    "TERMS_VERSION",
    "Settings",
    "non_utf8_encoding",
    "settings",
]
