#!/usr/bin/env bash
# Linux bootstrap — clones the repo, installs Python 3.13 if missing, installs
# Node.js 20 if missing, sets up a venv, builds the webapp, and creates an .env.
# Idempotent: safe to re-run. Reads adopter values from deploy.config — see
# deploy.config.example.
set -euo pipefail

# --- config + knobs -----------------------------------------------------
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$HERE/deploy.config" ]] && source "$HERE/deploy.config"
APP_NAME="${APP_NAME:-myapp}"
APP_MODULE="${APP_MODULE:-myapp.main:app}"
REPO_URL="${REPO_URL:-https://github.com/CHANGE-ME/your-repo.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/$APP_NAME/your-repo}"
BIND_HOST="${BIND_HOST:-127.0.0.1}"
BIND_PORT="${BIND_PORT:-8001}"
WEBAPP_DIR="${WEBAPP_DIR:-webapp}"
HEALTH_PATH="${HEALTH_PATH:-/api/v1/health}"
ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-https://app.example.com}"
# The ONE env file — where this script seeds secrets AND where systemd later reads them. Keep the
# two the same or they silently diverge: a key written here never reaches the service. See
# deploy.config.example for why an SELinux box has to move it under /etc.
ENV_FILE="${ENV_FILE:-$INSTALL_DIR/.env}"
# ------------------------------------------------------------------------

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

# set_env_kv + the brand fan-out, shared with update.sh.
source "$HERE/brand-env.sh"
source "$HERE/health.sh"

[[ "$(uname -s)" == "Linux" ]] || die "this script is Linux-only"
[[ "$REPO_URL" != *"CHANGE-ME"* ]] || die "edit REPO_URL at the top of bootstrap.sh first (or pass REPO_URL=...)"

# --- 0. detect package manager ------------------------------------------
if command -v dnf >/dev/null 2>&1; then
    PKG=dnf
elif command -v apt-get >/dev/null 2>&1; then
    PKG=apt
else
    die "no supported package manager (need dnf or apt-get). manual install required."
fi
say "package manager: $PKG"

# --- 1. git + curl -------------------------------------------------------
if ! command -v git >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
    say "installing git + curl"
    case "$PKG" in
        dnf) sudo dnf install -y git curl ;;
        apt) sudo apt-get update && sudo apt-get install -y git curl ;;
    esac
fi

# --- 2. Python 3.13 ------------------------------------------------------
if command -v python3.13 >/dev/null 2>&1; then
    say "python3.13 already installed: $(python3.13 --version)"
else
    case "$PKG" in
        dnf)
            say "installing python3.13 via dnf"
            sudo dnf install -y python3.13 python3.13-devel
            ;;
        apt)
            say "installing python3.13 via deadsnakes PPA (Ubuntu/Debian)"
            if ! command -v add-apt-repository >/dev/null 2>&1; then
                sudo apt-get update
                sudo apt-get install -y software-properties-common
            fi
            sudo add-apt-repository -y ppa:deadsnakes/ppa
            sudo apt-get update
            sudo apt-get install -y python3.13 python3.13-venv python3.13-dev
            ;;
    esac
fi

# --- 3. Node 20+ ---------------------------------------------------------
if command -v node >/dev/null 2>&1 && [[ "$(node -v | cut -dv -f2 | cut -d. -f1)" -ge 20 ]]; then
    say "node $(node -v) already installed"
else
    case "$PKG" in
        dnf)
            say "installing node.js via dnf (Fedora ships 20+)"
            sudo dnf install -y nodejs npm
            ;;
        apt)
            say "installing node.js 20 via NodeSource"
            curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
            sudo apt-get install -y nodejs
            ;;
    esac
fi

# --- 3. clone repo -------------------------------------------------------
if [[ -d "$INSTALL_DIR/.git" ]]; then
    say "repo already at $INSTALL_DIR — pulling latest"
    git -C "$INSTALL_DIR" pull --ff-only
else
    say "cloning $REPO_URL → $INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# --- 4. venv + uv sync (pinned graph from uv.lock) ----------------------
if [[ ! -d .venv ]]; then
    say "creating .venv"
    python3.13 -m venv .venv
fi
say "installing python deps from uv.lock (this can take a minute)"
# Install the exact pinned graph from uv.lock — same source of truth as CI.
# uv is fetched to ~/.local/bin (outside .venv, so a sync never wipes it).
command -v uv >/dev/null 2>&1 || python3 -m pip install --user -q uv
PATH="$HOME/.local/bin:$PATH" uv sync --frozen --extra all --extra dev

