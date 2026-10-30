#!/usr/bin/env bash
# update.sh — the only command you run after a change. Idempotent: pulls latest,
# refreshes deps, rebuilds the webapp, and restarts the app (+ its backend, if
# configured). Force-matches origin, so it never stalls on a diverged box —
# tracked files mirror the remote; .env / runtime data are gitignored and survive.
#
#   ./update.sh            # the tracked branch's tip
#   ./update.sh v0.8.17    # a tag, branch or commit on origin — a release deploy names its tag
set -euo pipefail
REF="${1:-}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$HERE/deploy.config" ]] && source "$HERE/deploy.config"
APP_NAME="${APP_NAME:-myapp}"
SERVICE_NAME="${SERVICE_NAME:-$APP_NAME}"
BOT_SERVICE_NAME="${BOT_SERVICE_NAME:-$SERVICE_NAME-admin-bot}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/$APP_NAME/your-repo}"
WEBAPP_DIR="${WEBAPP_DIR:-webapp}"
BIND_PORT="${BIND_PORT:-8001}"
HEALTH_PATH="${HEALTH_PATH:-/api/v1/health}"
BACKEND_DIR="${BACKEND_DIR:-}"
BACKEND_SERVICE="${BACKEND_SERVICE:-}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
# The data root, which the unit carries as `Environment=PROMPTPOTTER_HOME` (install-service.sh) and
# every command run BY HAND from the checkout must be handed explicitly. Unset = the tree still
# lives under $INSTALL_DIR and the default resolves to it.
DATA_DIR="${DATA_DIR:-}"
# The file systemd actually loads — see deploy.config.example. Branding any other one repaints
# nothing.
ENV_FILE="${ENV_FILE:-$INSTALL_DIR/.env}"

