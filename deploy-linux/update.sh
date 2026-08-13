#!/usr/bin/env bash
# update.sh — the only command you run after a change. Idempotent: pulls latest,
# refreshes deps, rebuilds the webapp, and restarts the app (+ its backend, if
# configured). Force-matches origin, so it never stalls on a diverged box —
# tracked files mirror the remote; .env / runtime data are gitignored and survive.
#
#   ./update.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$HERE/deploy.config" ]] && source "$HERE/deploy.config"
APP_NAME="${APP_NAME:-myapp}"
SERVICE_NAME="${SERVICE_NAME:-$APP_NAME}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/$APP_NAME/your-repo}"
WEBAPP_DIR="${WEBAPP_DIR:-webapp}"
BIND_PORT="${BIND_PORT:-8001}"
HEALTH_PATH="${HEALTH_PATH:-/api/v1/health}"
BACKEND_DIR="${BACKEND_DIR:-}"
BACKEND_SERVICE="${BACKEND_SERVICE:-}"
BACKEND_PORT="${BACKEND_PORT:-8000}"

# set_env_kv + the brand fan-out, shared with bootstrap.sh.
source "$HERE/brand-env.sh"
# wait_healthy — shared with every other script that starts something.
source "$HERE/health.sh"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[1;31m✗\033[0m %s\n' "$*"; }
sync() { git -C "$1" fetch --quiet origin && git -C "$1" reset --hard --quiet "@{u}"; }   # mirror origin

# --- preflight: refuse the placeholder fallback -----------------------------
# Without deploy.config, INSTALL_DIR defaults to a generic placeholder that
# doesn't exist — fail with the fix instead of a cryptic `git -C` error.
if [[ ! -f "$HERE/deploy.config" ]]; then
  bad "no deploy.config — create it first:"
  echo "    cp $HERE/deploy.config.example $HERE/deploy.config && \$EDITOR $HERE/deploy.config"
  exit 1
fi
[[ -d "$INSTALL_DIR" ]] || { bad "INSTALL_DIR not found: $INSTALL_DIR — fix INSTALL_DIR in $HERE/deploy.config"; exit 1; }

# --- app: sync → deps → webapp → restart ------------------------------------
say "app: $INSTALL_DIR"
sync "$INSTALL_DIR"
# Install the exact pinned graph from uv.lock — same source of truth as CI.
# uv is fetched to ~/.local/bin (outside .venv, so a sync never wipes it).
command -v uv >/dev/null 2>&1 || { command -v "$HOME/.local/bin/uv" >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh; }
( cd "$INSTALL_DIR" && PATH="$HOME/.local/bin:$PATH" uv sync --frozen --extra all )
# A dep rebuild relabels new .venv files user_home_t; re-apply the persisted
# bin_t rule (set by install-service.sh) so systemd can still exec them under
# SELinux enforcing — otherwise the service silently 203/EXEC crash-loops.
if command -v getenforce >/dev/null 2>&1 && [[ "$(getenforce)" == "Enforcing" ]]; then
  sudo restorecon -RF "$INSTALL_DIR/.venv/bin"
fi
# `CampaignConfig` is extra="forbid", so a field this sync dropped makes every
# campaign.json still naming it unreadable — the campaign 500s on first touch.
# Re-stamping is the sanctioned remedy and it belongs to deploying, not to
# remembering: report-only failures (unreadable file, no workspace yet) must not
# abort an otherwise-good deploy, so its exit code is advisory.
# The same verb re-projects each finished cycle's ledger onto the synced record
# shape — which is why it rewrites far more than configs, and why the line below
# says "data" rather than "configs". It is not housekeeping: a record shape that
# moved a field takes `resume` with it until the ledger carrying that field is
# migrated. A cycle with a live producer is skipped, so a deploy landing mid-run
# leaves that cycle's ledger untouched and picks it up on the next one.
say "data: re-stamp configs + compact cycle ledgers onto the synced schema"
if ! ( cd "$INSTALL_DIR" && .venv/bin/python -m promptpotter restamp --apply ); then
  bad "re-stamp reported skips/failures (above) — deploy continues; check them"
fi
# Brand rides every update: a sync overwrites tracked files, and the webapp bakes
# NEXT_PUBLIC_* in at build time — so skipping this would silently repaint the
# install upstream at the next deploy.
[[ -f "$INSTALL_DIR/.env" ]] && brand_write_env "$INSTALL_DIR/.env"
brand_export_webapp
[[ -d "$INSTALL_DIR/$WEBAPP_DIR" ]] && ( cd "$INSTALL_DIR/$WEBAPP_DIR" && npm install --silent && npm run build:deploy )
sudo systemctl restart "$SERVICE_NAME"

# --- backend (optional): sync → deps → restart ------------------------------
if [[ -n "$BACKEND_DIR" ]]; then
  say "backend: $BACKEND_DIR"
  sync "$BACKEND_DIR"
  [[ -x "$BACKEND_DIR/.venv/bin/pip" ]] && "$BACKEND_DIR/.venv/bin/pip" install -q -r "$BACKEND_DIR/requirements.txt"
  [[ -n "$BACKEND_SERVICE" ]] && sudo systemctl restart "$BACKEND_SERVICE"
fi

# --- health -----------------------------------------------------------------
say "health"
wait_healthy "http://127.0.0.1:$BIND_PORT$HEALTH_PATH" && ok "app up ($BIND_PORT)" || bad "app down — journalctl -u $SERVICE_NAME -e"
if [[ -n "$BACKEND_DIR" ]]; then
  wait_healthy "http://127.0.0.1:$BACKEND_PORT/status" && ok "backend up ($BACKEND_PORT)" || bad "backend down — journalctl -u ${BACKEND_SERVICE:-?} -e"
fi