# --- 5. env file ---------------------------------------------------------
if [[ ! -f "$ENV_FILE" ]]; then
    say "creating $ENV_FILE from .env.example"
    # Outside $HOME the file belongs to root — systemd reads it as root, and /etc is where SELinux
    # permits that — so creating it takes the same privilege its edits do.
    [[ -w "$(dirname "$ENV_FILE")" ]] && priv="" || priv="sudo"
    $priv install -m 600 .env.example "$ENV_FILE"
    echo
    warn "you MUST set at least one LLM API key in $ENV_FILE before starting the service"
    read -rp "set GROQ_API_KEY now? (paste key or press Enter to skip) > " groq_key
    if [[ -n "${groq_key:-}" ]]; then
        set_env_kv "$ENV_FILE" GROQ_API_KEY "$groq_key"
        say "GROQ_API_KEY set in $ENV_FILE"
    else
        warn "skipped — edit $ENV_FILE manually before running the service"
    fi
    # tighten CORS to the public hostname (from deploy.config)
    set_env_kv "$ENV_FILE" ALLOWED_ORIGINS "$ALLOWED_ORIGINS"
else
    say "$ENV_FILE already exists — leaving it alone"
fi

# Brand is re-applied on every run, existing env file or not: deploy.config is the
# declaration, so editing it and re-running must actually repaint the install.
brand_write_env "$ENV_FILE"

# --- 5b. shared TermNorm bearer token (both sides already implement the check) ---
# The optimizer authenticates to the TermNorm backend with a shared secret sent as
# `Authorization: Bearer` (PromptPotter: connectors/termnorm.py; TermNorm:
# config/middleware.py::bearer_auth_middleware, gated on TERMNORM_REQUIRE_AUTH). This
# only PROVISIONS the secret — generated once, written to BOTH .env files so they match.
# Idempotent: an existing non-empty token is reused, never clobbered.
CUR_TOKEN="$(read_env_kv "$ENV_FILE" TERMNORM_TOKEN)"
if [[ -z "$CUR_TOKEN" ]]; then
    CUR_TOKEN="$(openssl rand -hex 32 2>/dev/null || head -c32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    set_env_kv "$ENV_FILE" TERMNORM_TOKEN "$CUR_TOKEN"
    say "generated a shared TERMNORM_TOKEN in $ENV_FILE"
fi
if [[ -n "${BACKEND_DIR:-}" && -f "$BACKEND_DIR/.env" ]]; then
    set_env_kv "$BACKEND_DIR/.env" TERMNORM_TOKEN "$CUR_TOKEN"
    set_env_kv "$BACKEND_DIR/.env" TERMNORM_REQUIRE_AUTH true
    say "enabled bearer auth on the TermNorm backend ($BACKEND_DIR/.env) with the shared token"
elif [[ -n "${BACKEND_DIR:-}" ]]; then
    warn "BACKEND_DIR set but $BACKEND_DIR/.env missing — set TERMNORM_TOKEN=$CUR_TOKEN + TERMNORM_REQUIRE_AUTH=true there by hand"
else
    say "backend side not co-managed (BACKEND_DIR unset) — set the SAME TERMNORM_TOKEN + TERMNORM_REQUIRE_AUTH=true in TermNorm's .env"
fi

# --- 6. webapp build -----------------------------------------------------
say "building webapp (static export → $WEBAPP_DIR/out/)"
brand_export_webapp   # Next inlines NEXT_PUBLIC_* at build time — the rebuild IS the rename
cd "$WEBAPP_DIR"
npm install
npm run build:deploy   # full shipped artifact: React Compiler + source maps
cd ..

# --- 7. smoke ------------------------------------------------------------
say "smoke test: starting uvicorn until it answers"
.venv/bin/python -m uvicorn "$APP_MODULE" --host "$BIND_HOST" --port "$BIND_PORT" &
uv_pid=$!
if wait_healthy "http://$BIND_HOST:$BIND_PORT$HEALTH_PATH"; then
    say "health check passed"
else
    warn "health check failed — check logs above"
fi
kill "$uv_pid" 2>/dev/null || true
wait "$uv_pid" 2>/dev/null || true

printf '\n\033[1;32mbootstrap done.\033[0m\n\n'
printf 'next steps (from the same dir as this script):\n'
printf '  ./install-service.sh    # uvicorn as systemd\n'
printf '  ./install-tunnel.sh     # cloudflared tunnel\n\n'
printf 'if the smoke health check failed, fix it before continuing.\n'