# set_env_kv + the brand fan-out, shared with bootstrap.sh.
source "$HERE/brand-env.sh"
# wait_healthy — shared with every other script that starts something.
source "$HERE/health.sh"
source "$HERE/uv.sh"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[1;31m✗\033[0m %s\n' "$*"; }
# The commit $2 names on origin after the fetch; a branch name is origin's copy, never the box's.
resolve_ref() {
  if [[ -z "$2" ]]; then git -C "$1" rev-parse --verify --quiet '@{u}^{commit}'; return; fi
  git -C "$1" rev-parse --verify --quiet "refs/remotes/origin/$2^{commit}" \
    || git -C "$1" rev-parse --verify --quiet "refs/tags/$2^{commit}" \
    || git -C "$1" rev-parse --verify --quiet "$2^{commit}"
}
# Mirror ONE commit of origin and say which: a deploy that cannot name its commit cannot be checked.
sync() {
  local sha
  git -C "$1" fetch --quiet --tags origin
  sha="$(resolve_ref "$1" "$2")" || { bad "'$2' names nothing on origin — a tag, a branch or a commit"; exit 1; }
  git -C "$1" reset --hard --quiet "$sha"
  ok "$(basename "$1") at $(git -C "$1" describe --tags --always) ($(git -C "$1" rev-parse --short HEAD))"
}

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
self_sha() { sha256sum "$HERE/update.sh" 2>/dev/null | cut -d' ' -f1; }
self_before="$(self_sha)"
# The commit the live processes run, carried across the re-exec: the landing check rolls back to it.
landed_from="${UPDATE_LANDED_FROM:-$(git -C "$INSTALL_DIR" rev-parse HEAD)}"
sync "$INSTALL_DIR" "$REF"
# `sync` can replace THIS script, and bash keeps reading the descriptor it already opened — so the
# remainder of the run is the PRE-sync copy, and every fix to this file lands one deploy late while
# printing the old behaviour's output. Re-exec the new copy once, guarded against a loop.
if [[ -z "${UPDATE_REEXECED:-}" && -n "$self_before" && "$(self_sha)" != "$self_before" ]]; then
  ok "update.sh changed in this sync — re-running the new copy"
  export UPDATE_REEXECED=1 UPDATE_LANDED_FROM="$landed_from"
  exec bash "$HERE/update.sh" "$@"
fi
# Install the exact pinned graph from uv.lock — same source of truth as CI. Two syncs: the first
# `--inexact`, removing nothing, only so the synced code can run; the second exact, once the datasets
# this box offers (DATASET_OFFER) have named the extras their fetch needs.
ensure_uv
( cd "$INSTALL_DIR" && "$UV" sync --frozen --inexact --extra all )
# A run is its own process and outlives the restart (`KillMode=process`), so the landed code would
# read the ledger shapes an older producer still writes. The synced code asks, under the unit's
# environment as `health` below, and a refusal puts the checkout back on the commit they run.
if ! landing_out="$(sudo systemd-run --quiet --wait --pipe --collect --uid="$(id -un)" \
  -p EnvironmentFile="$ENV_FILE" -p WorkingDirectory="$INSTALL_DIR" \
  -E PROMPTPOTTER_HOME="${DATA_DIR:-$INSTALL_DIR/.promptpotter}" \
  "$INSTALL_DIR/.venv/bin/python" -m promptpotter machine-status)"; then
  printf '%s\n' "$landing_out"
  git -C "$INSTALL_DIR" reset --hard --quiet "$landed_from"
  bad "not landed: a live run is on other code — the checkout is back on $(git -C "$INSTALL_DIR" rev-parse --short HEAD); re-run once it pauses or ends"
  exit 1
fi
# The offer is read from the file the UNIT loads and handed in, as PROMPTPOTTER_HOME is below: this
# shell does not load $ENV_FILE. The verb refuses a name naming no dataset, and `set -e` stops the
# deploy there, before the exact sync drops what that name's extras were.
offer_out="$(cd "$INSTALL_DIR" && DATASET_OFFER="$(read_env_kv "$ENV_FILE" DATASET_OFFER)" .venv/bin/python -m promptpotter offer-extras)"
offer_extras=()
for extra in $offer_out; do offer_extras+=(--extra "$extra"); done
( cd "$INSTALL_DIR" && "$UV" sync --frozen --extra all "${offer_extras[@]}" )
ok "synced; offered datasets' extras: ${offer_extras[*]:-none}"
# A dep rebuild relabels new .venv files user_home_t; re-apply the persisted
# bin_t rule (set by install-service.sh) so systemd can still exec them under
# SELinux enforcing — otherwise the service silently 203/EXEC crash-loops.
if command -v getenforce >/dev/null 2>&1 && [[ "$(getenforce)" == "Enforcing" ]]; then
  sudo restorecon -RF "$INSTALL_DIR/.venv/bin"
fi
# Brand rides every update: a sync overwrites tracked files, and the webapp bakes
# NEXT_PUBLIC_* in at build time — so skipping this would silently repaint the
# install upstream at the next deploy.
[[ -f "$ENV_FILE" ]] && brand_write_env "$ENV_FILE"
brand_export_webapp
[[ -d "$INSTALL_DIR/$WEBAPP_DIR" ]] && ( cd "$INSTALL_DIR/$WEBAPP_DIR" && npm ci --silent && npm run build )
sudo systemctl restart "$SERVICE_NAME"
# The admin bot is a SEPARATE unit running the SAME checkout, so a sync that touched its code
# leaves it on the old one until something restarts it — and nothing did. Silent, because a bot
# still answering /blocked looks healthy. Skip where it was never installed.
systemctl cat "$BOT_SERVICE_NAME.service" >/dev/null 2>&1 && sudo systemctl restart "$BOT_SERVICE_NAME"

# --- backend (optional): sync → deps → restart ------------------------------
if [[ -n "$BACKEND_DIR" ]]; then
  say "backend: $BACKEND_DIR"
  sync "$BACKEND_DIR" ""   # REF names a commit of THIS repo; the backend rides its own tracked tip
  [[ -x "$BACKEND_DIR/.venv/bin/pip" ]] && "$BACKEND_DIR/.venv/bin/pip" install -q -r "$BACKEND_DIR/requirements.txt"
  [[ -n "$BACKEND_SERVICE" ]] && sudo systemctl restart "$BACKEND_SERVICE"
fi

# --- health -----------------------------------------------------------------
# These decide the EXIT CODE, and they are the only things that do. `bad` only prints, so
# until this counter existed the script ended 0 while announcing `✗ app down` — a deploy that left
# the service dead reported success to whoever ran it and to anything reading `$?`.
say "health"
down=0
if wait_healthy "http://127.0.0.1:$BIND_PORT$HEALTH_PATH"; then ok "app up ($BIND_PORT)"; else bad "app down — journalctl -u $SERVICE_NAME -e"; down=1; fi
# The bot has no port to probe — outbound-only is the charter — so `is-active` IS its health.
if systemctl cat "$BOT_SERVICE_NAME.service" >/dev/null 2>&1; then
  if systemctl is-active --quiet "$BOT_SERVICE_NAME"; then ok "admin bot up"; else bad "admin bot down — journalctl -u $BOT_SERVICE_NAME -e"; down=1; fi
fi
# Everything the box answers for that /health cannot see (a backend, a sign-in provider's view of
# oidc.json): the verb exits 1 on a broken one, and records and tells it as the bot would. It runs
# under the unit's EnvironmentFile: this shell holds neither its keys nor its bot token, so a probe
# run here would judge whatever the checkout's .env happens to hold instead of what the app serves.
health_out="$(sudo systemd-run --quiet --wait --pipe --collect --uid="$(id -un)" \
  -p EnvironmentFile="$ENV_FILE" -p WorkingDirectory="$INSTALL_DIR" \
  -E PROMPTPOTTER_HOME="${DATA_DIR:-$INSTALL_DIR/.promptpotter}" \
  "$INSTALL_DIR/.venv/bin/python" -m promptpotter health)" || down=1
while read -r state row; do
  if [[ "$state" == held ]]; then ok "$row"; elif [[ -n "$state" ]]; then bad "$row"; fi
done <<< "$health_out"
if [[ -n "$BACKEND_DIR" ]]; then
  if wait_healthy "http://127.0.0.1:$BACKEND_PORT/status"; then ok "backend up ($BACKEND_PORT)"; else bad "backend down — journalctl -u ${BACKEND_SERVICE:-?} -e"; down=1; fi
fi
exit "$down"
