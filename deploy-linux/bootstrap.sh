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
# ------------------------------------------------------------------------

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

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

# --- 4. venv + pip install ----------------------------------------------
if [[ ! -d .venv ]]; then
    say "creating .venv"
    python3.13 -m venv .venv
fi
say "installing python deps (this can take a minute)"
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -e ".[all,dev]"

# --- 5. .env -------------------------------------------------------------
if [[ ! -f .env ]]; then
    say "creating .env from .env.example"
    cp .env.example .env
    chmod 600 .env
    echo
    warn "you MUST set at least one LLM API key in .env before starting the service"
    read -rp "set GROQ_API_KEY now? (paste key or press Enter to skip) > " groq_key
    if [[ -n "${groq_key:-}" ]]; then
        # replace the placeholder line in-place
        sed -i "s|^GROQ_API_KEY=.*|GROQ_API_KEY=${groq_key}|" .env
        say "GROQ_API_KEY set in .env"
    else
        warn "skipped — edit $INSTALL_DIR/.env manually before running the service"
    fi
    # tighten CORS to the public hostname (from deploy.config)
    sed -i "s|^ALLOWED_ORIGINS=.*|ALLOWED_ORIGINS=${ALLOWED_ORIGINS}|" .env
else
    say ".env already exists — leaving it alone"
fi

# --- 6. webapp build -----------------------------------------------------
say "building webapp (static export → $WEBAPP_DIR/out/)"
cd "$WEBAPP_DIR"
npm install
npm run build:deploy   # full shipped artifact: React Compiler + source maps
cd ..

# --- 7. smoke ------------------------------------------------------------
say "smoke test: starting uvicorn for 4 seconds"
.venv/bin/python -m uvicorn "$APP_MODULE" --host "$BIND_HOST" --port "$BIND_PORT" &
uv_pid=$!
sleep 4
if curl -fsS "http://$BIND_HOST:$BIND_PORT$HEALTH_PATH" >/dev/null; then
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
